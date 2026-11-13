"""统计：把"我觉得"换成"数据显示"。

最重要的一张表：合规交易 vs 违规交易的期望值对比。
多数人不是不知道纪律，是不知道破戒的代价具体是多少钱。
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime

from . import db
from .discipline import snapshot, trading_days_between


def _viols(t: dict) -> list[str]:
    raw = t.get("violations")
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return [str(i) for i in v] if isinstance(v, list) else [str(v)]
    except Exception:
        return [str(raw)]


def _stats(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"count": 0, "win_rate": 0, "avg_pnl": 0, "avg_win": 0, "avg_loss": 0,
                "expectancy": 0, "profit_factor": 0, "total_pnl": 0, "avg_hold": 0,
                "best": 0, "worst": 0, "max_win_streak": 0, "max_loss_streak": 0}
    pnls = [float(t["pnl"] or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    holds = [t["hold_days"] or 0 for t in trades]

    # 连赢/连亏
    max_w = max_l = w = ls = 0
    for t in sorted(trades, key=lambda r: (r["sell_date"] or "", r["id"])):
        if float(t["pnl"] or 0) > 0:
            w += 1
            ls = 0
            max_w = max(max_w, w)
        else:
            ls += 1
            w = 0
            max_l = max(max_l, ls)

    return {
        "count": n,
        "win_rate": round(100.0 * len(wins) / n, 1),
        "avg_pnl": round(sum(pnls) / n, 2),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "expectancy": round(sum(pnls) / n, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 0,
        "total_pnl": round(sum(pnls), 2),
        "avg_hold": round(sum(holds) / n, 1),
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
        "max_win_streak": max_w,
        "max_loss_streak": max_l,
    }


def overview() -> dict:
    rows = [dict(r) for r in db.q("SELECT * FROM trades ORDER BY buy_date")]
    closed = [t for t in rows if t["status"] == "closed"]
    open_rows = [t for t in rows if t["status"] == "open"]
    snap = snapshot()

    # 权益曲线
    curve = []
    cum = 0.0
    base = snap["total_capital"]
    for t in sorted(closed, key=lambda r: (r["sell_date"] or "", r["id"])):
        cum += float(t["pnl"] or 0)
        curve.append({"date": t["sell_date"], "equity": round(base + cum, 2),
                      "pnl": round(float(t["pnl"] or 0), 2)})

    peak = base
    max_dd = 0.0
    for p in curve:
        peak = max(peak, p["equity"])
        max_dd = max(max_dd, (peak - p["equity"]) / peak * 100 if peak else 0)

    # 合规 vs 违规
    ok_rows, bad_rows = [], []
    for t in closed:
        if t["compliance"] == "ok" or not _viols(t):
            ok_rows.append(t)
        else:
            bad_rows.append(t)

    # 按月
    monthly: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0, "wins": 0})
    for t in closed:
        m = (t["sell_date"] or "")[:7]
        if not m:
            continue
        monthly[m]["pnl"] += float(t["pnl"] or 0)
        monthly[m]["count"] += 1
        if float(t["pnl"] or 0) > 0:
            monthly[m]["wins"] += 1
    months = [{"month": k, "pnl": round(v["pnl"], 2), "count": v["count"],
               "win_rate": round(100 * v["wins"] / v["count"], 1) if v["count"] else 0}
              for k, v in sorted(monthly.items())]

    # 错误标签
    tags = Counter()
    for r in db.q("SELECT mistake_tags FROM reviews"):
        try:
            for tag in json.loads(r["mistake_tags"] or "[]"):
                tags[tag] += 1
        except Exception:
            continue

    # 信号漏斗
    sig_total = db.q1("SELECT COUNT(*) AS n FROM signals")["n"]
    sig_trig = db.q1("SELECT COUNT(*) AS n FROM signals WHERE state='triggered'")["n"]
    plan_total = db.q1("SELECT COUNT(*) AS n FROM plans")["n"]
    trade_total = len(rows)
    reviewed = db.q1("SELECT COUNT(*) AS n FROM reviews")["n"]

    # 信号本身的表现（不管你有没有做）
    trig_rows = db.q("SELECT * FROM signals WHERE state='triggered' AND buy_date IS NOT NULL")
    sig_win = 0
    sig_n = 0
    for s in trig_rows:
        rows_b = db.q("SELECT date,close FROM bars WHERE code=? AND date>=? ORDER BY date LIMIT 6",
                      (s["code"], s["buy_date"]))
        if len(rows_b) >= 2 and s["ref_price"]:
            sig_n += 1
            if rows_b[-1]["close"] > s["ref_price"]:
                sig_win += 1

    return {
        "snapshot": snap,
        "all": _stats(closed),
        "compliant": _stats(ok_rows),
        "violating": _stats(bad_rows),
        "curve": curve,
        "max_drawdown": round(max_dd, 2),
        "monthly": months,
        "mistake_tags": tags.most_common(12),
        "funnel": {"signals": sig_total, "triggered": sig_trig, "plans": plan_total,
                   "trades": trade_total, "reviewed": reviewed},
        "signal_edge": {"n": sig_n, "win_rate": round(100 * sig_win / sig_n, 1) if sig_n else 0},
        "strategy": strategy_check(),
        "open_positions": open_rows,
    }


def strategy_check(horizons: tuple[int, ...] = (1, 3, 5)) -> dict:
    """战法体检：只看历史信号本身的后续表现，不看你有没有做。

    这是"该不该相信这套战法"的答案，也是没纪律时最容易忽略的基础概率。
    """
    rows = [dict(r) for r in db.q(
        "SELECT * FROM signals WHERE state='triggered' AND buy_date IS NOT NULL "
        "AND ref_price IS NOT NULL ORDER BY buy_date DESC LIMIT 800")]
    buckets: dict[int, list[float]] = {h: [] for h in horizons}
    adverse: list[float] = []
    for s in rows:
        bars = db.q("SELECT close,low FROM bars WHERE code=? AND date>=? ORDER BY date LIMIT ?",
                    (s["code"], s["buy_date"], max(horizons) + 1))
        if not bars:
            continue
        entry = float(s["ref_price"])
        if entry <= 0:
            continue
        for h in horizons:
            if len(bars) >= h:
                buckets[h].append((bars[h - 1]["close"] / entry - 1) * 100)
        if bars:
            adverse.append((min(b["low"] for b in bars[:max(horizons)]) / entry - 1) * 100)

    out = {}
    for h, vals in buckets.items():
        if not vals:
            out[f"d{h}"] = {"n": 0, "avg": 0, "win_rate": 0}
            continue
        out[f"d{h}"] = {
            "n": len(vals),
            "avg": round(sum(vals) / len(vals), 2),
            "win_rate": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
            "best": round(max(vals), 2),
            "worst": round(min(vals), 2),
        }
    out["avg_adverse"] = round(sum(adverse) / len(adverse), 2) if adverse else 0
    out["total_signals"] = len(rows)
    out["waiting"] = db.q1("SELECT COUNT(*) AS n FROM signals WHERE state='waiting'")["n"]
    return out


def trade_detail(trade_id: int) -> dict:
    t = db.q1("SELECT * FROM trades WHERE id=?", (trade_id,))
    if not t:
        return {}
    d = dict(t)
    d["violations"] = _viols(d)
    plan = db.q1("SELECT * FROM plans WHERE id=?", (t["plan_id"],)) if t["plan_id"] else None
    d["plan"] = dict(plan) if plan else None
    rv = db.q1("SELECT * FROM reviews WHERE trade_id=?", (trade_id,))
    d["review"] = dict(rv) if rv else None
    sig = db.q1("SELECT * FROM signals WHERE id=?", (t["signal_id"],)) if t["signal_id"] else None
    d["signal"] = dict(sig) if sig else None
    bars = db.q("SELECT date,open,high,low,close,volume FROM bars WHERE code=? AND date>=? "
                "ORDER BY date LIMIT 12", (t["code"], t["buy_date"]))
    d["bars"] = [dict(b) for b in bars]
    return d
