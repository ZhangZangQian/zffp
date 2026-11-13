"""纪律闸门单测：每一种"手滑"都必须被拦住。"""
from __future__ import annotations

import pytest

from zffp import db, discipline


@pytest.fixture(autouse=True)
def clean_db():
    db.init()
    for t in ("bars", "stocks", "signals", "plans", "trades", "reviews",
              "daily_log", "settings"):
        db.x(f"DELETE FROM {t}")
    # 造 9 个交易日，最新为 2026-06-09
    for i in range(9):
        d = f"2026-06-{i+1:02d}"
        db.x("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
             ("600000", d, 10.0, 10.2, 9.9, 10.0, 100000))
    db.save_params({}, {})
    yield


def make_signal(**kw) -> int:
    row = {
        "code": "600000", "name": "示例股", "board": "main",
        "t_date": "2026-06-05", "t_open": 9.80, "t_close": 10.78,
        "t1_date": "2026-06-06", "t1_open": 11.30, "t1_high": 11.60,
        "t1_low": 10.90, "t1_close": 11.00, "t1_gap": 0.048, "t1_body": 0.026,
        "breakout_date": "2026-06-09", "breakout_close": 11.00,
        "breakout_vol_ratio": 2.1, "buy_date": "2026-06-10", "ref_price": 11.20,
        "state": "triggered", "days_left": 0,
    }
    row.update(kw)
    cols = ",".join(row)
    marks = ",".join("?" * len(row))
    return db.x(f"INSERT INTO signals({cols}) VALUES({marks})", list(row.values()))


def plan_payload(sid: int, **kw) -> dict:
    p = {
        "signal_id": sid,
        "buy_date": "2026-06-10",
        "buy_price_limit": 11.00,
        "stop_price": 10.50,
        "target_price": 12.20,
        "thesis": "T日一字涨停后巨阴洗盘，缩量回踩不破T日开盘，今日放量涨停突破阴线高点，属于标准接力位。",
        "emotion": "平静",
    }
    p.update(kw)
    return p


def codes(res: dict) -> set[str]:
    return {b["code"] for b in res["blocks"]}


def test_valid_plan_passes():
    sid = make_signal()
    res = discipline.evaluate_plan(plan_payload(sid))
    assert res["ok"], res["blocks"]
    assert res["calc"]["shares"] > 0
    assert res["calc"]["rr"] >= 2.0
    assert res["calc"]["shares"] % 100 == 0


def test_missing_signal_blocked():
    res = discipline.evaluate_plan(plan_payload(9999))
    assert not res["ok"]
    assert "SIGNAL_MISSING" in codes(res)


def test_signal_not_triggered_blocked():
    sid = make_signal(state="waiting", breakout_date=None, buy_date=None)
    res = discipline.evaluate_plan(plan_payload(sid, buy_date="2026-06-10"))
    assert "SIGNAL_NOT_TRIGGERED" in codes(res)


def test_short_thesis_blocked():
    sid = make_signal()
    res = discipline.evaluate_plan(plan_payload(sid, thesis="感觉要涨"))
    assert "THESIS_SHORT" in codes(res)


def test_chasing_blocked():
    sid = make_signal()
    # 突破日收盘 11.00，追到 12.00 = +9%，超过 8% 上限
    res = discipline.evaluate_plan(plan_payload(sid, buy_price_limit=12.00))
    assert "CHASE" in codes(res)


def test_no_stop_blocked():
    sid = make_signal()
    res = discipline.evaluate_plan(plan_payload(sid, stop_price=0))
    assert "NO_STOP" in codes(res)


def test_stop_too_far_blocked():
    sid = make_signal()
    # 止损 9.90，距 11.00 有 10%，超过 8% 上限
    res = discipline.evaluate_plan(plan_payload(sid, stop_price=9.90, target_price=12.50))
    assert "STOP_TOO_FAR" in codes(res)


def test_low_rr_blocked():
    sid = make_signal()
    res = discipline.evaluate_plan(plan_payload(sid, target_price=11.40))
    assert "RR_LOW" in codes(res)


