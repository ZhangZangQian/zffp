"""S1 · 巨阴突破形态样本生成

口径（已与需求确认）：
    股票池：沪深主板（60/00），剔除 ST，T 日收盘价 < 40
    T   日：涨停（用 lbtj 的 zt_history，已按制度事件表算好涨停价）
    T+1 日：高开 >= 1%，收阴，收盘价 > T 日开盘价，阴线实体 >= 1.5%
    突破  ：T+1 之后 10 个交易日内，第一次「涨停 + 收盘 > T+1 最高价」
    买点  ：突破日的下一个交易日

**刻意不按放量过滤**：量比作为一个列存下来，让后续扫描去判断多少算放量。

输出：research/out/signals_main_board.parquet
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

LBTJ = r"D:\code\demo\lbtj\data"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# 形态参数（研究起点，不是结论）
MIN_GAP = 0.01
MIN_BODY = 0.015
WINDOW = 10
VOL_MA_DAYS = 5
HORIZONS = (1, 2, 3, 5)


def load_bars(code: str) -> pd.DataFrame | None:
    p = os.path.join(LBTJ, "raw", "daily_em", f"{code}.parquet")
    if not os.path.exists(p):
        return None
    try:
        d = pd.read_parquet(p, columns=["date", "open", "high", "low", "close",
                                        "volume", "amount", "turnover", "preclose"])
    except Exception:
        return None
    d = d.dropna(subset=["open", "high", "low", "close", "preclose"])
    if len(d) < 30:
        return None
    d = d.sort_values("date").reset_index(drop=True)
    d["date"] = d["date"].astype(str).str[:10]
    return d


def limit_flags(d: pd.DataFrame) -> np.ndarray:
    """主板 10% 涨停（已剔除 ST）：收盘价打到涨停价，且以最高价收盘。"""
    lim = (d["preclose"] * 1.10).round(2)
    return ((d["close"] >= lim - 0.005) & (d["close"] >= d["high"] - 0.005)).values


def scan_stock(code: str, t_dates: list[str], meta: dict) -> list[dict]:
    d = load_bars(code)
    if d is None:
        return []
    n = len(d)
    o = d["open"].values
    h = d["high"].values
    lo = d["low"].values
    c = d["close"].values
    v = d["volume"].values
    zt = limit_flags(d)
    idx = {dt: i for i, dt in enumerate(d["date"].values)}

    out = []
    for t_date in t_dates:
        i = idx.get(t_date)
        if i is None or i + 1 >= n or i + 2 >= n:
            continue
        t, t1 = i, i + 1
        if not zt[t]:
            continue
        # 二次校验：沪深主板 10% 板，涨幅必须接近 10%（挡掉漏网的 5% ST 板）
        t_pct = float(c[t] / d["preclose"].values[t] - 1) * 100
        if t_pct < 9.4:
            continue
        # ---- T+1 巨阴 ----
        if o[t1] < c[t] * (1 + MIN_GAP):
            continue
        if c[t1] >= o[t1]:
            continue
        if c[t1] <= o[t]:
            continue
        body = (o[t1] - c[t1]) / o[t1] if o[t1] > 0 else 0.0
        if body < MIN_BODY:
            continue

        # ---- 第一次涨停突破 ----
        hit = -1
        for j in range(t + 2, min(n, t + 2 + WINDOW)):
            if c[j] <= h[t1]:
                continue
            if not zt[j]:
                continue
            hit = j
            break
        if hit < 0:
            continue                       # 窗口内没有突破，不入样本
        if hit + 1 >= n:
            continue                       # 突破日之后还没数据（最新样本）

        base = v[max(0, hit - VOL_MA_DAYS):hit]
        vma = base[base > 0].mean() if len(base) else 0.0
        vol_ratio = float(v[hit] / vma) if vma > 0 else 0.0

        b = hit + 1                        # 买点
        entry = float(o[b])
        if entry <= 0:
            continue
        lim_b = round(float(d["preclose"].values[b]) * 1.10, 2)
        one_word = bool(zt[b] and abs(o[b] - h[b]) < 1e-6 and abs(h[b] - lo[b]) < 1e-6)
        open_at_limit = bool(o[b] >= lim_b - 0.005)

        rec = {
            "code": code,
            "t_date": t_date,
            "t_open": float(o[t]), "t_close": float(c[t]), "t_pct": t_pct,
            "t_lianban": meta.get("lianban"),
            "t_float_mv": meta.get("float_mv"),
            "t_turnover": float(d["turnover"].values[t]) if "turnover" in d else np.nan,
            "t1_date": d["date"].values[t1],
            "t1_open": float(o[t1]), "t1_high": float(h[t1]),
            "t1_low": float(lo[t1]), "t1_close": float(c[t1]),
            "t1_gap": float(o[t1] / c[t] - 1),
            "t1_body": float(body),
            "t1_amp": float((h[t1] - lo[t1]) / c[t] * 100),
            "breakout_date": d["date"].values[hit],
            "breakout_close": float(c[hit]),
            "breakout_vol_ratio": vol_ratio,
            "breakout_pct": float(c[hit] / c[hit - 1] - 1) * 100,
            "days_to_breakout": hit - t1,
            "buy_date": d["date"].values[b],
            "buy_open": entry,
            "unbuyable_yizi": one_word,
            "open_at_limit": open_at_limit,
            "t_close_price": float(c[t]),
        }

        # ---- 前向收益（以次日开盘价为成本）----
        closes = c[b:min(n, b + max(HORIZONS) + 1)]
        highs = h[b:min(n, b + max(HORIZONS) + 1)]
        lows = lo[b:min(n, b + max(HORIZONS) + 1)]
        rec["ret_d0"] = float(closes[0] / entry - 1) * 100
        for k in HORIZONS:
            rec[f"ret_d{k}"] = float(closes[k] / entry - 1) * 100 if len(closes) > k else np.nan
        rec["mfe5"] = float(highs.max() / entry - 1) * 100
        rec["mae5"] = float(lows.min() / entry - 1) * 100
        rec["bars_after"] = int(len(closes) - 1)
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试）")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    zt = pd.read_parquet(os.path.join(LBTJ, "feat", "zt_history.parquet"))
    zt["code"] = zt["code"].astype(str).str.zfill(6)
    zt["date"] = zt["date"].astype(str).str[:10]

    cand = zt[zt["board"].isin(["sh_main", "sz_main"])
              & (~zt["is_st5"].astype(bool))
              & (zt["close"] < 40)].copy()
    print(f"涨停事件 {len(zt):,} → 沪深主板 + 非ST + 股价<40：{len(cand):,}")

    groups = list(cand.groupby("code"))
    if args.limit:
        groups = groups[:args.limit]
    print(f"涉及个股 {len(groups):,} 只，开始扫描…")

    rows: list[dict] = []
    for k, (code, g) in enumerate(groups, 1):
        meta = g.iloc[0].to_dict()
        rows += scan_stock(code, g["date"].tolist(), meta)
        if k % 500 == 0:
            print(f"  {k}/{len(groups)}  已命中 {len(rows):,}", flush=True)

    df = pd.DataFrame(rows)
    if df.empty:
        print("没有命中样本")
        return
    df["year"] = df["t_date"].str[:4]
    path = os.path.join(OUT, "signals_main_board.parquet")
    df.to_parquet(path, index=False)
    print(f"\n样本 {len(df):,} 条 → {path}")
    print("\n按年份：")
    print(df.groupby("year").size().to_string())
    print("\n可成交性：一字 %.1f%%，开盘即涨停 %.1f%%" % (
        df.unbuyable_yizi.mean() * 100, df.open_at_limit.mean() * 100))
    print("\n收益（未扣成本，单位 %）：")
    for k in (0, 1, 2, 3, 5):
        s = df[f"ret_d{k}"].dropna()
        print(f"  D+{k}: 均值 {s.mean():+.2f}  中位 {s.median():+.2f}  胜率 {(s>0).mean()*100:.1f}%  n={len(s):,}")
    print("\n量比分布：", df.breakout_vol_ratio.describe(percentiles=[.1,.25,.5,.75,.9]).round(2).to_dict())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
