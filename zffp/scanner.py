"""扫描器：把本地行情跑一遍战法，产出信号池。

设计：数据更新与扫描分离。
    update  -> 慢（网络），一天一次
    scan    -> 快（纯本地），可以随时跑
"""
from __future__ import annotations

import json
from datetime import datetime

from . import db
from .strategy import Bar, scan_stock


def _load_bars(limit_days: int = 100) -> dict[str, list[Bar]]:
    end = db.completed_trade_date()
    if not end:
        return {}
    rows = db.q("SELECT DISTINCT date FROM bars WHERE date<=? ORDER BY date DESC LIMIT ?",
                (end, limit_days))
    if not rows:
        return {}
    since = rows[-1]["date"]
    out: dict[str, list[Bar]] = {}
    for r in db.q("SELECT code,date,open,high,low,close,volume FROM bars "
                  "WHERE date>=? AND date<=? ORDER BY code,date", (since, end)):
        out.setdefault(r["code"], []).append(
            Bar(r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]))
    return out


def scan(lookback: int = 60) -> dict:
    strategy, _ = db.load_params()
    boards = set(strategy.get("boards") or ["main"])
    exclude_st = bool(strategy.get("exclude_st", True))

    stocks = {r["code"]: r["name"] for r in db.q("SELECT code,name FROM stocks")}
    bars_map = _load_bars(lookback + 30)

    found: list[dict] = []
    scanned = 0
    for code, bars in bars_map.items():
        name = stocks.get(code, code)
        if len(bars) < 12:
            continue
        if "退" in name:
            continue
        ref = next((b for b in reversed(bars) if b.volume > 0), None)
        if not ref:
            continue
        if exclude_st and "ST" in name.upper():
            continue
        scanned += 1
        for rec in scan_stock(code, name, bars, strategy, lookback=lookback):
            if rec["board"] not in boards:
                continue
            found.append(rec)

    now = datetime.now().isoformat(timespec="seconds")

    sql = """
    INSERT INTO signals (code,name,board,t_date,t_open,t_close,t1_date,t1_open,t1_high,
                         t1_low,t1_close,t1_gap,t1_body,breakout_date,breakout_close,
                         breakout_vol_ratio,buy_date,ref_price,state,days_left,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(code,t_date) DO UPDATE SET
        name=excluded.name, board=excluded.board,
        t_open=excluded.t_open, t_close=excluded.t_close,
        t1_date=excluded.t1_date, t1_open=excluded.t1_open, t1_high=excluded.t1_high,
        t1_low=excluded.t1_low, t1_close=excluded.t1_close,
        t1_gap=excluded.t1_gap, t1_body=excluded.t1_body,
        breakout_date=excluded.breakout_date, breakout_close=excluded.breakout_close,
        breakout_vol_ratio=excluded.breakout_vol_ratio,
        buy_date=excluded.buy_date, ref_price=excluded.ref_price,
        state=excluded.state,
        days_left=excluded.days_left, updated_at=excluded.updated_at
    """
    rows = []
    for r in found:
        rows.append((r["code"], r["name"], r["board"], r["t_date"], r["t_open"], r["t_close"],
                     r["t1_date"], r["t1_open"], r["t1_high"], r["t1_low"], r["t1_close"],
                     r["t1_gap"], r["t1_body"], r["breakout_date"], r["breakout_close"],
                     r["breakout_vol_ratio"], r["buy_date"], r["ref_price"], r["state"],
                     r["days_left"], now))
    if rows:
        db.xm(sql, rows)

    # 清理 150 个交易日以前、且从未产生计划的信号（保留近期历史便于复盘）
    cutoff = db.q1("SELECT date FROM (SELECT DISTINCT date FROM bars ORDER BY date DESC "
                   "LIMIT 1 OFFSET 150)")
    if cutoff and cutoff["date"]:
        db.x("DELETE FROM signals WHERE t_date < ? AND id NOT IN "
             "(SELECT signal_id FROM plans WHERE signal_id IS NOT NULL)", (cutoff["date"],))

    today = db.completed_trade_date()
    counts = {r["state"]: r["n"] for r in
              db.q("SELECT state, COUNT(*) AS n FROM signals GROUP BY state")}
    return {
        "scanned": scanned,
        "signals": len(found),
        "counts": counts,
        "today_actionable": len(actionable(today)) if today else 0,
        "trade_date": today,
    }


def actionable(day: str | None = None) -> list[dict]:
    """下一个交易日可以下手的信号：突破日就在最新收盘日，买点还没到。"""
    day = day or db.completed_trade_date()
    if not day:
        return []
    return [dict(r) for r in db.q(
        "SELECT * FROM signals WHERE state='triggered' AND (buy_date IS NULL OR buy_date>?) "
        "ORDER BY breakout_vol_ratio DESC", (day,))]


def missed(day: str | None = None) -> list[dict]:
    """买点已过但没执行的信号——这是纪律账本上最该看的一页。"""
    day = day or db.completed_trade_date()
    if not day:
        return []
    return [dict(r) for r in db.q(
        "SELECT * FROM signals WHERE state='triggered' AND buy_date<=? "
        "ORDER BY buy_date DESC LIMIT 60", (day,))]


def pool() -> dict:
    today = db.completed_trade_date()
    waiting = [dict(r) for r in db.q(
        "SELECT * FROM signals WHERE state='waiting' ORDER BY days_left ASC, t1_date DESC")]
    triggered = [dict(r) for r in db.q(
        "SELECT * FROM signals WHERE state='triggered' ORDER BY buy_date DESC, breakout_date DESC")]
    buy_today = actionable(today)
    pending_open = [s for s in triggered if s["buy_date"] is None]
    missed_rows = missed(today)
    expired = [dict(r) for r in db.q(
        "SELECT * FROM signals WHERE state='expired' ORDER BY t1_date DESC LIMIT 40")]
    # 第一次涨停突破没放量 → 形态作废，不进信号池
    failed = [dict(r) for r in db.q(
        "SELECT * FROM signals WHERE state='failed' ORDER BY breakout_date DESC LIMIT 40")]
    planned_ids = {r["signal_id"] for r in db.q(
        "SELECT signal_id FROM plans WHERE signal_id IS NOT NULL")}
    traded_ids = {r["signal_id"] for r in db.q(
        "SELECT signal_id FROM trades WHERE signal_id IS NOT NULL")}
    for group in (waiting, triggered, buy_today, pending_open, expired, missed_rows, failed):
        for s in group:
            s["planned"] = s["id"] in planned_ids
            s["traded"] = s["id"] in traded_ids
    return {
        "today": today,
        "actionable": buy_today,
        "waiting": waiting,
        "triggered": triggered,
        "expired": expired,
        "failed": failed,
        "missed": missed_rows,
        "counts": {"actionable": len(buy_today),
                   "waiting": len(waiting),
                   "triggered": len(triggered),
                   "failed": len(failed),
                   "missed": len(missed_rows)},
    }
