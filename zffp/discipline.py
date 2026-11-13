"""纪律引擎。

核心思想：**不靠意志力，靠闸门。**
凡是"事后才想起来"的规则（止损、仓位、盈亏比、连亏冷静期），
一律在写计划/记成交的瞬间由机器校验，做不到就不让过。

三类输出：
    blocks  -> 硬性阻止（必须改）
    warns   -> 放行但留痕（会计入纪律统计）
    calc    -> 由系统算出的股数/仓位/风险（不让你自己拍脑袋）
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import db


# --------------------------------------------------------------------------
# 交易日历（从行情表反推，无需外部接口）
# --------------------------------------------------------------------------
def trading_days() -> list[str]:
    return [r["date"] for r in db.q("SELECT DISTINCT date FROM bars ORDER BY date")]


def trade_date() -> str:
    """当前视角的"最新已收盘交易日"。"""
    d = db.completed_trade_date()
    return d or datetime.now().strftime("%Y-%m-%d")


def add_trading_days(base: str, n: int) -> str:
    days = trading_days()
    if not days:
        return (datetime.strptime(base, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")
    later = [d for d in days if d > base]
    if n <= 0:
        return base
    if len(later) >= n:
        return later[n - 1]
    return (datetime.strptime(days[-1], "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def next_trading_day(base: str) -> str:
    return add_trading_days(base, 1)


def trading_days_between(a: str, b: str) -> int:
    """a 到 b 之间隔了几个交易日（b 晚于 a 为正）。"""
    days = trading_days()
    try:
        return max(0, days.index(b) - days.index(a))
    except ValueError:
        da = datetime.strptime(a, "%Y-%m-%d")
        dbb = datetime.strptime(b, "%Y-%m-%d")
        return max(0, round((dbb - da).days * 5 / 7))


# --------------------------------------------------------------------------
# 账户快照
# --------------------------------------------------------------------------
def snapshot() -> dict:
    _, risk = db.load_params()
    cap = float(risk.get("total_capital", 200000))
    rows = db.q("SELECT * FROM trades")
    open_rows = [r for r in rows if r["status"] == "open"]
    closed = [r for r in rows if r["status"] == "closed"]
    realized = sum(float(r["pnl"] or 0) for r in closed)
    open_amount = sum(float(r["amount"] or 0) for r in open_rows)
    equity = cap + realized
    available = equity - open_amount

    closed_sorted = sorted(closed, key=lambda r: (r["sell_date"] or "", r["id"]), reverse=True)
    streak = 0
    for r in closed_sorted:
        if float(r["pnl"] or 0) < 0:
            streak += 1
        else:
            break

    today = trade_date()
    today_pnl = sum(float(r["pnl"] or 0) for r in closed if r["sell_date"] == today)

    cooldown_until = None
    need = int(risk.get("loss_streak_cooldown", 3))
    if streak >= need and closed_sorted:
        last_loss = next((r for r in closed_sorted if float(r["pnl"] or 0) < 0), None)
        if last_loss and last_loss["sell_date"]:
            cooldown_until = add_trading_days(last_loss["sell_date"],
                                              int(risk.get("cooldown_days", 3)))

    return {
        "total_capital": cap,
        "realized_pnl": round(realized, 2),
        "equity": round(equity, 2),
        "open_amount": round(open_amount, 2),
        "available": round(available, 2),
        "open_count": len(open_rows),
        "closed_count": len(closed),
        "win_rate": round(100.0 * sum(1 for r in closed if float(r["pnl"] or 0) > 0)
                          / len(closed), 1) if closed else 0.0,
        "loss_streak": streak,
        "cooldown_until": cooldown_until,
        "in_cooldown": bool(cooldown_until and cooldown_until >= today),
        "today_pnl": round(today_pnl, 2),
        "trade_date": today,
    }


# --------------------------------------------------------------------------
# 结果结构
# --------------------------------------------------------------------------
@dataclass
class Gate:
    code: str
    title: str
    detail: str
    fix: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "title": self.title,
                "detail": self.detail, "fix": self.fix}


def _round_lot(shares: float) -> int:
    return int(max(0, shares) // 100 * 100)


# --------------------------------------------------------------------------
# 计划校验
# --------------------------------------------------------------------------
def evaluate_plan(payload: dict) -> dict:
    strategy, risk = db.load_params()
    snap = snapshot()
    today = snap["trade_date"]

    blocks: list[Gate] = []
    warns: list[Gate] = []
    calc: dict = {}

    signal_id = payload.get("signal_id")
    sig = db.q1("SELECT * FROM signals WHERE id=?", (signal_id,)) if signal_id else None

    if not sig:
        blocks.append(Gate("SIGNAL_MISSING", "没有信号来源",
                           "计划必须挂在机器扫描出的信号上，不接受「临时觉得能涨」。",
                           "去信号池挑一只，或先跑扫描。"))
    elif sig["state"] != "triggered":
        blocks.append(Gate("SIGNAL_NOT_TRIGGERED", "信号尚未触发",
                           f"当前状态是「{sig['state']}」，还没有出现放量涨停突破。",
                           "等突破日出现再写计划，提前埋伏属于另一种战法。"))
    elif sig["buy_date"] and sig["buy_date"] < today:
        warns.append(Gate("SIGNAL_STALE", "买点已过",
                          f"该信号买点是 {sig['buy_date']}，已是过去时。",
                          "只能补记历史交易（会被标记为补记，不计违规）。"))

    expected_buy = None
    if sig:
        expected_buy = sig["buy_date"] or next_trading_day(sig["breakout_date"] or today)

    buy_date = payload.get("buy_date") or expected_buy
    buy_px = float(payload.get("buy_price_limit") or 0)
    stop = float(payload.get("stop_price") or 0)
    target = float(payload.get("target_price") or 0)
    thesis = (payload.get("thesis") or "").strip()
    backfill = bool(payload.get("backfill"))

    # ---- 买点日期 ----
    if sig and expected_buy and buy_date != expected_buy:
        blocks.append(Gate("BUY_DATE_MISMATCH", "买点日期不符",
                           f"信号规定买点是 {expected_buy}，你填的是 {buy_date}。",
                           "战法的买点只有一天，错过就不做。"))

    if buy_px <= 0:
        blocks.append(Gate("PRICE_MISSING", "缺少买入价上限",
                           "没有价格上限，等于允许自己无限追高。",
                           "填写「我愿意成交的最高价」。"))
    else:
        ref = float(sig["breakout_close"] or 0) if sig else 0
        chase = float(strategy.get("max_chase_pct", 0.08))
        if ref > 0 and buy_px > ref * (1 + chase):
            pct = (buy_px / ref - 1) * 100
            blocks.append(Gate("CHASE", "追高",
                               f"计划价 {buy_px:.2f} 已高于突破日收盘 {ref:.2f} 的 {pct:.1f}%，"
                               f"超过上限 {chase*100:.0f}%。",
                               "降低计划价，或放弃这笔。追高的胜率不会因为你想赚而提高。"))
        elif ref > 0 and buy_px > ref:
            warns.append(Gate("GAP_UP", "次日需高开承接",
                              f"计划价高于突破日收盘 {(buy_px/ref-1)*100:.1f}%，"
                              "需要开盘溢价才能成交。",
                              "开盘高于计划价就放弃，不要现价追。"))

    # ---- 止损 ----
    if stop <= 0:
        blocks.append(Gate("NO_STOP", "没有止损价",
                           "不设止损的交易不是交易，是赌博。",
                           "点「建议止损」由系统按形态算。"))
    elif buy_px > 0 and stop >= buy_px:
        blocks.append(Gate("STOP_ABOVE_PRICE", "止损价高于买入价",
                           "这个止损一开仓就会触发。", "止损必须低于买入价。"))
    elif buy_px > 0:
        risk_pct = (buy_px - stop) / buy_px
        max_stop = float(risk.get("max_stop_pct", 0.08))
        if risk_pct > max_stop:
            blocks.append(Gate("STOP_TOO_FAR", "止损太远",
                               f"止损幅度 {risk_pct*100:.1f}%，超过上限 {max_stop*100:.0f}%。"
                               "止损太远的真实结局是你不会执行。",
                               f"把止损收到 {buy_px*(1-max_stop):.2f} 以上，或降低买入价。"))

    # ---- 盈亏比 ----
    if buy_px > 0 and stop > 0 and stop < buy_px:
        if target <= buy_px:
            blocks.append(Gate("NO_TARGET", "缺少目标价",
                               "没有目标就不知道什么时候该走。",
                               "点「建议目标」由系统按 2:1 盈亏比算。"))
        else:
            rr = (target - buy_px) / (buy_px - stop)
            calc["rr"] = round(rr, 2)
            min_rr = float(risk.get("min_rr", 2.0))
            if rr < min_rr - 0.01:      # 容忍分位取整误差
                blocks.append(Gate("RR_LOW", "盈亏比不够",
                                   f"当前盈亏比 {rr:.2f}:1，低于下限 {min_rr:.1f}:1。",
                                   "要么止损更近，要么目标更高，否则长期必亏。"))

    # ---- 仓位与资金 ----
    if buy_px > 0 and stop > 0 and stop < buy_px:
        risk_budget = snap["equity"] * float(risk.get("risk_per_trade_pct", 0.02))
        by_risk = _round_lot(risk_budget / (buy_px - stop))
        by_pos = _round_lot(snap["equity"] * float(risk.get("max_position_pct", 0.30)) / buy_px)
        by_cash = _round_lot(snap["available"] / buy_px)
        shares = min(by_risk, by_pos, by_cash)
        calc.update({
            "risk_budget": round(risk_budget, 2),
            "max_shares_by_risk": by_risk,
            "max_shares_by_position": by_pos,
            "max_shares_by_cash": by_cash,
            "shares": shares,
            "amount": round(shares * buy_px, 2),
            "position_pct": round(shares * buy_px / snap["equity"] * 100, 1) if snap["equity"] else 0,
            "risk_amount": round(shares * (buy_px - stop), 2),
            "risk_pct": round(shares * (buy_px - stop) / snap["equity"] * 100, 2) if snap["equity"] else 0,
        })
        if shares <= 0:
            blocks.append(Gate("SIZE_ZERO", "算出来买不了 1 手",
                               "按风险预算和仓位上限算，可买股数为 0。",
                               "资金太小或止损太远；这种情况本来就不该做。"))
        else:
            want = int(payload.get("shares") or 0)
            if want > shares:
                blocks.append(Gate("OVERSIZE", "超出系统允许的股数",
                                   f"你填 {want} 股，按单笔风险和仓位上限最多 {shares} 股。",
                                   "按系统给的股数下单。重仓不是勇气，是没算过账。"))
            elif want > 0:
                calc.update({
                    "shares": want,
                    "amount": round(want * buy_px, 2),
                    "position_pct": round(want * buy_px / snap["equity"] * 100, 1),
                    "risk_amount": round(want * (buy_px - stop), 2),
                    "risk_pct": round(want * (buy_px - stop) / snap["equity"] * 100, 2),
                })
        if shares > 0 and calc["risk_pct"] > float(risk.get("risk_per_trade_pct", 0.02)) * 100 + 0.01:
            warns.append(Gate("RISK_SLIGHT", "风险略超预算",
                              f"实际单笔风险 {calc['risk_pct']:.2f}%，"
                              f"预算 {float(risk.get('risk_per_trade_pct',0.02))*100:.1f}%。",
                              "按系统给的股数下单。"))

    # ---- 纪律类 ----
    if len(thesis) < 10:
        blocks.append(Gate("THESIS_SHORT", "买入理由太敷衍",
                           f"现在只有 {len(thesis)} 个字。写不出理由，就是没有理由。",
                           "至少写清：为什么是它、为什么是这个位置、错了在哪走。"))

    plans_today = db.q1("SELECT COUNT(*) AS n FROM plans WHERE plan_date=? AND status='locked'",
                        (today,))["n"]
    cap_plans = int(risk.get("max_plans_per_day", 3))
    if plans_today >= cap_plans:
        blocks.append(Gate("PLAN_CAP", "今日计划已满",
                           f"今天已经写了 {plans_today} 个计划，上限 {cap_plans}。",
                           "计划越多的日子，往往是手最痒的日子。"))

    if snap["open_count"] >= int(risk.get("max_new_positions", 3)):
        blocks.append(Gate("POSITION_CAP", "持仓数量已满",
                           f"当前持有 {snap['open_count']} 只，上限 "
                           f"{int(risk.get('max_new_positions', 3))} 只。",
                           "先处理手上的票。"))

    if snap["in_cooldown"]:
        blocks.append(Gate("COOLDOWN", "冷静期",
                           f"已连亏 {snap['loss_streak']} 笔，冷静期到 {snap['cooldown_until']}。",
                           "空仓不是认输，是止损的一部分。"))
    elif snap["loss_streak"] >= 2:
        warns.append(Gate("LOSS_STREAK", "连亏预警",
                          f"已连亏 {snap['loss_streak']} 笔，"
                          f"再亏 {int(risk.get('loss_streak_cooldown',3)) - snap['loss_streak']} 笔进入冷静期。",
                          "降低仓位，只做最标准的形态。"))

    stop_loss_line = -snap["equity"] * float(risk.get("daily_stop_loss_pct", 0.03))
    if snap["today_pnl"] <= stop_loss_line:
        blocks.append(Gate("DAILY_STOP", "今日已触及止损线",
                           f"今日亏损 {snap['today_pnl']:.0f} 元，超过上限 {stop_loss_line:.0f}。",
                           "今天收工。明天再战。"))

    if sig and db.q1("SELECT 1 FROM trades WHERE code=? AND status='open'", (sig["code"],)):
        blocks.append(Gate("ALREADY_HOLDING", "已持有该股",
                           f"{sig['name']} 还在持仓里。", "不要对同一只票重复下注。"))
    if sig and db.q1("SELECT 1 FROM plans WHERE signal_id=? AND status='locked'", (sig["id"],)):
        blocks.append(Gate("DUP_PLAN", "该信号已有计划",
                           "同一信号只能有一个执行计划。", "去「计划」页修改，而不是再写一个。"))

    if backfill:
        warns.append(Gate("BACKFILL", "补记模式",
                          "按补记处理，不计入违规统计。", ""))

    return {
        "ok": len(blocks) == 0,
        "blocks": [b.as_dict() for b in blocks],
        "warns": [w.as_dict() for w in warns],
        "calc": calc,
        "signal": dict(sig) if sig else None,
        "snapshot": snap,
        "expected_buy_date": expected_buy,
        "trade_date": today,
    }


def suggest(signal: dict, buy_price: float | None = None) -> dict:
    """给出建议的买入上限 / 止损 / 目标 / 股数。

    所有数字先取到分，再据此推目标价并向上取整，
    保证"系统给的数"一定能通过"系统的闸门"（否则用户会觉得被耍）。
    """
    _, risk = db.load_params()
    snap = snapshot()
    ref = float(signal.get("ref_price") or signal.get("breakout_close") or 0)
    if not ref:
        return {}
    buy = round(float(buy_price or ref), 2)
    max_stop = float(risk.get("max_stop_pct", 0.08))
    t1h = float(signal.get("t1_high") or 0)
    # 止损：形态失效位（跌破阴线最高价）与风控上限取更近者
    by_pattern = round(t1h * 0.985, 2) if t1h else 0.0
    by_risk = round(buy * (1 - max_stop), 2)
    stop = max(by_pattern, by_risk) if by_pattern else by_risk
    if stop >= buy:
        stop = round(buy * 0.95, 2)
    need = round(buy - stop, 2)
    min_rr = float(risk.get("min_rr", 2.0))
    target = math.ceil(round(buy + need * min_rr, 4) * 100) / 100

    risk_budget = snap["equity"] * float(risk.get("risk_per_trade_pct", 0.02))
    by_risk_sh = _round_lot(risk_budget / max(0.01, need))
    by_pos_sh = _round_lot(snap["equity"] * float(risk.get("max_position_pct", 0.30)) / buy)
    by_cash_sh = _round_lot(snap["available"] / buy)
    shares = min(by_risk_sh, by_pos_sh, by_cash_sh)
    return {
        "buy_price_limit": buy,
        "stop_price": stop,
        "target_price": target,
        "shares": shares,
        "amount": round(shares * buy, 2),
        "risk_amount": round(shares * need, 2),
        "rr": round((target - buy) / max(0.01, need), 2),
        "stop_pct": round(need / buy * 100, 2),
        "position_pct": round(shares * buy / snap["equity"] * 100, 1) if snap["equity"] else 0,
    }


# --------------------------------------------------------------------------
# 成交校验
# --------------------------------------------------------------------------
def evaluate_trade(payload: dict) -> dict:
    _, risk = db.load_params()
    snap = snapshot()
    today = snap["trade_date"]
    blocks: list[Gate] = []
    warns: list[Gate] = []

    plan = db.q1("SELECT * FROM plans WHERE id=?", (payload.get("plan_id"),)) \
        if payload.get("plan_id") else None
    backfill = bool(payload.get("backfill"))

    if not plan:
        blocks.append(Gate("NO_PLAN", "无计划不成交",
                           "这笔买入没有对应的交易计划。系统拒绝记录，"
                           "因为记录下来的不是交易，是冲动。",
                           "先去写计划：买什么、买多少、错了在哪走。"))
        return {"ok": False, "blocks": [b.as_dict() for b in blocks],
                "warns": [], "plan": None, "snapshot": snap}

    if plan["status"] != "locked":
        blocks.append(Gate("PLAN_USED", "计划已失效",
                           f"该计划状态是「{plan['status']}」。", "重新写一个计划。"))

    buy_date = payload.get("buy_date") or plan["buy_date"]
    price = float(payload.get("buy_price") or 0)
    shares = int(payload.get("shares") or plan["shares"] or 0)

    if not backfill and buy_date != plan["buy_date"]:
        blocks.append(Gate("DATE_MISMATCH", "不是计划的日期",
                           f"计划买点是 {plan['buy_date']}，你记的是 {buy_date}。",
                           "错过就作废，不要三天后补一枪。"))

    limit_px = float(plan["buy_price_limit"] or 0)
    tol = float(risk.get("slippage_tol", 0.002))
    if price <= 0:
        blocks.append(Gate("PRICE_MISSING", "缺少成交价", "", "填写实际成交价。"))
    elif limit_px and price > limit_px * (1 + tol):
        blocks.append(Gate("OVER_PRICE", "超出计划价买入",
                           f"成交 {price:.2f} > 计划上限 {limit_px:.2f}。",
                           "追价成交必须先去改计划（会留痕），不能既追又装作没追。"))

    if shares <= 0:
        blocks.append(Gate("SHARES_MISSING", "股数不正确", "", "填写股数（100 的整数倍）。"))
    elif plan["shares"] and shares > int(plan["shares"]):
        blocks.append(Gate("OVERSIZE", "超出计划仓位",
                           f"计划 {plan['shares']} 股，实际 {shares} 股。",
                           "加仓是另一个计划，不是同一个计划多买点。"))

    amount = price * shares
    if amount > snap["available"] + 1:
        blocks.append(Gate("NO_CASH", "可用资金不足",
                           f"需要 {amount:.0f}，可用 {snap['available']:.0f}。", ""))

    if snap["in_cooldown"]:
        blocks.append(Gate("COOLDOWN", "冷静期",
                           f"冷静期到 {snap['cooldown_until']}。", "空仓。"))
    stop_line = -snap["equity"] * float(risk.get("daily_stop_loss_pct", 0.03))
    if snap["today_pnl"] <= stop_line:
        blocks.append(Gate("DAILY_STOP", "今日已触及止损线",
                           f"今日亏损 {snap['today_pnl']:.0f} 元。", "今天收工。"))

    if db.q1("SELECT 1 FROM trades WHERE code=? AND status='open'", (plan["code"],)):
        blocks.append(Gate("ALREADY_HOLDING", "已持有该股", f"{plan['name']} 在持仓中。", ""))

    if backfill:
        warns.append(Gate("BACKFILL", "补记", "补记历史成交，不计违规。", ""))

    return {
        "ok": len(blocks) == 0,
        "blocks": [b.as_dict() for b in blocks],
        "warns": [w.as_dict() for w in warns],
        "plan": dict(plan),
        "snapshot": snap,
        "computed": {"amount": round(amount, 2),
                     "position_pct": round(amount / snap["equity"] * 100, 1) if snap["equity"] else 0},
    }


def stop_delay_days(code: str, buy_date: str, sell_date: str, stop_price: float) -> int:
    """从收盘跌破止损到真正卖出，拖了几天。"""
    if not stop_price or stop_price <= 0:
        return 0
    rows = db.q("SELECT date, low, close FROM bars WHERE code=? AND date>=? AND date<=? "
                "ORDER BY date", (code, buy_date, sell_date))
    first_break = None
    for r in rows:
        if r["low"] <= stop_price:
            first_break = r["date"]
            break
    if not first_break or first_break >= sell_date:
        return 0
    return trading_days_between(first_break, sell_date)


def evaluate_close(trade: dict, payload: dict) -> dict:
    blocks: list[Gate] = []
    warns: list[Gate] = []
    price = float(payload.get("sell_price") or 0)
    sell_date = payload.get("sell_date") or trade_date()
    reason = (payload.get("exit_reason") or "").strip()

    if price <= 0:
        blocks.append(Gate("PRICE_MISSING", "缺少卖出价", "", ""))
    if not reason:
        blocks.append(Gate("NO_REASON", "没写卖出原因",
                           "卖出原因决定你这笔交易是「按计划」还是「情绪」。",
                           "选择原因并补一句话。"))

    violations: list[str] = []
    stop = float(trade.get("stop_price") or 0)
    if price > 0 and stop > 0 and price < stop:
        delay = stop_delay_days(trade["code"], trade["buy_date"], sell_date, stop)
        if delay >= 1:
            violations.append(f"STOP_DELAY:{delay}")
            warns.append(Gate("STOP_DELAY", f"止损拖了 {delay} 个交易日",
                              f"股价早已跌破止损 {stop:.2f}，你才在 {price:.2f} 卖出。",
                              "这个拖字，就是账户回撤的主要来源。"))
        else:
            warns.append(Gate("STOP_HIT", "破止损离场",
                              "按纪律止损，这笔亏得对。", ""))

    if sell_date < trade["buy_date"]:
        blocks.append(Gate("DATE_INVALID", "卖出日期早于买入", "", ""))
    return {"ok": len(blocks) == 0, "blocks": [b.as_dict() for b in blocks],
            "warns": [w.as_dict() for w in warns], "violations": violations}


# --------------------------------------------------------------------------
# 纪律分
# --------------------------------------------------------------------------
def review_score(followed_plan: str, violations: list[str]) -> int:
    score = 100
    if followed_plan == "否":
        score -= 45
    elif followed_plan == "部分":
        score -= 20
    score -= 15 * len([v for v in violations if not v.startswith("BACKFILL")])
    return max(0, min(100, score))


def daily_score(day: str) -> dict:
    """某个交易日的纪律分：只看当天发生的动作，不看盈亏。"""
    trades = db.q("SELECT * FROM trades")
    events: list[dict] = []
    score = 100

    for t in trades:
        vs = _loads(t["violations"])
        if t["buy_date"] == day:
            if not t["plan_id"]:
                score -= 40
                events.append({"type": "no_plan", "text": f"{t['name']} 无计划买入", "delta": -40})
            for v in vs:
                if v == "OVER_PRICE":
                    score -= 25
                    events.append({"type": "over_price", "text": f"{t['name']} 追价成交", "delta": -25})
                elif v == "OVERSIZE":
                    score -= 20
                    events.append({"type": "oversize", "text": f"{t['name']} 超仓位", "delta": -20})
                elif v == "COOLDOWN":
                    score -= 40
                    events.append({"type": "cooldown", "text": f"{t['name']} 冷静期开仓", "delta": -40})
        if t["sell_date"] == day:
            for v in vs:
                if v.startswith("STOP_DELAY"):
                    score -= 30
                    events.append({"type": "stop_delay",
                                   "text": f"{t['name']} 止损拖延", "delta": -30})
            rv = db.q1("SELECT id FROM reviews WHERE trade_id=?", (t["id"],))
            if not rv:
                score -= 20
                events.append({"type": "no_review", "text": f"{t['name']} 未复盘", "delta": -20})

    plans = db.q("SELECT * FROM plans WHERE plan_date=? AND status='locked'", (day,))
    for p in plans:
        if p["buy_date"] < day and not db.q1("SELECT 1 FROM trades WHERE plan_id=?", (p["id"],)):
            score -= 15
            events.append({"type": "plan_unexecuted",
                           "text": f"{p['name']} 计划了没执行（{p['buy_date']}）", "delta": -15})

    return {"date": day, "score": max(0, min(100, score)), "events": events}


def _loads(raw) -> list:
    import json
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else [str(v)]
    except Exception:
        return [str(raw)]


def streak() -> dict:
    """连续合规天数（只统计有动作的交易日）。"""
    days = sorted({r["date"] for r in db.q("SELECT DISTINCT date FROM bars")}, reverse=True)
    active = set()
    for t in db.q("SELECT buy_date, sell_date FROM trades"):
        if t["buy_date"]:
            active.add(t["buy_date"])
        if t["sell_date"]:
            active.add(t["sell_date"])
    for p in db.q("SELECT plan_date FROM plans"):
        active.add(p["plan_date"])

    if not active:
        return {"days": 0, "since": None, "today_score": 100}

    ordered = [d for d in days if d in active]
    run = 0
    since = None
    for d in ordered:
        sc = daily_score(d)
        if sc["score"] >= 90 and not sc["events"]:
            run += 1
            since = d
        else:
            break
    today = trade_date()
    return {"days": run, "since": since, "today_score": daily_score(today)["score"]}
