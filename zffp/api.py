"""HTTP 接口层。前端只做展示，所有判断都在后端。"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, db, discipline, scanner
from .config import (EMOTIONS, INDICATOR_PARAMS, MISTAKE_TAGS, kdj_label,
                     macd_label, rsi_label)
from .datafeed import import_csv, update_market

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

app = FastAPI(title="巨阴突破战法 · 复盘纪律系统", version="1.0.0")

# --------------------------------------------------------------------------
# 后台任务（行情更新）
# --------------------------------------------------------------------------
JOB: dict[str, Any] = {"running": False, "message": "", "total": 0, "done": 0,
                       "ok": 0, "fail": 0, "started_at": None, "elapsed": 0}
_JOB_LOCK = threading.Lock()


def _run_update(mode: str, limit: int | None, workers: int):
    with _JOB_LOCK:
        JOB.update({"running": True, "total": 0, "done": 0, "ok": 0, "fail": 0,
                    "message": "启动…", "started_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed": 0})
    try:
        update_market(JOB, mode=mode, limit=limit, workers=workers)
    except Exception as e:  # pragma: no cover
        JOB["message"] = f"失败：{e}"
    finally:
        JOB["running"] = False


@app.get("/api/health")
def health():
    return {"ok": True, "db": str(db.DB_PATH), "trade_date": db.completed_trade_date()}


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    s, r = db.load_params()
    return {"strategy": s, "risk": r, "mistake_tags": MISTAKE_TAGS,
            "emotions": EMOTIONS, "last_update": db.get_setting("last_update")}


@app.put("/api/settings")
def put_settings(payload: dict = Body(...)):
    s, r = db.load_params()
    s.update(payload.get("strategy") or {})
    r.update(payload.get("risk") or {})
    db.save_params(s, r)
    return {"ok": True, "strategy": db.load_params()[0], "risk": db.load_params()[1]}


# --------------------------------------------------------------------------
# 行情
# --------------------------------------------------------------------------
@app.post("/api/data/update")
def data_update(payload: dict = Body(default={})):
    if JOB["running"]:
        return {"ok": False, "message": "已有任务在跑", "job": JOB}
    mode = payload.get("mode", "incremental")
    limit = payload.get("limit")
    workers = int(payload.get("workers", 8))
    threading.Thread(target=_run_update, args=(mode, limit, workers), daemon=True).start()
    return {"ok": True, "message": "已开始更新", "job": JOB}


@app.get("/api/data/status")
def data_status():
    have, total = db.bar_coverage()
    return {"job": JOB, "coverage": {"with_bars": have, "stocks": total},
            "trade_date": db.completed_trade_date(),
            "last_update": db.get_setting("last_update")}


@app.post("/api/scan")
def run_scan(payload: dict = Body(default={})):
    return {"ok": True, **scanner.scan(lookback=int(payload.get("lookback", 60)))}


# --------------------------------------------------------------------------
# 信号池
# --------------------------------------------------------------------------
@app.get("/api/signals")
def signals():
    return scanner.pool()


# --------------------------------------------------------------------------
# 行情（K 线图数据）
# --------------------------------------------------------------------------
def _ema(vals: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    out: list[float] = []
    e = None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def _sma_cn(vals: list[float], n: int, m: int = 1) -> list[float]:
    """通达信 SMA(X,N,M): Y = (M*X + (N-M)*Y') / N"""
    out: list[float] = []
    y = None
    for v in vals:
        y = v if y is None else (m * v + (n - m) * y) / n
        out.append(y)
    return out


def _indicators(bars) -> dict:
    """MACD / KDJ / RSI，全部按通达信口径，参数见 config.INDICATOR_PARAMS。"""
    mp = INDICATOR_PARAMS["macd"]
    kp = INDICATOR_PARAMS["kdj"]
    rp = list(INDICATOR_PARAMS["rsi"])

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    dif = [a - b for a, b in zip(_ema(closes, mp["fast"]), _ema(closes, mp["slow"]))]
    dea = _ema(dif, mp["signal"])
    macd = [2 * (a - b) for a, b in zip(dif, dea)]

    ks: list[float] = []
    ds: list[float] = []
    js: list[float] = []
    k_prev = d_prev = 50.0
    n = int(kp["n"])
    for i, b in enumerate(bars):
        lo = min(lows[max(0, i - (n - 1)):i + 1])
        hi = max(highs[max(0, i - (n - 1)):i + 1])
        rsv = (b.close - lo) / (hi - lo) * 100 if hi > lo else 50.0
        k = (rsv + (kp["m1"] - 1) * k_prev) / kp["m1"]
        d = (k + (kp["m2"] - 1) * d_prev) / kp["m2"]
        ks.append(k)
        ds.append(d)
        js.append(3 * k - 2 * d)
        k_prev, d_prev = k, d

    rsi: dict[int, list[float]] = {}
    ups: list[float] = []
    downs: list[float] = []
    for i, b in enumerate(bars):
        base = bars[i - 1].close if i else b.open
        ch = b.close - base
        ups.append(max(ch, 0.0))
        downs.append(abs(ch))
    for period in rp:
        su = _sma_cn(ups, period)
        sd = _sma_cn(downs, period)
        rsi[period] = [100 * u / (u + d) if (u + d) > 0 else 50.0 for u, d in zip(su, sd)]

    out = {
        "dif": [round(v, 3) for v in dif],
        "dea": [round(v, 3) for v in dea],
        "macd": [round(v, 3) for v in macd],
        "k": [round(v, 2) for v in ks],
        "d": [round(v, 2) for v in ds],
        "j": [round(v, 2) for v in js],
    }
    for period in rp:
        out[f"rsi{period}"] = [round(v, 2) for v in rsi[period]]
    out["labels"] = {"macd": macd_label(), "kdj": kdj_label(), "rsi": rsi_label()}
    out["rsi_periods"] = rp
    return out


@app.get("/api/kline")
def kline(code: str = "", days: int = 90):
    """返回某只股票的日线序列 + 均线 + 涨停标记 + MACD/KDJ/RSI + 最新报价。"""
    from statistics import mean

    from .strategy import is_limit_up, limit_ratio

    code = (code or "").strip()[-6:]
    if not code:
        raise HTTPException(400, "缺少股票代码")
    row = db.q1("SELECT name FROM stocks WHERE code=?", (code,))
    name = row["name"] if row else code
    days = max(30, min(int(days or 90), 300))
    bars = db.get_bars(code, days + 60)      # 多取 60 根用于预热均线和指标
    if not bars:
        raise HTTPException(404, f"{code} 没有本地行情，请先更新数据")

    _, risk = db.load_params()
    ratio = limit_ratio(code, name)
    closes = [b.close for b in bars]
    ind = _indicators(bars)

    def ma(i: int, n: int):
        if i + 1 < n:
            return None
        return round(mean(closes[i + 1 - n:i + 1]), 3)

    full = []
    rsi_periods = ind["rsi_periods"]
    for i, b in enumerate(bars):
        prev = bars[i - 1] if i > 0 else None
        pct = (b.close / prev.close - 1) * 100 if prev and prev.close else 0.0
        rec = {
            "date": b.date, "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
            "pct": round(pct, 2),
            "ma5": ma(i, 5), "ma10": ma(i, 10), "ma20": ma(i, 20),
            "dif": ind["dif"][i], "dea": ind["dea"][i], "macd": ind["macd"][i],
            "k": ind["k"][i], "d": ind["d"][i], "j": ind["j"][i],
            "limit_up": bool(prev and is_limit_up(prev.close, b, ratio, 0.005)),
        }
        for period in rsi_periods:
            rec[f"rsi{period}"] = ind[f"rsi{period}"][i]
        full.append(rec)

    out = full[-days:]
    last = out[-1]
    prev_close = out[-2]["close"] if len(out) > 1 else last["open"]
    return {
        "code": code,
        "name": name,
        "limit_ratio": ratio,
        "bars": out,
        "labels": ind["labels"],
        "rsi_periods": rsi_periods,
        "quote": {
            "date": last["date"], "price": last["close"], "prev_close": prev_close,
            "pct": round((last["close"] / prev_close - 1) * 100, 2) if prev_close else 0,
            "open": last["open"], "high": last["high"], "low": last["low"],
            "volume": last["volume"],
            "high_52": max(b["high"] for b in out),
            "low_52": min(b["low"] for b in out),
        },
        "levels": {"max_stop_pct": risk.get("max_stop_pct", 0.08)},
    }


@app.get("/api/search")
def search(q: str = "", limit: int = 8):
    q = (q or "").strip()
    if not q:
        return {"items": []}
    rows = db.q("SELECT code,name FROM stocks WHERE code LIKE ? OR name LIKE ? "
                "ORDER BY code LIMIT ?", (f"%{q}%", f"%{q}%", max(1, min(limit, 30))))
    return {"items": [dict(r) for r in rows]}


# --------------------------------------------------------------------------
# 计划
# --------------------------------------------------------------------------
@app.get("/api/plans")
def list_plans(status: str = ""):
    sql = "SELECT * FROM plans"
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT 200"
    return {"plans": [dict(r) for r in db.q(sql, args)]}


@app.post("/api/plans/preview")
def preview_plan(payload: dict = Body(...)):
    res = discipline.evaluate_plan(payload)
    sig = res.get("signal")
    if sig:
        res["suggest"] = discipline.suggest(sig, payload.get("buy_price_limit"))
    return res


@app.post("/api/plans")
def create_plan(payload: dict = Body(...)):
    res = discipline.evaluate_plan(payload)
    if not res["ok"]:
        return JSONResponse(status_code=422, content={"ok": False, **res})

    sig = res["signal"]
    calc = res["calc"]
    now = datetime.now().isoformat(timespec="seconds")
    risk_amount = calc.get("risk_amount") or 0
    buy_date = payload.get("buy_date") or res.get("expected_buy_date")
    pid = db.x(
        "INSERT INTO plans(signal_id,code,name,plan_date,buy_date,buy_price_limit,ref_price,"
        "shares,amount,position_pct,stop_price,target_price,risk_amount,rr,thesis,emotion,"
        "status,gate_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sig["id"] if sig else None, payload.get("code") or (sig["code"] if sig else ""),
         payload.get("name") or (sig["name"] if sig else ""),
         res["trade_date"], buy_date, float(payload["buy_price_limit"]),
         sig["breakout_close"] if sig else None,
         calc.get("shares"), calc.get("amount"), calc.get("position_pct"),
         float(payload["stop_price"]), float(payload["target_price"]),
         risk_amount, calc.get("rr"),
         (payload.get("thesis") or "").strip(), payload.get("emotion") or "",
         "locked",
         json.dumps({"blocks": res["blocks"], "warns": res["warns"]}, ensure_ascii=False),
         now))
    return {"ok": True, "plan_id": pid, "plan": dict(db.q1("SELECT * FROM plans WHERE id=?", (pid,)))}


