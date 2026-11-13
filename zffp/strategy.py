"""巨阴突破战法：形态识别核心。

规则（全部可参数化）：
    T   日：涨停（收盘封板）
    T+1 日：高开 + 收阴 + 收盘价 > T 日开盘价
    突破日：T+1 之后 window 个交易日内，出现「放量涨停」且收盘价 > T+1 最高价
    买点  ：突破日的下一个交易日

本模块是纯函数，不碰数据库、不碰网络，方便单测与复算。
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


# --------------------------------------------------------------------------
# 板块与涨停幅度
# --------------------------------------------------------------------------
def board_of(code: str) -> str:
    c = code[-6:]
    if c.startswith(("688", "689")):
        return "star"
    if c.startswith(("300", "301", "302")):
        return "chinext"
    if c.startswith(("8", "4", "92")):
        return "bj"
    return "main"


def limit_ratio(code: str, name: str = "") -> float:
    """涨停幅度：主板 10%，创业板/科创板 20%，北交所 30%，主板 ST 5%。"""
    board = board_of(code)
    if board == "bj":
        return 0.30
    if board in ("star", "chinext"):
        return 0.20          # 创业板/科创板 ST 同样是 20%
    if "ST" in (name or "").upper():
        return 0.05
    return 0.10


def is_limit_up(prev_close: float, bar: Bar, ratio: float, tol: float = 0.005) -> bool:
    """涨停判定：涨幅接近上限，且以当日最高价收盘（封住）。"""
    if prev_close <= 0:
        return False
    pct = bar.close / prev_close - 1.0
    if pct < ratio - tol:
        return False
    # 收盘即最高价（留一点浮点余量），避免把"冲高未封"算成涨停
    return bar.close >= bar.high - max(1e-6, bar.high * 5e-4)


def _vol_ratio(bars: list[Bar], idx: int, days: int) -> float:
    lo = max(0, idx - days)
    hist = [b.volume for b in bars[lo:idx] if b.volume > 0]
    if not hist:
        return 0.0
    base = mean(hist)
    if base <= 0:
        return 0.0
    return bars[idx].volume / base


# --------------------------------------------------------------------------
# 单只股票扫描
# --------------------------------------------------------------------------
def scan_stock(
    code: str,
    name: str,
    bars: list[Bar],
    p: dict,
    *,
    lookback: int = 60,
) -> list[dict]:
    """扫描单只股票，返回所有形态实例。

    state:
        triggered -> 已出现突破，等买点（buy_date 可能为 None 表示次日待定）
        waiting   -> T+1 已成立，突破窗口还没走完
        expired   -> 窗口走完仍未突破
    """
    n = len(bars)
    if n < 4:
        return []
    ratio = limit_ratio(code, name)
    tol = float(p.get("limit_tol", 0.005))
    window = int(p.get("window", 10))
    gap_min = float(p.get("min_gap_pct", 0.01))
    body_min = float(p.get("min_body_pct", 0.015))
    need_above = bool(p.get("require_close_above_t_open", True))
    vol_days = int(p.get("vol_ma_days", 5))
    vol_need = float(p.get("vol_ratio", 1.5))

    start = max(1, n - lookback)
    out: list[dict] = []

    for i in range(start, n - 1):
        t = bars[i]
        # ---- T 日：涨停 ----
        if not is_limit_up(bars[i - 1].close, t, ratio, tol):
            continue

        t1 = bars[i + 1]
        # ---- T+1：高开 ----
        if t1.open < t.close * (1 + gap_min):
            continue
        # ---- T+1：收阴 ----
        if t1.close >= t1.open:
            continue
        # ---- T+1：收盘价仍高于 T 日开盘价（多头未破坏）----
        if need_above and t1.close <= t.open:
            continue
        body = (t1.open - t1.close) / t1.open if t1.open > 0 else 0.0
        if body < body_min:
            continue

        # ---- 突破：T+1 之后 window 个交易日内 ----
        win_end = min(n, i + 2 + window)

        # 先找「第一次涨停收盘突破」是哪一天——注意是第一次，不是第一个放量的
        first_break = -1
        for j in range(i + 2, win_end):
            b = bars[j]
            if b.close <= t1.high:            # 必须收盘突破 T+1 最高价
                continue
            if not is_limit_up(bars[j - 1].close, b, ratio, tol):
                continue
            first_break = j
            break

        hit_idx = -1
        hit_vr = 0.0
        if first_break > 0:
            vr = _vol_ratio(bars, first_break, vol_days)
            if vr >= vol_need:
                # 第一次突破就放量：这才是战法要的那一笔
                hit_idx, hit_vr = first_break, vr
            elif not p.get("first_break_must_volume", True):
                # 兼容旧口径：允许等到后面某天放量涨停再算
                for j in range(first_break + 1, win_end):
                    b = bars[j]
                    if b.close <= t1.high or not is_limit_up(bars[j - 1].close, b, ratio, tol):
                        continue
                    v2 = _vol_ratio(bars, j, vol_days)
                    if v2 >= vol_need:
                        hit_idx, hit_vr = j, v2
                        break

        rec = {
            "code": code,
            "name": name,
            "board": board_of(code),
            "t_date": t.date,
            "t_open": t.open,
            "t_close": t.close,
            "t1_date": t1.date,
            "t1_open": t1.open,
            "t1_high": t1.high,
            "t1_low": t1.low,
            "t1_close": t1.close,
            "t1_gap": t1.open / t.close - 1.0,
            "t1_body": body,
            "breakout_date": None,
            "breakout_close": None,
            "breakout_vol_ratio": None,
            "buy_date": None,
            "ref_price": None,
            "state": "waiting",
            "days_left": 0,
        }

        if hit_idx > 0:
            b = bars[hit_idx]
            rec["breakout_date"] = b.date
            rec["breakout_close"] = b.close
            rec["breakout_vol_ratio"] = round(hit_vr, 2)
            rec["state"] = "triggered"
            if hit_idx + 1 < n:
                nb = bars[hit_idx + 1]
                rec["buy_date"] = nb.date
                rec["ref_price"] = nb.open
        elif first_break > 0:
            # 第一次涨停突破没放量 —— 形态已被消耗，后面再放量涨停也不算
            b = bars[first_break]
            rec["breakout_date"] = b.date
            rec["breakout_close"] = b.close
            rec["breakout_vol_ratio"] = round(_vol_ratio(bars, first_break, vol_days), 2)
            rec["state"] = "failed"
        else:
            if win_end >= n:
                rec["state"] = "waiting"
                rec["days_left"] = max(0, (i + 2 + window) - n)
            else:
                rec["state"] = "expired"

        out.append(rec)

    return out


def dedupe(signals: Iterable[dict]) -> list[dict]:
    """同一只票同一 T 日只留一条；同一只票保留最近的一条。"""
    seen: dict[tuple[str, str], dict] = {}
    for s in signals:
        seen[(s["code"], s["t_date"])] = s
    return list(seen.values())


def best_per_code(signals: Iterable[dict]) -> list[dict]:
    """每只票只留最新的一条（信号池展示用）。"""
    best: dict[str, dict] = {}
    for s in signals:
        cur = best.get(s["code"])
        if cur is None or s["t_date"] > cur["t_date"]:
            best[s["code"]] = s
    return list(best.values())


def describe(rec: dict) -> str:
    """给出一条人类可读的信号描述。"""
    if rec["state"] == "triggered":
        if rec.get("buy_date"):
            return f"{rec['breakout_date']} 放量涨停({rec['breakout_vol_ratio']}倍)突破阴线高点 {rec['t1_high']:.2f}，买点 {rec['buy_date']}"
        return f"{rec['breakout_date']} 放量涨停突破阴线高点 {rec['t1_high']:.2f}，等次日开盘买入"
    if rec["state"] == "failed":
        return (f"{rec['breakout_date']} 涨停突破但量比仅 {rec['breakout_vol_ratio']}，"
                f"第一次突破无量，形态作废")
    if rec["state"] == "waiting":
        return f"{rec['t1_date']} 巨阴成立，剩余 {rec.get('days_left', 0)} 个交易日等突破 {rec['t1_high']:.2f}"
    return f"{rec['t1_date']} 巨阴成立，窗口内未突破 {rec['t1_high']:.2f}"
