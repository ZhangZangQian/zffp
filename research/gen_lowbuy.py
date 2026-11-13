"""S2 · 巨阴低吸 + 突破窗口对比（一次算完 10 日 / 20 日）

收录所有「T 涨停 + T+1 高开阴线且收盘 > T 开盘」的形态（不要求后续突破），
向后观察 25 根 K 线，同时记录：
    · 巨阴收盘买入后的前向路径（D+1~D+20、MFE/MAE）
    · 第一次涨停突破发生在第几天（1~20，无则为空）
    · 突破次日开盘价（用于检验"追突破"策略）

这样 10 日 / 20 日两个窗口都能从同一份数据里切出来，口径完全一致。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

LBTJ = r"D:\code\demo\lbtj\data"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

MIN_GAP = 0.01
MIN_BODY = 0.015
MAX_WINDOW = 20          # 最多观察 20 个交易日
TAIL = 6                 # 突破日之后再取 6 根，用于"追突破"策略


def load_bars(code: str):
    p = os.path.join(LBTJ, "raw", "daily_em", f"{code}.parquet")
    if not os.path.exists(p):
        return None
    try:
        d = pd.read_parquet(p, columns=["date", "open", "high", "low", "close",
                                        "volume", "turnover", "preclose"])
    except Exception:
        return None
    d = d.dropna(subset=["open", "high", "low", "close", "preclose"])
    if len(d) < 30:
        return None
    d = d.sort_values("date").reset_index(drop=True)
    d["date"] = d["date"].astype(str).str[:10]
    return d


def scan(code: str, dates: list[str]) -> list[dict]:
    d = load_bars(code)
    if d is None:
        return []
    n = len(d)
    o, h, lo, c = (d[x].values for x in ("open", "high", "low", "close"))
    v = d["volume"].values
    pre = d["preclose"].values
    lim = (pre * 1.10).round(2)
    zt = (c >= lim - 0.005) & (c >= h - 0.005)
    idx = {x: i for i, x in enumerate(d["date"].values)}

    out = []
    for t_date in dates:
        i = idx.get(t_date)
        if i is None or i + 2 >= n:
            continue
        t, t1 = i, i + 1
        if not zt[t] or (c[t] / pre[t] - 1) * 100 < 9.4:
            continue
        if o[t1] < c[t] * (1 + MIN_GAP):
            continue
        if c[t1] >= o[t1] or c[t1] <= o[t]:
            continue
        body = (o[t1] - c[t1]) / o[t1]
        if body < MIN_BODY:
            continue
        entry = float(c[t1])
        if entry <= 0:
            continue

        # ---- 20 日内第一次涨停突破 ----
        bo = -1
        for j in range(t + 2, min(n, t + 1 + MAX_WINDOW)):
            if c[j] > h[t1] and zt[j]:
                bo = j
                break

        rec = {
            "code": code, "t_date": t_date,
            "t_open": float(o[t]), "t_close": float(c[t]),
            "t1_date": d["date"].values[t1],
            "t1_open": float(o[t1]), "t1_high": float(h[t1]),
            "t1_low": float(lo[t1]), "t1_close": float(c[t1]),
            "t1_gap": float(o[t1] / c[t] - 1), "t1_body": float(body),
            "t1_turnover": float(d["turnover"].values[t1]) if "turnover" in d else np.nan,
            "entry": entry,
            "bo_idx": int(bo - t1) if bo > 0 else np.nan,
            "bo_date": d["date"].values[bo] if bo > 0 else None,
        }

        # ---- 巨阴买入后的路径 ----
        seg_end = min(n, t1 + 1 + MAX_WINDOW)
        cs, hs, ls = c[t1 + 1:seg_end], h[t1 + 1:seg_end], lo[t1 + 1:seg_end]
        rec["path_len"] = int(len(cs))
        for k in (1, 2, 3, 5, 10, 15, 20):
            rec[f"ret_d{k}"] = float(cs[k - 1] / entry - 1) * 100 if len(cs) >= k else np.nan
        for w in (10, 20):
            if len(cs) >= 1:
                seg = slice(0, min(w, len(cs)))
                rec[f"mfe{w}"] = float(hs[seg].max() / entry - 1) * 100
                rec[f"mae{w}"] = float(ls[seg].min() / entry - 1) * 100

        # ---- 突破日 & 追突破策略 ----
        if bo > 0:
            rec["bo_close"] = float(c[bo])
            rec["bo_ret"] = float(c[bo] / entry - 1) * 100
            vma = v[max(0, bo - 5):bo].mean()
            rec["bo_vol_ratio"] = float(v[bo] / vma) if vma > 0 else np.nan
            if bo + 1 < n:
                bo_open = float(o[bo + 1])
                rec["bo_buy_open"] = bo_open
                for k in (1, 2, 3, 5):
                    jj = bo + k
                    rec[f"br_ret_d{k}"] = (float(c[jj] / bo_open - 1) * 100
                                           if jj < n and bo_open > 0 else np.nan)
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    zt = pd.read_parquet(os.path.join(LBTJ, "feat", "zt_history.parquet"))
    zt["code"] = zt["code"].astype(str).str.zfill(6)
    zt["date"] = zt["date"].astype(str).str[:10]
    cand = zt[zt["board"].isin(["sh_main", "sz_main"])
              & (~zt["is_st5"].astype(bool))
              & (zt["close"] < 40)]
    print(f"候选涨停事件 {len(cand):,}")

    groups = list(cand.groupby("code"))
    if args.limit:
        groups = groups[:args.limit]
    rows: list[dict] = []
    for k, (code, g) in enumerate(groups, 1):
        rows += scan(code, g["date"].tolist())
        if k % 800 == 0:
            print(f"  {k}/{len(groups)}  {len(rows):,}", flush=True)

    df = pd.DataFrame(rows)
    df["year"] = df["t_date"].str[:4]
    p = os.path.join(OUT, "geyin_w20.parquet")
    df.to_parquet(p, index=False)
    print(f"\n巨阴样本 {len(df):,} → {p}")
    for w in (10, 20):
        rate = (df.bo_idx <= w).mean() * 100
        print(f"  {w} 日窗口内突破率：{rate:.1f}%")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