@app.post("/api/plans/{plan_id}/cancel")
def cancel_plan(plan_id: int):
    row = db.q1("SELECT * FROM plans WHERE id=?", (plan_id,))
    if not row:
        raise HTTPException(404, "计划不存在")
    if row["status"] != "locked":
        raise HTTPException(400, f"当前状态 {row['status']}，不能作废")
    db.x("UPDATE plans SET status='cancelled' WHERE id=?", (plan_id,))
    return {"ok": True}


# --------------------------------------------------------------------------
# 成交
# --------------------------------------------------------------------------
@app.get("/api/trades")
def list_trades(status: str = ""):
    sql = "SELECT * FROM trades"
    args: list = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT 300"
    out = []
    for r in db.q(sql, args):
        d = dict(r)
        try:
            d["violations"] = json.loads(d["violations"] or "[]")
        except Exception:
            d["violations"] = []
        out.append(d)
    return {"trades": out}


@app.get("/api/trades/{trade_id}")
def get_trade(trade_id: int):
    d = analytics.trade_detail(trade_id)
    if not d:
        raise HTTPException(404, "交易不存在")
    return d


@app.post("/api/trades/preview")
def preview_trade(payload: dict = Body(...)):
    return discipline.evaluate_trade(payload)


@app.post("/api/trades")
def create_trade(payload: dict = Body(...)):
    res = discipline.evaluate_trade(payload)
    if not res["ok"]:
        return JSONResponse(status_code=422, content={"ok": False, **res})

    plan = res["plan"]
    now = datetime.now().isoformat(timespec="seconds")
    buy_price = float(payload["buy_price"])
    shares = int(payload["shares"])
    amount = round(buy_price * shares, 2)
    violations = []
    if payload.get("backfill"):
        violations.append("BACKFILL")
    for w in res["warns"]:
        if w["code"] in ("GAP_UP",):
            violations.append(w["code"])

    tid = db.x(
        "INSERT INTO trades(plan_id,signal_id,code,name,buy_date,buy_time,buy_price,shares,"
        "amount,stop_price,target_price,status,compliance,violations,note,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (plan["id"], plan["signal_id"], plan["code"], plan["name"],
         payload.get("buy_date") or plan["buy_date"],
         payload.get("buy_time") or "",
         buy_price, shares, amount,
         float(plan["stop_price"]), float(plan["target_price"]),
         "open", "ok", json.dumps(violations, ensure_ascii=False),
         (payload.get("note") or "").strip(), now))
    db.x("UPDATE plans SET status='executed' WHERE id=?", (plan["id"],))
    return {"ok": True, "trade_id": tid}


