"""战法参数与风控参数：集中定义，可持久化到 settings 表。

设计原则：
1. 所有"会改变信号数量/风险敞口"的数字都在这里，不散落在代码里。
2. 参数存 JSON，前端可改，改完立刻生效（下一次扫描）。
"""
from __future__ import annotations

import copy
from typing import Any


# --------------------------------------------------------------------------
# 战法参数：决定"哪些票会被机器选出来"
# --------------------------------------------------------------------------
DEFAULT_STRATEGY: dict[str, Any] = {
    # ---- T 日：涨停 ----
    # 涨停判定容差（前复权价格有小数误差，按涨跌幅近似判定）
    "limit_tol": 0.005,
    # 参与的市场：main=主板 chinext=创业板 star=科创板 bj=北交所
    "boards": ["main", "chinext"],
    "exclude_st": True,
    # ---- T+1：高开低走阴线，但收盘价高于 T 日开盘价 ----
    "min_gap_pct": 0.010,        # 高开幅度下限（相对 T 日收盘）
    "min_body_pct": 0.015,       # 阴线实体下限（"巨阴"，(开-收)/开）
    "require_close_above_t_open": True,   # 核心规则：收盘价 > T 日开盘价
    # ---- 突破：未来 N 个交易日内，放量涨停突破 T+1 最高价 ----
    "window": 10,
    "vol_ma_days": 5,            # 放量的对比基准：前 N 日均量
    "vol_ratio": 1.5,            # 放量倍数
    # 必须是"第一次涨停突破"当天放量才算；
    # 第一次突破无量就已经把形态消耗掉了，后面再放量涨停不算信号
    "first_break_must_volume": True,
    # ---- 买点：突破日次日 ----
    "max_chase_pct": 0.08,       # 相对突破日收盘价，超过就算追高
}

# --------------------------------------------------------------------------
# 风控参数：决定"你能不能下单"（纪律闸门）
# --------------------------------------------------------------------------
DEFAULT_RISK: dict[str, Any] = {
    "total_capital": 200000.0,      # 账户总资金
    "risk_per_trade_pct": 0.02,     # 单笔最大亏损占总资金 2%
    "max_position_pct": 0.30,       # 单票最大仓位 30%
    "max_stop_pct": 0.08,           # 止损幅度上限 8%（止损太远 = 不敢执行）
    "min_rr": 2.0,                  # 盈亏比下限 2:1，不到不许开仓
    "max_plans_per_day": 3,         # 每日最多 3 个计划
    "max_new_positions": 3,         # 同时最多持有 3 只
    "daily_stop_loss_pct": 0.03,    # 当日亏损达 3% 停止开仓
    "loss_streak_cooldown": 3,      # 连亏 3 笔
    "cooldown_days": 3,             # 冷静 3 个交易日
    "max_hold_days": 5,             # 持有超过 5 日未达目标 → 强制复盘
    # 允许的滑点：实际成交价高于计划上限即视为破戒
    "slippage_tol": 0.002,
}

# 复盘时必须选择"错在哪"的标签（用于统计你的高频错误）
MISTAKE_TAGS = [
    "无计划临时起意",
    "追高买入",
    "超仓位",
    "不止损/扛单",
    "提前止盈",
    "该止损没止损",
    "信号过期才买",
    "频繁交易",
    "情绪化交易",
    "看盘口冲动",
    "听消息/跟票",
    "该重仓没重仓",
]

EMOTIONS = ["平静", "贪婪", "恐惧", "急躁", "侥幸", "报复心", "犹豫", "自信"]

# --------------------------------------------------------------------------
# 副图指标参数（只影响看图，不影响选股）
# --------------------------------------------------------------------------
INDICATOR_PARAMS: dict[str, Any] = {
    # MACD：快线 EMA、慢线 EMA、信号线 EMA
    "macd": {"fast": 10, "slow": 20, "signal": 7},
    # KDJ：RSV 周期、K 平滑、D 平滑
    "kdj": {"n": 9, "m1": 3, "m2": 3},
    # RSI：三条周期
    "rsi": [6, 12, 24],
}


def macd_label() -> str:
    p = INDICATOR_PARAMS["macd"]
    return f"MACD({p['fast']},{p['slow']},{p['signal']})"


def kdj_label() -> str:
    p = INDICATOR_PARAMS["kdj"]
    return f"KDJ({p['n']},{p['m1']},{p['m2']})"


def rsi_label() -> str:
    return f"RSI({','.join(str(n) for n in INDICATOR_PARAMS['rsi'])})"


def merge(defaults: dict, override: dict | None) -> dict:
    """用 override 覆盖 defaults，保证新增参数在旧配置里也有默认值。"""
    out = copy.deepcopy(defaults)
    if not override:
        return out
    for k, v in override.items():
        if k in out:
            base = out[k]
            if isinstance(base, float) and isinstance(v, (int, float)):
                out[k] = float(v)
            elif isinstance(base, int) and not isinstance(base, bool) and isinstance(v, (int, float)):
                out[k] = int(v)
            elif isinstance(base, bool):
                out[k] = bool(v)
            else:
                out[k] = v
    return out
