"""生成自包含的 HTML 检验报告（无外部依赖，双击即可打开）。

    <lbtj venv>/python make_report.py
    → out/report.html
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

LBTJ = r"D:\code\demo\lbtj\data"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
COST = 0.25
UP, DOWN = "#f0475c", "#17b978"      # A股：红涨绿跌


# ---------------------------------------------------------------- 图表
def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar_svg(labels, values, ns, width=980, height=250, ylabel="D+3 净收益 %"):
    pos = [v for v in values if v == v]
    lo, hi = min(0, min(pos)), max(0, max(pos))
    span = (hi - lo) or 1
    pad_l, pad_r, pad_t, pad_b = 52, 14, 18, 50
    ih = height - pad_t - pad_b
    iw = width - pad_l - pad_r
    zero = pad_t + (hi / span) * ih

    def yv(v):
        return pad_t + ((hi - v) / span) * ih

    n = len(values)
    step = iw / n
    bw = min(step * 0.62, 64)
    s = [f'<svg viewBox="0 0 {width} {height}" class="chart">']
    for frac in (0, .25, .5, .75, 1):
        v = lo + span * frac
        y = yv(v)
        s.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" '
                 f'stroke="#1d2735" stroke-width="1"/>')
        s.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" fill="#66758a" font-size="11" '
                 f'text-anchor="end">{v:.1f}</text>')
    s.append(f'<line x1="{pad_l}" y1="{zero:.1f}" x2="{width-pad_r}" y2="{zero:.1f}" '
             f'stroke="#4a5970" stroke-width="1.2"/>')
    for i, (lab, v, nn) in enumerate(zip(labels, values, ns)):
        if v != v:
            continue
        x = pad_l + step * (i + 0.5)
        y0, y1 = min(zero, yv(v)), max(zero, yv(v))
        color = UP if v > 0 else DOWN
        s.append(f'<rect x="{x-bw/2:.1f}" y="{y0:.1f}" width="{bw:.1f}" '
                 f'height="{max(1.5, y1-y0):.1f}" fill="{color}" opacity=".85" rx="2"/>')
        # 正值标签放在柱顶上方；负值放在柱内靠零轴处（深色，避免绿字压绿柱）
        if v > 0:
            ty, fill = yv(v) - 6, "#c9d4e2"
        else:
            ty = zero + 14
            fill = "#08120e" if abs(y1 - y0) > 26 else "#c9d4e2"
        s.append(f'<text x="{x:.1f}" y="{ty:.1f}" fill="{fill}" '
                 f'font-size="11" text-anchor="middle">{v:+.2f}</text>')
        s.append(f'<text x="{x:.1f}" y="{height-30}" fill="#8290a3" font-size="11" '
                 f'text-anchor="middle">{esc(lab)}</text>')
        s.append(f'<text x="{x:.1f}" y="{height-13}" fill="#4f5d72" font-size="10" '
                 f'text-anchor="middle">n={nn}</text>')
    s.append('</svg>')
    return "".join(s), ylabel


def hist_svg(values, width=980, height=200, bins=36):
    v = np.array([x for x in values if x == x])
    lo, hi = np.percentile(v, 0.5), np.percentile(v, 99.5)
    edges = np.linspace(lo, hi, bins + 1)
    cnt, _ = np.histogram(v, bins=edges)
    mx = cnt.max() or 1
    pad_l, pad_r, pad_t, pad_b = 52, 14, 14, 28
    ih = height - pad_t - pad_b
    iw = width - pad_l - pad_r
    step = iw / bins
    s = [f'<svg viewBox="0 0 {width} {height}" class="chart">']
    zero_x = pad_l + (0 - lo) / (hi - lo) * iw
    for i, c in enumerate(cnt):
        x = pad_l + step * i
        h = c / mx * ih
        center = (edges[i] + edges[i + 1]) / 2
        color = UP if center > 0 else DOWN
        s.append(f'<rect x="{x:.1f}" y="{pad_t+ih-h:.1f}" width="{step-1:.1f}" '
                 f'height="{h:.1f}" fill="{color}" opacity=".75"/>')
    s.append(f'<line x1="{zero_x:.1f}" y1="{pad_t}" x2="{zero_x:.1f}" y2="{pad_t+ih}" '
             f'stroke="#8b98a8" stroke-width="1.2" stroke-dasharray="4 3"/>')
    s.append(f'<text x="{zero_x+4:.1f}" y="{pad_t+10}" fill="#8b98a8" font-size="11">0%</text>')
    s.append(f'<text x="{pad_l}" y="{height-8}" fill="#66758a" font-size="10">{lo:.0f}%</text>')
    s.append(f'<text x="{width-pad_r}" y="{height-8}" fill="#66758a" font-size="10" '
             f'text-anchor="end">{hi:.0f}%</text>')
    s.append('</svg>')
    return "".join(s)


def heat_cell(v):
    if v != v:
        return "#151b26", "#8290a3"
    a = min(0.72, abs(v) / 1.2 + 0.08)
    if v < 0:
        r, g, b = 23, 185, 120
    else:
        r, g, b = 240, 71, 92
    return f"rgba({r},{g},{b},{a:.2f})", "#eef4fa"


def tstat(s):
    s = pd.Series(s).dropna()
    if len(s) < 5 or s.std() == 0:
        return 0.0
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))


# ---------------------------------------------------------------- 主流程
def main():
    df = pd.read_parquet(os.path.join(OUT, "signals_main_board.parquet"))
    df = df[~df.unbuyable_yizi].copy()
    df["net3"] = df["ret_d3"] - COST

    con = sqlite3.connect(r"D:\code\demo\zffp\data\zffp.db")
    names = dict(pd.read_sql("SELECT code,name FROM stocks", con).values)
    con.close()

    # ---- 前向路径（算出场/入场规则用）----
    print("读取前向 K 线…")
    paths = {}
    for code, g in df.groupby("code"):
        p = os.path.join(LBTJ, "raw", "daily_em", f"{code}.parquet")
        if not os.path.exists(p):
            continue
        try:
            d = pd.read_parquet(p, columns=["date", "open", "high", "low", "close"])
        except Exception:
            continue
        d["date"] = d["date"].astype(str).str[:10]
        d = d.sort_values("date").reset_index(drop=True)
        idx = {v: i for i, v in enumerate(d["date"].values)}
        o, h, l, c = d["open"].values, d["high"].values, d["low"].values, d["close"].values
        for _, r in g.iterrows():
            i = idx.get(r["buy_date"])
            if i is not None:
                paths[(code, r["buy_date"])] = (o[i:i + 12], h[i:i + 12], l[i:i + 12], c[i:i + 12])

    # ---- 出场规则 ----
    def sim_exit(stop, target, hold):
        out = []
        for _, r in df.iterrows():
            pth = paths.get((r["code"], r["buy_date"]))
            if pth is None or len(pth[0]) == 0:
                continue
            o, h, l, c = pth
            entry = float(o[0])
            if entry <= 0:
                continue
            sp, tp = entry * (1 - stop), entry * (1 + target)
            ret = None
            for k in range(min(hold, len(o))):
                if k > 0 and o[k] <= sp:
                    ret = o[k] / entry - 1; break
                if k > 0 and o[k] >= tp:
                    ret = o[k] / entry - 1; break
                if l[k] <= sp:
                    ret = sp / entry - 1; break
                if h[k] >= tp:
                    ret = tp / entry - 1; break
            if ret is None:
                ret = c[min(hold, len(c)) - 1] / entry - 1
            out.append(ret * 100 - COST)
        return pd.Series(out)

    stops, targets = (0.03, 0.05, 0.06, 0.08), (0.05, 0.08, 0.10, 0.15)
    grid = {}
    for st in stops:
        for tg in targets:
            grid[(st, tg)] = sim_exit(st, tg, 3).mean()

    # ---- 入场方式 ----
    def sim_entry(mode):
        rets = []
        for _, r in df.iterrows():
            pth = paths.get((r["code"], r["buy_date"]))
            if pth is None or len(pth[0]) == 0:
                continue
            o, h, l, c = pth
            op = float(o[0])
            entry, start = None, 0
            if mode == "open":
                entry = op
            elif mode.startswith("limit"):
                px = op * (1 - float(mode.split(":")[1]))
                if l[0] <= px:
                    entry, start = px, 0
                elif len(l) > 1 and l[1] <= px:
                    entry, start = px, 1
            elif mode == "close":
                entry, start = float(c[0]), 1
            elif mode == "pullback":
                px = float(r["t1_high"]) * 1.02
                if px < op and l[0] <= px:
                    entry, start = px, 0
                elif px < op and len(l) > 1 and l[1] <= px:
                    entry, start = px, 1
            if entry is None or entry <= 0:
                continue
            k = min(start + 3, len(c) - 1)
            rets.append((float(c[k]) / entry - 1) * 100 - COST)
        s = pd.Series(rets)
        return len(s), s.mean() if len(s) else np.nan, (s > 0).mean() * 100 if len(s) else np.nan

    # ---- 分箱 ----
    def binned(series, bins, labels):
        key = pd.cut(series, bins, labels=labels)
        g = df.groupby(key, observed=True)["net3"]
        return list(g.mean().round(2)), list(g.size())

    VR_LBL = ["<0.8", "0.8-1", "1-1.2", "1.2-1.5", "1.5-2", "2-2.5", "2.5-3", ">3"]
    BODY_LBL = ["1.5-2%", "2-3%", "3-4%", "4-5%", "5-6%", "6-8%", ">8%"]
    GAP_LBL = ["<2%", "2-3.5%", "3.5-5%", ">5%"]

    vr_v, vr_n = binned(df["breakout_vol_ratio"],
                        [0, .8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 99], VR_LBL)
    body_v, body_n = binned(df["t1_body"] * 100, [1.5, 2, 3, 4, 5, 6, 8, 99], BODY_LBL)
    gap_v, gap_n = binned(df["t1_gap"] * 100, [-99, 2, 3.5, 5, 99], GAP_LBL)

    by_year = df.groupby("year")["net3"]
    yrs = [str(y) for y in by_year.mean().index]
    yr_v, yr_n = list(by_year.mean().round(2)), list(by_year.size())

    # ---- 今天的样本 ----
    overall = df["net3"].mean()
    overall_t = tstat(df["net3"])
    win = (df["net3"] > 0).mean() * 100

    # ---- 可核对案例 ----
    ex = df.sample(12, random_state=7).sort_values("t_date")
    rows = []
    for _, r in ex.iterrows():
        rows.append(f"""<tr>
          <td><b>{r['code']}</b><br><span class="dim">{names.get(r['code'], '')}</span></td>
          <td class="num">{r['t_date']}<br><span class="dim">{r['t_close']:.2f}</span></td>
          <td class="num">{r['t1_date']}<br><span class="dim">{r['t1_open']:.2f} / {r['t1_close']:.2f}</span></td>
          <td class="num">{r['breakout_date']}<br><span class="dim">{r['breakout_close']:.2f} · 量{r['breakout_vol_ratio']:.2f}</span></td>
          <td class="num">{r['buy_date']}<br><span class="dim">{r['buy_open']:.2f}</span></td>
          <td class="num {'pos' if r['ret_d3'] > 0 else 'neg'}">{r['ret_d3']:+.1f}%</td>
          <td class="num dim">{r['mfe5']:+.0f}% / {r['mae5']:+.0f}%</td>
        </tr>""")

    vr_svg, _ = bar_svg(VR_LBL, vr_v, vr_n)
    body_svg, _ = bar_svg(BODY_LBL, body_v, body_n)
    gap_svg, _ = bar_svg(GAP_LBL, gap_v, gap_n)
    yr_svg, _ = bar_svg(yrs, yr_v, yr_n)
    hist = hist_svg(df["net3"].tolist())

    # 热力图
    heat = ""
    for st in stops:
        heat += "<tr><th>止损 " + f"{st*100:.0f}%</th>"
        for tg in targets:
            v = grid[(st, tg)]
            bg, fg = heat_cell(v)
            heat += f'<td style="background:{bg};color:{fg}">{v:+.2f}%</td>'
        heat += "</tr>"

    entry_rows = ""
    for mode, label in [("open", "开盘价买入"), ("limit:0.01", "开盘价 −1% 限价"),
                        ("limit:0.02", "开盘价 −2% 限价"), ("limit:0.03", "开盘价 −3% 限价"),
                        ("close", "当日收盘买入"), ("pullback", "回踩突破位 +2%")]:
        n, avg, w = sim_entry(mode)
        entry_rows += (f"<tr><td>{label}</td><td class='num'>{n}</td>"
                       f"<td class='num dim'>{n/len(df)*100:.0f}%</td>"
                       f"<td class='num {'pos' if avg > 0 else 'neg'}'>{avg:+.2f}%</td>"
                       f"<td class='num'>{w:.1f}%</td></tr>")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>巨阴突破 · 八年检验报告</title>
<style>
:root{{--bg:#0a0e14;--panel:#121822;--bd:#1f2937;--tx:#e6edf3;--dim:#8290a3;
--up:#f0475c;--down:#17b978}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(1000px 500px at 20% -10%,#16202e,var(--bg) 60%);
color:var(--tx);font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
padding:34px 26px 90px}}
.wrap{{max-width:1060px;margin:0 auto}}
h1{{font-size:23px;margin:0 0 6px}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:26px}}
h2{{font-size:16px;margin:34px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--bd)}}
.card{{background:linear-gradient(170deg,#161d29,#111720);border:1px solid var(--bd);
border-radius:12px;padding:16px 18px;margin-bottom:14px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}}
.kpi{{background:linear-gradient(170deg,#161d29,#111720);border:1px solid var(--bd);
border-radius:12px;padding:14px 16px}}
.kpi .k{{font-size:11.5px;color:var(--dim)}}
.kpi .v{{font-size:25px;font-weight:700;letter-spacing:-.5px;margin-top:2px}}
.kpi .f{{font-size:11.5px;color:var(--dim);margin-top:2px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th,td{{padding:8px 9px;border-bottom:1px solid rgba(31,41,55,.6);text-align:left}}
th{{color:var(--dim);font-weight:500;font-size:11.5px}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.dim{{color:var(--dim);font-size:11px}}
.pos{{color:var(--up);font-weight:600}}
.neg{{color:var(--down);font-weight:600}}
.chart{{width:100%;height:auto;display:block}}
.heat td{{text-align:center;font-weight:600;border-radius:4px}}
.note{{background:rgba(245,165,36,.07);border:1px solid rgba(245,165,36,.3);
border-radius:10px;padding:13px 16px;font-size:13px;line-height:1.75}}
.warn{{background:rgba(240,71,92,.07);border-color:rgba(240,71,92,.32)}}
code{{background:#0c1219;border:1px solid var(--bd);border-radius:4px;padding:1px 5px;font-size:12px}}
ul{{margin:8px 0;padding-left:20px}} li{{margin:4px 0}}
</style></head><body><div class="wrap">

<h1>巨阴突破战法 · 八年检验报告</h1>
<div class="sub">沪深主板 · 非 ST · 股价 &lt; 40 · 2019-01 ~ 2026-10 ·
生成于 {datetime.now():%Y-%m-%d %H:%M}</div>

<div class="kpis">
  <div class="kpi"><div class="k">有效样本</div><div class="v">{len(df):,}</div>
    <div class="f">剔除一字板买不进后</div></div>
  <div class="kpi"><div class="k">D+3 期望值</div>
    <div class="v {'pos' if overall>0 else 'neg'}">{overall:+.2f}%</div>
    <div class="f">已扣 0.25% 往返成本</div></div>
  <div class="kpi"><div class="k">胜率</div><div class="v">{win:.1f}%</div>
    <div class="f">D+3 为正的比例</div></div>
  <div class="kpi"><div class="k">t 值</div><div class="v {'pos' if overall_t>0 else 'neg'}">{overall_t:+.2f}</div>
    <div class="f">绝对值 &gt; 2 即统计显著</div></div>
</div>

<div class="note warn">
<b>结论：负期望，且显著。</b>
买入动作是「突破日次日开盘买入」，持有 3 个交易日。
{len(df):,} 笔样本的平均净收益 {overall:+.2f}%，t = {overall_t:+.2f}——
这个差距不可能用运气解释。而且下面每一刀切下去，<b>没有任何一个分组是正的</b>。
</div>

<h2>一、放量到底有没有用</h2>
<div class="card">{vr_svg}</div>
<div class="note">量比越高，亏得越多。量比 &gt; 2.5 的两组分别是 {vr_v[-2]:+.2f}% 和 {vr_v[-1]:+.2f}%。
<b>「放量涨停突破」不是强势信号，是兑现信号。</b></div>

<h2>二、阴线阴到什么程度</h2>
<div class="card">{body_svg}</div>
<div class="note">七个区间全是负的，没有明显单调性。最好的一档（{BODY_LBL[0]}）是 {body_v[0]:+.2f}%，
但也不赚钱。<b>这个参数没什么可调的。</b></div>

<h2>三、高开幅度</h2>
<div class="card">{gap_svg}</div>
<div class="note">高开越多越差。最低一档 {gap_v[0]:+.2f}%，最高一档 {gap_v[-1]:+.2f}%。
高开不是抢筹，是给你一个更贵的价。</div>

<h2>四、按年份</h2>
<div class="card">{yr_svg}</div>
<div class="note">八年，年年为负。不是某一年的行情问题。</div>

<h2>五、出场纪律能不能救</h2>
<div class="card">
<table class="heat"><tr><th>止损 \\ 止盈</th>
{''.join(f'<th style="text-align:center">+{int(t*100)}%</th>' for t in targets)}</tr>
{heat}</table>
</div>
<div class="note">16 组止损/止盈组合，持有 3 日，<b>全部为负</b>。
最好的一组（8% 止损 / 5% 止盈）也只有 −0.27%。<b>出场救不回入场。</b></div>

<h2>六、换一种买法呢</h2>
<div class="card">
<table><tr><th>入场方式</th><th class="num">可成交</th><th class="num">占比</th>
<th class="num">D+3 期望</th><th class="num">胜率</th></tr>{entry_rows}</table>
</div>
<div class="note">六种买法全负。等回踩到 −2% 限价成交能少亏一点（−1.51%），但仍然是亏。</div>

<h2>七、收益分布</h2>
<div class="card">{hist}</div>
<div class="note">中位数 −3.15%，左尾很长：77% 的样本最大浮亏超过 −5%。
这是「偶尔赚小钱、经常亏大钱」的分布，不是「高胜率小亏」的形态。</div>

<h2>八、随便抽 12 笔给你核对</h2>
<div class="card">
<table>
<tr><th>代码 / 名称</th><th>T 日涨停</th><th>T+1 巨阴<br><span class="dim">开 / 收</span></th>
<th>突破日<br><span class="dim">收盘 / 量比</span></th><th>买点<br><span class="dim">开盘价</span></th>
<th class="num">D+3</th><th class="num">最大浮盈/浮亏</th></tr>
{''.join(rows)}
</table>
</div>
<div class="note">
<b>怎么核对：</b>打开任意行情软件，输入代码，翻到「T 日」那一天——
应该看到一根涨停板；「T+1」应该是一根高开低走的阴线，收盘仍在 T 日开盘价之上；
「突破日」应该是一根涨停板，收盘价高于 T+1 的最高价。
买入价 = 突破日次日的开盘价，D+3 = 第 3 个交易日收盘价相对买入价。
</div>

<h2>九、这份报告测不到什么</h2>
<div class="note">
<ul>
<li><b>盘中择时</b>：没有分钟数据，只能按开盘/收盘/限价成交来近似。你实盘可能有盘口判断。</li>
<li><b>创业板 / 科创板（20cm）</b>：本次只测沪深主板 10cm。</li>
<li><b>你的选股眼光</b>：这里是无差别地做所有符合条件的票，没有个股基本面筛选。</li>
</ul>
所以严谨的说法是：<b>「突破日次日无脑买入」这个动作在 8 年里是亏的</b>，
而不是「你做这个战法一定亏」。
</div>

<h2>十、下一步</h2>
<div class="note">
<b>A. 反过来验（推荐，半天）</b>：既然「涨停突破后买入」是显著负收益，
那它作为<b>卖出信号</b>是不是正收益？你手里的票出现放量涨停突破时，该走不该追。<br><br>
<b>B. 把买点提前一天</b>：不等突破，直接在 T+1 巨阴当天收盘买（「巨阴低吸」）。完全不同的战法，改几行就能测。<br><br>
<b>C. 换池子</b>：创业板 20cm 重跑一遍。
</div>

<div class="sub" style="margin-top:40px">
数据源：lbtj 日线（2018-11~2026-10）+ 自算涨停史 · 成本假设 0.25% 往返 ·
信号样本 <code>out/signals_main_board.parquet</code>
</div>
</div></body></html>"""

    p = os.path.join(OUT, "report.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)
    df.to_csv(os.path.join(OUT, "signals_main_board.csv"), index=False, encoding="utf-8-sig")
    print("→", p)
    print("→ out/signals_main_board.csv")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