@app.post("/api/trades/{trade_id}/close")
def close_trade(trade_id: int, payload: dict = Body(...)):
    row = db.q1("SELECT * FROM trades WHERE id=?", (trade_id,))
    if not row:
        raise HTTPException(404, "交易不存在")
    if row["status"] != "open":
        raise HTTPException(400, "该笔已平仓")

    trade = dict(row)
    res = discipline.evaluate_close(trade, payload)
    if not res["ok"]:
        return JSONResponse(status_code=422, content={"ok": False, **res})

    price = float(payload["sell_price"])
    sell_date = payload.get("sell_date") or discipline.trade_date()
    pnl = round((price - trade["buy_price"]) * trade["shares"], 2)
    pnl_pct = round((price / trade["buy_price"] - 1) * 100, 2)
    hold = discipline.trading_days_between(trade["buy_date"], sell_date) or 1

    violations = []
    try:
        violations = json.loads(trade["violations"] or "[]")
    except Exception:
        pass
    violations += res["violations"]
    compliance = "ok"
    real = [v for v in violations if not v.startswith("BACKFILL")]
    if len(real) == 1:
        compliance = "partial"
    elif len(real) > 1:
        compliance = "violation"

    db.x("UPDATE trades SET sell_date=?, sell_price=?, pnl=?, pnl_pct=?, exit_reason=?, "
         "hold_days=?, status='closed', compliance=?, violations=? WHERE id=?",
         (sell_date, price, pnl, pnl_pct, payload.get("exit_reason", ""), hold,
          compliance, json.dumps(violations, ensure_ascii=False), trade_id))
    return {"ok": True, "pnl": pnl, "pnl_pct": pnl_pct, "compliance": compliance,
            "warns": res["warns"], "need_review": True}


