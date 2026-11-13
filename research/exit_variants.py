"""S4 · 「涨停续持」规则的稳健性检验

三种出场口径（买点均为：突破日次日开盘）：
    V0 买入当日收盘卖
    V1 买入当日涨停才续持，否则当日收盘卖
    V2 至少持有到次日；次日涨停则续持，否则次日收盘卖
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

LBTJ = r"D:\code\demo\lbtj\data"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
COST = 0.25
MAXDT = 15


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


def first_non_zt(zt, start, n):
    for k in range(start, min(n, start + MAXDT)):
        if not zt[k]:
            return k
    return min(n - 1, start + MAXDT - 1)


def main():
    df = pd.read_parquet(os.path.join(OUT, "geyin_w20.parquet"))
    df = df[df.bo_date.notna()].copy()
    print(f"突破样本 {len(df):,}")

    recs = []
    cache = {}
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
            ib = dates.get(r.bo_date)
            if ib is None or ib + 2 >= n:
                continue
            e = o[ib + 1]
            if e <= 0:
                continue
            b = ib + 1                                   # 买入日

            # V0：当日收盘卖
            v0 = c[b] / e - 1
            # V1：当日涨停才续持
            if zt[b]:
                k = first_non_zt(zt, b + 1, n)
                v1, d1 = c[k] / e - 1, k - b + 1
            else:
                v1, d1 = c[b] / e - 1, 1
            # V2：至少持有到次日，次日涨停才续持
            if zt[b + 1]:
                k = first_non_zt(zt, b + 2, n)
                v2, d2 = c[k] / e - 1, k - b + 1
            else:
                v2, d2 = c[b + 1] / e - 1, 2

            recs.append(dict(code=r.code, year=r.t_date[:4], bo_idx=r.bo_idx,
                             v0=v0 * 100 - COST, v1=v1 * 100 - COST, v2=v2 * 100 - COST,
                             d1=d1, d2=d2, buy_zt=bool(zt[b]), next_zt=bool(zt[b + 1])))

    t = pd.DataFrame(recs)
    print(f"可成交 {len(t):,}\n")

    def rep(col, label):
        s = t[col].dropna()
        tt = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
        print(f"  {label:<26} n={len(s):5d}  期望 {s.mean():+6.2f}%  中位 {s.median():+6.2f}%  "
              f"胜率 {(s>0).mean()*100:4.1f}%  t={tt:+6.2f}")

    print("=== 三种出场口径 ===")
    rep("v0", "V0 当日收盘卖")
    rep("v1", "V1 当日涨停才续持")
    rep("v2", "V2 次日涨停才续持")

    print(f"\n  买入日涨停比例 {t.buy_zt.mean()*100:.1f}%   次日涨停比例 {t.next_zt.mean()*100:.1f}%")

    print("\n=== V2 按年份 ===")
    for y, g in t.groupby("year"):
        s = g.v2
        tt = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if len(s) > 5 else 0
        print(f"  {y}  n={len(s):4d}  期望 {s.mean():+6.2f}%  胜率 {(s>0).mean()*100:4.1f}%  t={tt:+5.2f}")

    print("\n=== V2 的收益分布（正期望是不是靠几条大鱼）===")
    s = t.v2.sort_values()
    for q in (0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99):
        print(f"  {int(q*100):>2}% 分位: {s.quantile(q):+7.2f}%")
    print(f"  最大 {s.max():+.1f}%  最小 {s.min():+.1f}%")
    pos = s[s > 0].sum(); tot = s.sum()
    print(f"  正收益合计 {pos:+.0f}%，全部净收益合计 {tot:+.0f}% → 去掉最赚的 5% 后："
          f"{s.iloc[:-int(len(s)*0.05)].mean():+.2f}%")
    print(f"  去掉最赚的 1% 后：{s.iloc[:-int(len(s)*0.01)].mean():+.2f}%")
    tr = s.clip(lower=s.quantile(.02), upper=s.quantile(.98))
    print(f"  2%/98% 缩尾后均值：{tr.mean():+.2f}%")

    print("\n=== V2 分组：次日涨停 vs 没涨停 ===")
    for flag, nm in ((True, "次日涨停(续持)"), (False, "次日没涨停(卖出)")):
        sub = t[t.next_zt == flag]
        if len(sub) < 10:
            continue
        print(f"  {nm:<16} n={len(sub):5d}  期望 {sub.v2.mean():+6.2f}%  中位 {sub.v2.median():+6.2f}%  "
              f"胜率 {(sub.v2>0).mean()*100:4.1f}%  平均持有 {sub.d2.mean():.2f} 日")

    t.to_parquet(os.path.join(OUT, "exit_variants.parquet"), index=False)
    print("\n→ out/exit_variants.parquet")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