def test_wrong_buy_date_blocked():
    sid = make_signal()
    res = discipline.evaluate_plan(plan_payload(sid, buy_date="2026-06-12"))
    assert "BUY_DATE_MISMATCH" in codes(res)


def _create_plan(sid: int, **kw) -> dict:
    res = discipline.evaluate_plan(plan_payload(sid, **kw))
    assert res["ok"], res["blocks"]
    p = plan_payload(sid, **kw)
    calc = res["calc"]
    pid = db.x(
        "INSERT INTO plans(signal_id,code,name,plan_date,buy_date,buy_price_limit,ref_price,"
        "shares,amount,position_pct,stop_price,target_price,risk_amount,rr,thesis,emotion,"
        "status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "600000", "示例股", "2026-06-09", p["buy_date"], p["buy_price_limit"],
         p["ref_price"] if "ref_price" in p else 11.20,
         calc["shares"], calc["amount"], calc["position_pct"], p["stop_price"],
         p["target_price"], calc["risk_amount"], calc["rr"], p["thesis"], p["emotion"],
         "locked", "2026-06-09T20:00:00"))
    return dict(db.q1("SELECT * FROM plans WHERE id=?", (pid,)))


def test_trade_without_plan_blocked():
    res = discipline.evaluate_trade({"plan_id": None, "buy_price": 11.0, "shares": 100})
    assert not res["ok"]
    assert "NO_PLAN" in {b["code"] for b in res["blocks"]}


def test_trade_over_price_blocked():
    sid = make_signal()
    plan = _create_plan(sid)
    res = discipline.evaluate_trade({"plan_id": plan["id"], "buy_date": "2026-06-10",
                                     "buy_price": 11.60, "shares": plan["shares"]})
    assert "OVER_PRICE" in {b["code"] for b in res["blocks"]}


def test_trade_oversize_blocked():
    sid = make_signal()
    plan = _create_plan(sid)
    res = discipline.evaluate_trade({"plan_id": plan["id"], "buy_date": "2026-06-10",
                                     "buy_price": 11.00, "shares": int(plan["shares"]) + 100})
    assert "OVERSIZE" in {b["code"] for b in res["blocks"]}


def test_trade_on_wrong_day_blocked():
    sid = make_signal()
    plan = _create_plan(sid)
    res = discipline.evaluate_trade({"plan_id": plan["id"], "buy_date": "2026-06-12",
                                     "buy_price": 11.00, "shares": plan["shares"]})
    assert "DATE_MISMATCH" in {b["code"] for b in res["blocks"]}


def test_trade_ok():
    sid = make_signal()
    plan = _create_plan(sid)
    res = discipline.evaluate_trade({"plan_id": plan["id"], "buy_date": "2026-06-10",
                                     "buy_price": 11.00, "shares": plan["shares"]})
    assert res["ok"], res["blocks"]


def test_cooldown_after_three_losses():
    # 造 3 笔连续亏损，最后一笔今天（2026-06-09）平仓
    for i, (bd, sd) in enumerate([("2026-06-04", "2026-06-05"),
                                  ("2026-06-05", "2026-06-08"),
                                  ("2026-06-08", "2026-06-09")]):
        db.x("INSERT INTO trades(code,name,buy_date,sell_date,buy_price,shares,amount,"
             "pnl,status,stop_price,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
             ("60000%d" % i, "亏损股", bd, sd,
              10.0, 1000, 10000, -500.0, "closed", 9.5, "2026-06-05T10:00:00"))
    snap = discipline.snapshot()
    assert snap["loss_streak"] == 3
    assert snap["in_cooldown"]

    sid = make_signal()
    res = discipline.evaluate_plan(plan_payload(sid))
    assert "COOLDOWN" in codes(res)


def test_position_cap():
    for i in range(3):
        db.x("INSERT INTO trades(code,name,buy_date,buy_price,shares,amount,status,"
             "stop_price,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
             ("60010%d" % i, "持仓股", "2026-06-08", 10.0, 1000, 10000, "open", 9.5,
              "2026-06-08T10:00:00"))
    sid = make_signal()
    res = discipline.evaluate_plan(plan_payload(sid))
    assert "POSITION_CAP" in codes(res)
