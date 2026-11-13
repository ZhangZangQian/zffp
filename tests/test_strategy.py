"""形态识别单测：用手工构造的 K 线验证每一条规则都被严格执行。"""
from __future__ import annotations

from datetime import date, timedelta

from zffp.strategy import Bar, is_limit_up, limit_ratio, scan_stock

P = {
    "limit_tol": 0.005,
    "min_gap_pct": 0.01,
    "min_body_pct": 0.015,
    "require_close_above_t_open": True,
    "window": 10,
    "vol_ma_days": 5,
    "vol_ratio": 1.5,
}


def bars_from(rows: list[tuple]) -> list[Bar]:
    d0 = date(2026, 1, 5)
    out = []
    for i, (o, h, l, c, v) in enumerate(rows):
        d = d0 + timedelta(days=i)
        out.append(Bar(d.strftime("%Y-%m-%d"), o, h, l, c, v))
    return out


def base_pattern(breakout_volume: float = 300000.0,
                 breakout_close: float = 12.43,
                 t1_close: float = 11.20,
                 t1_open: float = 11.55) -> list[tuple]:
    """10 元起，T 日涨停，T+1 高开低走，第 6 个交易日放量涨停突破。"""
    rows = [(10.0, 10.1, 9.9, 10.0, 100000)] * 5
    # T 日：涨停封板
    rows.append((10.30, 11.00, 10.20, 11.00, 200000))
    # T+1：高开 5%，收阴，收盘 11.20 > T 日开盘 10.30
    rows.append((t1_open, 11.80, 11.05, t1_close, 180000))
    # 横盘 4 天
    rows += [(11.30, 11.45, 11.10, 11.30, 90000)] * 4
    # 突破日：涨停 12.43，放量，收盘价 > T+1 最高价 11.80
    rows.append((11.50, breakout_close, 11.45, breakout_close, breakout_volume))
    # 买点日
    rows.append((12.60, 12.90, 12.40, 12.70, 260000))
    return rows


def test_limit_ratio_by_board():
    assert limit_ratio("600000") == 0.10
    assert limit_ratio("000001") == 0.10
    assert limit_ratio("300750") == 0.20
    assert limit_ratio("688981") == 0.20
    assert limit_ratio("600001", "ST某某") == 0.05
    assert limit_ratio("300001", "ST某某") == 0.20


def test_is_limit_up_requires_close_at_high():
    bar = Bar("2026-01-05", 10.5, 11.0, 10.4, 11.0, 1000)
    assert is_limit_up(10.0, bar, 0.10)
    # 冲高回落，收盘不在最高价 -> 未封板
    bar2 = Bar("2026-01-05", 10.5, 11.0, 10.4, 10.92, 1000)
    assert not is_limit_up(10.0, bar2, 0.10)


def test_normal_pattern_hits():
    sigs = scan_stock("002001", "测试股", bars_from(base_pattern()), P)
    assert len(sigs) == 1
    s = sigs[0]
    assert s["state"] == "triggered"
    assert s["t1_high"] == 11.80
    assert s["breakout_date"] == "2026-01-16"
    assert s["buy_date"] == "2026-01-17"
    assert s["ref_price"] == 12.60


def test_reject_when_t1_close_below_t_open():
    # T+1 收盘 10.20 < T 日开盘 10.30，形态被破坏
    rows = base_pattern(t1_close=10.20)
    assert scan_stock("002001", "测试股", bars_from(rows), P) == []


def test_reject_when_t1_not_gap_up():
    rows = base_pattern(t1_open=10.95)
    assert scan_stock("002001", "测试股", bars_from(rows), P) == []


def test_reject_when_t1_is_yang():
    rows = base_pattern()
    idx = 6
    o, h, l, c, v = rows[idx]
    rows[idx] = (o, h, l, o + 0.1, v)          # 收阳
    sigs = scan_stock("002001", "测试股", bars_from(rows), P)
    assert sigs == []


def test_reject_when_breakout_not_above_t1_high():
    # 涨停但收盘 11.50 < T+1 最高 11.80 -> 没有突破
    rows = base_pattern(breakout_close=11.50)
    rows[11] = (11.20, 11.50, 11.15, 11.50, 300000)
    sigs = scan_stock("002001", "测试股", bars_from(rows), P)
    assert all(s["state"] != "triggered" for s in sigs)


def test_reject_when_volume_not_enough():
    rows = base_pattern(breakout_volume=100000)   # 没有放量
    sigs = scan_stock("002001", "测试股", bars_from(rows), P)
    assert all(s["state"] != "triggered" for s in sigs)


def test_expired_when_window_passes():
    rows = base_pattern()
    # 砍掉突破日和买点日，窗口内再无突破
    rows = rows[:11]
    rows += [(11.30, 11.45, 11.10, 11.30, 90000)] * 10
    sigs = scan_stock("002001", "测试股", bars_from(rows), P)
    assert len(sigs) == 1
    assert sigs[0]["state"] == "expired"


def test_waiting_when_window_not_finished():
    rows = base_pattern()
    rows = rows[:10]      # 只到横盘第 3 天，窗口未走完
    sigs = scan_stock("002001", "测试股", bars_from(rows), P)
    assert len(sigs) == 1
    assert sigs[0]["state"] == "waiting"
    assert sigs[0]["days_left"] > 0


def test_first_break_without_volume_is_failed():
    """第一次涨停突破当天没放量 → 形态作废，不能只是"没触发"。"""
    rows = base_pattern()
    rows[11] = (11.50, 12.43, 11.45, 12.43, 100000)   # 涨停突破，但量比 < 1.5
    rows.append((12.60, 12.90, 12.40, 12.70, 260000))
    sigs = scan_stock("002001", "测试股", bars_from(rows), P)
    assert len(sigs) == 1
    assert sigs[0]["state"] == "failed"
    assert sigs[0]["breakout_vol_ratio"] < 1.5


def test_second_break_with_volume_is_not_a_signal():
    """第一次涨停突破无量、第二次涨停才放量 —— 这种不能算信号。"""
    rows = base_pattern()
    rows[11] = (11.50, 12.43, 11.45, 12.43, 100000)   # 第一次：涨停突破，缩量
    rows[12] = (12.60, 13.67, 12.55, 13.67, 400000)   # 第二次：再涨停，放量
    rows.append((13.80, 14.00, 13.50, 13.90, 300000))
    sigs = scan_stock("002001", "测试股", bars_from(rows), P)
    assert len(sigs) == 1
    assert sigs[0]["state"] == "failed"
    assert sigs[0]["breakout_date"] == "2026-01-16"   # 记的是第一次突破那天


def test_second_break_allowed_when_rule_off():
    """关掉严格口径后，退回旧逻辑：等到放量那天才算。"""
    rows = base_pattern()
    rows[11] = (11.50, 12.43, 11.45, 12.43, 100000)
    rows[12] = (12.60, 13.67, 12.55, 13.67, 400000)
    rows.append((13.80, 14.00, 13.50, 13.90, 300000))
    p2 = dict(P, first_break_must_volume=False)
    sigs = scan_stock("002001", "测试股", bars_from(rows), p2)
    assert len(sigs) == 1
    assert sigs[0]["state"] == "triggered"
    assert sigs[0]["breakout_date"] == "2026-01-17"