# --------------------------------------------------------------------------
# 复盘
# --------------------------------------------------------------------------
@app.get("/api/reviews/pending")
def pending_reviews():
    rows = db.q("SELECT * FROM trades WHERE status='closed' AND id NOT IN "
                "(SELECT trade_id FROM reviews) ORDER BY sell_date DESC")
    return {"pending": [dict(r) for r in rows]}


@app.post("/api/reviews")
def create_review(payload: dict = Body(...)):
    tid = payload.get("trade_id")
    t = db.q1("SELECT * FROM trades WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404, "交易不存在")
    lesson = (payload.get("lesson") or "").strip()
    followed = payload.get("followed_plan") or "部分"
    if len(lesson) < 8:
        return JSONResponse(status_code=422, content={
            "ok": False, "blocks": [{"code": "LESSON_SHORT", "title": "教训写得太短",
                                     "detail": f"现在 {len(lesson)} 个字。",
                                     "fix": "写下一次遇到同样形态，你具体会怎么做。"}]})

    try:
        violations = json.loads(t["violations"] or "[]")
    except Exception:
        violations = []
    score = discipline.review_score(followed, violations)
    tags = payload.get("mistake_tags") or []
    now = datetime.now().isoformat(timespec="seconds")

    db.x("INSERT INTO reviews(trade_id,review_date,followed_plan,exit_quality,emotion,"
         "mistake_tags,lesson,score,created_at) VALUES(?,?,?,?,?,?,?,?,?) "
         "ON CONFLICT(trade_id) DO UPDATE SET review_date=excluded.review_date,"
         "followed_plan=excluded.followed_plan,exit_quality=excluded.exit_quality,"
         "emotion=excluded.emotion,mistake_tags=excluded.mistake_tags,"
         "lesson=excluded.lesson,score=excluded.score",
         (tid, discipline.trade_date(), followed, int(payload.get("exit_quality") or 3),
          payload.get("emotion") or "", json.dumps(tags, ensure_ascii=False), lesson,
          score, now))
    return {"ok": True, "score": score}


# --------------------------------------------------------------------------
# 总览
# --------------------------------------------------------------------------
def _position_view() -> list[dict]:
    out = []
    today = db.completed_trade_date()
    for r in db.q("SELECT * FROM trades WHERE status='open' ORDER BY buy_date DESC"):
        t = dict(r)
        bars = db.q("SELECT date,high,low,close FROM bars WHERE code=? AND date>=? "
                    "ORDER BY date", (t["code"], t["buy_date"]))
        last = bars[-1]["close"] if bars else t["buy_price"]
        stop = float(t["stop_price"] or 0)
        pnl_pct = (last / t["buy_price"] - 1) * 100 if t["buy_price"] else 0
        held = discipline.trading_days_between(t["buy_date"], today) if today else 0
        broke = any(b["low"] <= stop for b in bars) if stop else False
        state, hint = "持有中", ""
        if stop and last <= stop:
            state, hint = "已破止损", "收盘已在止损下方，明天必须处理"
        elif broke:
            state, hint = "盘中破止损", "已经触及止损，别再等"
        elif t["target_price"] and last >= t["target_price"]:
            state, hint = "已达目标", "按计划止盈，或上移止损"
        elif held >= 5:
            state, hint = "持有超期", f"已持有 {held} 个交易日，战法要求快进快出"
        out.append({**t, "last": round(last, 2), "pnl_pct": round(pnl_pct, 2),
                    "unrealized": round((last - t["buy_price"]) * t["shares"], 2),
                    "held": held, "state": state, "hint": hint,
                    "to_stop_pct": round((last / stop - 1) * 100, 2) if stop else None,
                    "progress": round((last - t["buy_price"]) /
                                      (t["target_price"] - t["buy_price"]) * 100, 1)
                    if t["target_price"] and t["target_price"] > t["buy_price"] else 0})
    return out


def _flow() -> list[dict]:
    """今日流程：由数据自动核验，不靠自觉打勾。"""
    today = db.completed_trade_date()
    lu = db.get_setting("last_update") or {}
    data_done = bool(today and (lu.get("trade_date") or "") >= today)
    scan_row = db.q1("SELECT MAX(updated_at) AS t FROM signals")
    scan_done = bool(today and scan_row and scan_row["t"] and scan_row["t"][:10] >= today)
    nxt = discipline.next_trading_day(today) if today else None
    plan_n = db.q1("SELECT COUNT(*) AS n FROM plans WHERE buy_date=? AND status='locked'",
                   (nxt,))["n"] if nxt else 0
    pending = db.q1("SELECT COUNT(*) AS n FROM trades WHERE status='closed' AND id NOT IN "
                    "(SELECT trade_id FROM reviews)")["n"]
    return [
        {"key": "data", "label": "更新行情", "done": data_done,
         "hint": "点右上角「更新行情」，把今天的数据拉下来" if not data_done else "已是最新"},
        {"key": "scan", "label": "跑战法扫描", "done": scan_done,
         "hint": "扫描后才知道今天有没有信号" if not scan_done else "已扫描"},
        {"key": "plan", "label": f"写明日计划（{nxt or '-'}）", "done": plan_n > 0,
         "hint": "今晚写下明天买什么、买多少、错了在哪走" if not plan_n else f"{plan_n} 个计划待执行"},
        {"key": "review", "label": "完成今日复盘", "done": pending == 0,
         "hint": f"还有 {pending} 笔未复盘" if pending else "全部已复盘"},
    ]


@app.get("/api/overview")
def overview():
    s, r = db.load_params()
    pool = scanner.pool()
    today = db.completed_trade_date()
    plan_nxt = discipline.next_trading_day(today) if today else None
    return {
        "trade_date": today,
        "next_trade_date": plan_nxt,
        "params": {"strategy": s, "risk": r},
        "snapshot": discipline.snapshot(),
        "streak": discipline.streak(),
        "daily_score": discipline.daily_score(today) if today else {"score": 100, "events": []},
        "flow": _flow(),
        "actionable": pool["actionable"],
        "missed": pool["missed"][:12],
        "missed_count": pool["counts"]["missed"],
        "waiting_count": pool["counts"]["waiting"],
        "triggered_count": pool["counts"]["triggered"],
        "positions": _position_view(),
        "pending_reviews": [dict(x) for x in db.q(
            "SELECT * FROM trades WHERE status='closed' AND id NOT IN "
            "(SELECT trade_id FROM reviews) ORDER BY sell_date DESC")],
        "plans_today": [dict(x) for x in db.q(
            "SELECT * FROM plans WHERE status='locked' ORDER BY id DESC LIMIT 10")],
        "data": {"trade_date": today,
                 "coverage": db.bar_coverage(),
                 "intraday": db.is_intraday(),
                 "last_update": db.get_setting("last_update")},
        "mistake_tags": MISTAKE_TAGS,
        "emotions": EMOTIONS,
    }


@app.get("/api/stats")
def stats():
    return analytics.overview()


@app.get("/api/strategy/health")
def strategy_health():
    return analytics.strategy_check()


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------
@app.post("/api/import/csv")
def import_csv_api(payload: dict = Body(...)):
    path = payload.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(400, "路径不存在")
    n = import_csv(path, payload.get("code"))
    return {"ok": True, "bars": n}


@app.post("/api/data/seed")
def seed_demo():
    from .seed import seed
    return {"ok": True, **seed()}


# 静态前端（必须最后挂载）
if WEB.exists():
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
