"""S3 · 「涨停续持，不涨停就走」规则检验

规则定义（两个买点分别测）：
    A. 巨阴低吸：T+1 收盘买入
       次日（D+1）收盘若涨停 → 继续持有；否则当日收盘卖出。
       之后每天同理，直到某个非涨停日收盘卖出。
    B. 追突破：突破日次日开盘买入
       买入次日收盘若涨停 → 继续持有；否则卖出。

对照基准：固定持有 1 天 / 3 天的收益。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

LBTJ = r"D:\code\demo\lbtj\data"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
COST = 0.25
MAXDT = 20


def load_bars(code):
    p = os.path.join(LBTJ, "raw", "daily_em", f"{code}.parquet")
    if not os.path.exists(p):
        return None
    try:
        d = pd.read_parquet(p, columns=["date", "open", "high", "low", "close", "preclose"])
    except Exception:
        return None
    d = d.dropna(subset=["open", "high", "low", "close", "preclose"])
    if len(d) < 30:
        return None
    d = d.sort_values("date").reset_index(drop=True)
    d["date"] = d["date"].astype(str).str[:10]
    lim = (d["preclose"] * 1.10).round(2)
    d["zt"] = ((d["close"] >= lim - 0.005) & (d["close"] >= d["high"] - 0.005)).values
    return d


def walk_exit(close, zt, start, maxdt=MAXDT):
    """从 start 开始找第一个非涨停日，返回 (卖出序号, 持有天数)。
    start 是第一个可卖日的下标（>=1 相对买入）。"""
    for k in range(start, min(len(close), start + maxdt)):
        if not zt[k]:
            return k, k - start + 1
    k = min(len(close) - 1, start + maxdt - 1)
    return k, k - start + 1


def main():
    df = pd.read_parquet(os.path.join(OUT, "geyin_w20.parquet"))
    print(f"巨阴样本 {len(df):,}")

    cache = {}
    A_ret, A_days = [], []          # 低吸 + 不涨停就走
    A_d1, A_d3 = [], []             # 对照：固定持有
    B_ret, B_days = [], []          # 追突破 + 不涨停就走
    B_d1, B_d3 = [], []
    B_sameday = []                  # 追突破：当天不涨停就卖

    for code, g in df.groupby("code"):
        d = cache.get(code)
        if d is None:
            d = load_bars(code)
            cache[code] = d
        if d is None:
            continue
        dates = {v: i for i, v in enumerate(d["date"].values)}
        o, c, zt = d["open"].values, d["close"].values, d["zt"].values
        n = len(d)

        for r in g.itertuples(index=False):
            i1 = dates.get(r.t1_date)
            if i1 is not None and i1 + 1 < n:
                entry = r.entry
                # A：次日不涨停就卖
                k, days = walk_exit(c, zt, i1 + 1)
                A_ret.append((c[k] / entry - 1) * 100 - COST)
                A_days.append(days)
                A_d1.append((c[i1 + 1] / entry - 1) * 100 - COST)
                if i1 + 3 < n:
                    A_d3.append((c[i1 + 3] / entry - 1) * 100 - COST)

            # B：突破日次日开盘买
            if r.bo_date is not None and not pd.isna(r.bo_idx):
                ib = dates.get(r.bo_date)
                if ib is None or ib + 2 >= n:
                    continue
                e = o[ib + 1]
                if e <= 0:
                    continue
                # 买入当天收盘涨停 → 续持；否则当天收盘卖（第二种口径）
                if zt[ib + 1]:
                    k, days = walk_exit(c, zt, ib + 2)
                    B_ret.append((c[k] / e - 1) * 100 - COST)
                    B_days.append(days + 1)
                else:
                    B_ret.append((c[ib + 1] / e - 1) * 100 - COST)
                    B_days.append(1)
                B_sameday.append((c[ib + 1] / e - 1) * 100 - COST)
                B_d1.append((c[ib + 2] / e - 1) * 100 - COST)
                if ib + 4 < n:
                    B_d3.append((c[ib + 4] / e - 1) * 100 - COST)

    def rep(label, arr, days=None):
        s = pd.Series(arr).dropna()
        if len(s) < 20:
            print(f"  {label}: 样本不足")
            return
        t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
        extra = f"  平均持有 {np.mean(days):.2f} 日" if days else ""
        print(f"  {label:<28} n={len(s):5d}  期望 {s.mean():+6.2f}%  "
              f"中位 {s.median():+6.2f}%  胜率 {(s>0).mean()*100:4.1f}%  t={t:+6.2f}{extra}")

    print("\n=== A. 巨阴低吸（T+1 收盘买入）===")
    rep("不涨停就走（移动出场）", A_ret, A_days)
    rep("对照：固定持有 1 天", A_d1)
    rep("对照：固定持有 3 天", A_d3)

    print("\n=== B. 追突破（突破日次日开盘买入）===")
    rep("不涨停就走（移动出场）", B_ret, B_days)
    rep("对照：买入当日收盘卖", B_sameday)
    rep("对照：固定持有 1 天", B_d1)
    rep("对照：固定持有 3 天", B_d3)

    s = pd.Series(B_days)
    print(f"\n  追突破持有天数分布：1日 {(s==1).mean()*100:.1f}%  "
          f"2日 {(s==2).mean()*100:.1f}%  3日+ {(s>=3).mean()*100:.1f}%  最长 {s.max():.0f}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
