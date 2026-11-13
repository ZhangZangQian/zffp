/* K 线图：纯 SVG，无依赖。
   主图蜡烛 + 成交量 + MACD / KDJ / RSI 副图，支持滚轮缩放、拖动平移。 */
const KLine = (() => {
  const UP = '#f0475c', DOWN = '#17b978';
  const W = 1200, L = 8, R = 62, T = 14;
  const H_PRICE = 238, H_ANN = 52, H_VOL = 70, H_IND = 66, GAP = 12;
  const MIN_BARS = 20;
  const MAX_BARS = 300;

  const LINE_COLORS = ['#f5a524', '#4f8cf7', '#a78bfa', '#4ade80'];

  const store = new WeakMap();

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function layout(opts) {
    const g = {};
    let y = T;
    g.price = { top: y, h: H_PRICE };
    y += H_PRICE + 2;
    g.ann = { top: y, h: H_ANN };
    y += H_ANN;
    g.vol = { top: y, h: H_VOL };
    y += H_VOL + GAP;
    ['macd', 'kdj', 'rsi'].forEach((k) => {
      if (opts[k]) { g[k] = { top: y, h: H_IND }; y += H_IND + GAP; }
    });
    return { g, H: y - GAP + 28 };
  }

  /* ---------------------------------------------------------------- 绘制 */
  function build(bars, annot, opts, params = {}) {
    const n = bars.length;
    if (!n) return { svg: '', H: 200, legend: '' };
    const labels = params.labels || {};

    const { g, H } = layout(opts);
    const step = (W - L - R) / n;
    const x = (i) => L + step * (i + 0.5);
    const bodyW = Math.max(1.5, Math.min(step * 0.62, 14));
    const axisY = H - 10;

    let s = `<svg viewBox="0 0 ${W} ${H}" class="kline-svg" preserveAspectRatio="xMidYMid meet">`;

    /* ---- 主图：价格 ---- */
    const P = g.price;
    let maxP = Math.max(...bars.map((b) => b.high));
    let minP = Math.min(...bars.map((b) => b.low));
    (annot.lines || []).forEach((l) => {
      if (!l.price) return;
      if (l.price >= minP * 0.85 && l.price <= maxP * 1.15) {
        maxP = Math.max(maxP, l.price); minP = Math.min(minP, l.price);
      }
    });
    const spanP = (maxP - minP) || 1;
    maxP += spanP * 0.05; minP -= spanP * 0.05;
    const yP = (p) => P.top + ((maxP - p) / (maxP - minP)) * P.h;

    for (let k = 0; k <= 4; k++) {
      const p = minP + ((maxP - minP) * k) / 4;
      s += `<line x1="${L}" y1="${yP(p)}" x2="${W - R}" y2="${yP(p)}" stroke="#1b2432"/>`;
      s += `<text x="${W - R + 7}" y="${yP(p) + 4}" fill="#66758a" font-size="11" class="num">${p.toFixed(2)}</text>`;
    }

    [['ma5', '#f5a524'], ['ma10', '#4f8cf7'], ['ma20', '#a78bfa']].forEach(([key, color]) => {
      let d = '', started = false;
      bars.forEach((b, i) => {
        const v = b[key];
        if (v == null) { started = false; return; }
        d += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${yP(v).toFixed(1)}`;
        started = true;
      });
      if (d) s += `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.2" opacity=".85"/>`;
    });

    bars.forEach((b, i) => {
      const color = b.close >= b.open ? UP : DOWN;
      const cx = x(i);
      s += `<line x1="${cx}" y1="${yP(b.high)}" x2="${cx}" y2="${yP(b.low)}" stroke="${color}" stroke-width="1"/>`;
      const yo = yP(b.open), yc = yP(b.close);
      s += `<rect x="${cx - bodyW / 2}" y="${Math.min(yo, yc)}" width="${bodyW}"
        height="${Math.max(1, Math.abs(yc - yo))}" fill="${color}"/>`;
    });

    /* ---- 关键日期标注（价格与成交量之间的条带，按横向重叠自动分行）---- */
    const usedRows = [];
    (annot.marks || []).forEach((m) => {
      const i = bars.findIndex((b) => b.date === m.date);
      if (i < 0) return;
      const cx = x(i);
      const color = m.color || '#4f8cf7';
      const label = m.label || '';
      const w = label.length * 8.6 + 9;
      const rx = clamp(cx - w / 2, L + 2, W - R - w - 2);

      let row = 0;
      while (row < 3 && usedRows[row]
        && usedRows[row].some(([a, b]) => !(rx + w < a - 3 || rx > b + 3))) row += 1;
      usedRows[row] = usedRows[row] || [];
      usedRows[row].push([rx, rx + w]);

      s += `<line x1="${cx}" y1="${P.top}" x2="${cx}" y2="${g.vol.top + g.vol.h}"
        stroke="${color}" stroke-width="1" stroke-dasharray="3 3" opacity=".4"/>`;
      const ly = g.ann.top + 12 + row * 14;
      s += `<rect x="${rx}" y="${ly - 9}" width="${w}" height="13" rx="3"
        fill="#0b1220" opacity=".92"/>`;
      s += `<rect x="${rx}" y="${ly - 9}" width="${w}" height="13" rx="3"
        fill="${color}" opacity=".22"/>`;
      s += `<text x="${rx + w / 2}" y="${ly + 1}" fill="${color}" font-size="10"
        text-anchor="middle">${label}</text>`;
    });

    /* ---- 水平价位线 ---- */
    (annot.lines || []).forEach((l) => {
      if (!l.price) return;
      const py = yP(l.price);
      if (py < P.top - 2 || py > P.top + P.h + 2) return;
      s += `<line x1="${L}" y1="${py}" x2="${W - R}" y2="${py}" stroke="${l.color}"
        stroke-width="1" stroke-dasharray="5 4" opacity=".75"/>`;
      const text = `${l.label} ${l.price.toFixed(2)}`;
      const w = text.length * 8.4 + 10;
      s += `<rect x="${L + 3}" y="${py - 9}" width="${w}" height="15" rx="3" fill="#0d1420" opacity=".92"/>`;
      s += `<text x="${L + 8}" y="${py + 3}" fill="${l.color}" font-size="10.5">${text}</text>`;
    });

    /* ---- 最新价 ---- */
    const last = bars[n - 1];
    const ly = yP(last.close);
    const lastColor = last.close >= last.open ? UP : DOWN;
    s += `<line x1="${L}" y1="${ly}" x2="${W - R}" y2="${ly}" stroke="${lastColor}"
      stroke-width="1" stroke-dasharray="2 3" opacity=".55"/>`;
    s += `<rect x="${W - R + 2}" y="${ly - 8}" width="56" height="16" rx="3" fill="${lastColor}"/>`;
    s += `<text x="${W - R + 30}" y="${ly + 4}" fill="#fff" font-size="10.5"
      text-anchor="middle" class="num">${last.close.toFixed(2)}</text>`;

    /* ---- 成交量 ---- */
    const V = g.vol;
    const volMax = Math.max(...bars.map((b) => b.volume)) || 1;
    const yV = (v) => V.top + V.h - (v / volMax) * V.h;
    s += `<line x1="${L}" y1="${V.top + V.h}" x2="${W - R}" y2="${V.top + V.h}" stroke="#1b2432"/>`;
    bars.forEach((b, i) => {
      const color = b.close >= b.open ? UP : DOWN;
      const hgt = V.top + V.h - yV(b.volume);
      s += `<rect x="${x(i) - bodyW / 2}" y="${yV(b.volume)}" width="${bodyW}"
        height="${Math.max(0.5, hgt)}" fill="${color}" opacity=".55"/>`;
      if (b.limit_up) {
        s += `<circle cx="${x(i)}" cy="${V.top + V.h - 3}" r="2" fill="${UP}" opacity=".9"/>`;
      }
    });

    /* ---- 指标副图 ---- */
    if (opts.macd) s += paneMacd(bars, g.macd, labels.macd || 'MACD');
    if (opts.kdj) s += paneKdj(bars, g.kdj, labels.kdj || 'KDJ');
    if (opts.rsi) s += paneRsi(bars, g.rsi, labels.rsi || 'RSI', params.rsi || [6, 12, 24]);

    /* ---- 日期轴 ---- */
    const dateStep = Math.max(1, Math.ceil(n / 8));
    for (let i = 0; i < n; i += dateStep) {
      s += `<text x="${x(i)}" y="${axisY}" fill="#66758a" font-size="10"
        text-anchor="middle" class="num">${bars[i].date.slice(5)}</text>`;
    }

    s += '</svg>';

    const legend = [
      `<span style="color:#f5a524">MA5 ${fmt(last.ma5)}</span>`,
      `<span style="color:#4f8cf7">MA10 ${fmt(last.ma10)}</span>`,
      `<span style="color:#a78bfa">MA20 ${fmt(last.ma20)}</span>`,
    ].join('');

    return { svg: s, H, legend };
  }

  const fmt = (v) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(2);

  function paneHeader(y, label, items) {
    const est = (t) => String(t).length * 6.2;
    const parts = [[label, '#66758a']].concat(
      items.map(([k, v, c]) => [`${k} ${v}`, c || '#8b98a8']));
    const total = parts.reduce((a, p) => a + est(p[0]), 0) + (parts.length - 1) * 10;
    let s = `<rect x="${L}" y="${y + 1}" width="${total + 12}" height="13" rx="2.5"
      fill="#0b1220" opacity=".88"/>`;
    let dx = L + 6;
    parts.forEach(([text, color]) => {
      s += `<text x="${dx}" y="${y + 11}" fill="${color}" font-size="10">${text}</text>`;
      dx += est(text) + 10;
    });
    return s;
  }

  function linePath(bars, key, x, y) {
    let d = '', started = false;
    bars.forEach((b, i) => {
      const v = b[key];
      if (v == null) { started = false; return; }
      d += `${started ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      started = true;
    });
    return d;
  }

  function paneMacd(bars, p, label) {
    const vals = bars.flatMap((b) => [b.dif, b.dea, b.macd]).filter((v) => v != null);
    if (!vals.length) return '';
    const m = Math.max(...vals.map(Math.abs)) || 1;
    const y = (v) => p.top + 18 + ((m - v) / (2 * m)) * (p.h - 22);
    const n = bars.length;
    const step = (W - L - R) / n;
    const x = (i) => L + step * (i + 0.5);
    const bw = Math.max(1, Math.min(step * 0.5, 8));
    const last = bars[n - 1];

    let s = `<line x1="${L}" y1="${y(0)}" x2="${W - R}" y2="${y(0)}" stroke="#232d3d"/>`;
    s += paneHeader(p.top, label, [
      ['DIF', fmt(last.dif), '#f5a524'],
      ['DEA', fmt(last.dea), '#4f8cf7'],
      ['MACD', fmt(last.macd), last.macd >= 0 ? UP : DOWN],
    ]);
    bars.forEach((b, i) => {
      if (b.macd == null) return;
      const top = Math.min(y(b.macd), y(0));
      const hgt = Math.abs(y(b.macd) - y(0));
      s += `<rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${Math.max(0.6, hgt)}"
        fill="${b.macd >= 0 ? UP : DOWN}" opacity=".75"/>`;
    });
    s += `<path d="${linePath(bars, 'dif', x, y)}" fill="none" stroke="#f5a524" stroke-width="1.2"/>`;
    s += `<path d="${linePath(bars, 'dea', x, y)}" fill="none" stroke="#4f8cf7" stroke-width="1.2"/>`;
    s += `<line x1="${L}" y1="${p.top + p.h}" x2="${W - R}" y2="${p.top + p.h}" stroke="#1b2432"/>`;
    return s;
  }

  function paneKdj(bars, p, label) {
    const vals = bars.flatMap((b) => [b.k, b.d, b.j]).filter((v) => v != null);
    if (!vals.length) return '';
    const lo = Math.min(0, ...vals), hi = Math.max(100, ...vals);
    const y = (v) => p.top + 18 + ((hi - v) / (hi - lo)) * (p.h - 22);
    const n = bars.length, step = (W - L - R) / n;
    const x = (i) => L + step * (i + 0.5);
    const last = bars[n - 1];

    let s = `<line x1="${L}" y1="${p.top + p.h}" x2="${W - R}" y2="${p.top + p.h}" stroke="#1b2432"/>`;
    [20, 80].forEach((v) => {
      s += `<line x1="${L}" y1="${y(v)}" x2="${W - R}" y2="${y(v)}" stroke="#232d3d" stroke-dasharray="3 4"/>`;
    });
    s += paneHeader(p.top, label, [
      ['K', fmt(last.k), '#f5a524'],
      ['D', fmt(last.d), '#4f8cf7'],
      ['J', fmt(last.j), '#a78bfa'],
    ]);
    s += `<path d="${linePath(bars, 'k', x, y)}" fill="none" stroke="#f5a524" stroke-width="1.2"/>`;
    s += `<path d="${linePath(bars, 'd', x, y)}" fill="none" stroke="#4f8cf7" stroke-width="1.2"/>`;
    s += `<path d="${linePath(bars, 'j', x, y)}" fill="none" stroke="#a78bfa" stroke-width="1.2"/>`;
    return s;
  }

  function paneRsi(bars, p, label, periods) {
    const n = bars.length, step = (W - L - R) / n;
    const x = (i) => L + step * (i + 0.5);
    const y = (v) => p.top + 18 + ((100 - v) / 100) * (p.h - 22);
    const last = bars[n - 1];

    let s = `<line x1="${L}" y1="${p.top + p.h}" x2="${W - R}" y2="${p.top + p.h}" stroke="#1b2432"/>`;
    [30, 70].forEach((v) => {
      s += `<line x1="${L}" y1="${y(v)}" x2="${W - R}" y2="${y(v)}" stroke="#232d3d" stroke-dasharray="3 4"/>`;
    });
    s += paneHeader(p.top, label, periods.map((d, i) => [
      String(d), fmt(last['rsi' + d]), LINE_COLORS[i % LINE_COLORS.length],
    ]));
    periods.forEach((d, i) => {
      s += `<path d="${linePath(bars, 'rsi' + d, x, y)}" fill="none"
        stroke="${LINE_COLORS[i % LINE_COLORS.length]}" stroke-width="1.2"/>`;
    });
    return s;
  }

  /* ---------------------------------------------------------------- 渲染 */
  function render(el) {
    const st = store.get(el);
    if (!st) return;
    const slice = st.bars.slice(st.start, st.start + st.count);
    const { svg, H, legend } = build(slice, st.annot, st.opts, st.params);
    const zoomPct = Math.round((st.count / st.bars.length) * 100);

    el.innerHTML = `
      <div class="kline-wrap">
        <div class="kline-legend">${legend}
          <span class="muted" style="margin-left:auto">
            <i class="dot" style="background:${UP}"></i>涨
            <i class="dot" style="background:${DOWN}"></i>跌
            <i class="dot" style="background:${UP};opacity:.55"></i>成交量底部圆点=涨停
          </span>
        </div>
        <div class="kline-canvas">
          ${svg}
          <div class="kline-cross" hidden></div>
          <div class="kline-tip" hidden></div>
        </div>
        <div class="kline-toolbar">
          <button class="btn tiny" data-z="in">放大</button>
          <button class="btn tiny" data-z="out">缩小</button>
          <button class="btn tiny" data-z="reset">重置</button>
          <span class="hint">显示 ${st.count} / ${st.bars.length} 根（${zoomPct}%）</span>
          <span class="hint" style="margin-left:auto">滚轮缩放 · 按住拖动平移 · 双击重置</span>
        </div>
      </div>`;
    bind(el, slice);
  }

  // 节流：rAF 在后台标签页/无头环境可能不触发，用 setTimeout 兜底保证最终一定重绘
  let lastPaint = 0;
  function schedule(el) {
    const now = performance.now();
    if (now - lastPaint >= 16) {
      lastPaint = now;
      render(el);
      return;
    }
    clearTimeout(el._kt);
    el._kt = setTimeout(() => { lastPaint = performance.now(); render(el); }, 16);
  }

  /* ---------------------------------------------------------------- 交互 */
  function bind(el, slice) {
    const st = store.get(el);
    const svg = el.querySelector('.kline-svg');
    const cross = el.querySelector('.kline-cross');
    const tip = el.querySelector('.kline-tip');
    if (!svg) return;

    const stepPx = () => svg.getBoundingClientRect().width / st.bars.length;
    const ux2idx = (clientX) => {
      const rect = svg.getBoundingClientRect();
      const ux = ((clientX - rect.left) / rect.width) * W;
      return clamp(Math.floor((ux - L) / ((W - L - R) / slice.length)), 0, slice.length - 1);
    };

    const hideTip = () => { cross.hidden = true; tip.hidden = true; };

    svg.addEventListener('mouseleave', hideTip);
    svg.addEventListener('mousemove', (e) => {
      if (st.dragging) return;
      const i = ux2idx(e.clientX);
      const b = slice[i];
      if (!b) return;
      const rect = svg.getBoundingClientRect();
      const cxPx = ((L + ((W - L - R) / slice.length) * (i + 0.5)) / W) * rect.width;

      cross.hidden = false;
      cross.style.left = `${cxPx}px`;
      cross.style.height = `${svg.clientHeight}px`;

      const cls = b.close >= b.open ? 'up' : 'down';
      const volText = b.volume >= 10000
        ? `${(b.volume / 10000).toFixed(1)} 万手` : `${Math.round(b.volume).toLocaleString()} 手`;
      const indRows = [];
      if (st.opts.macd) {
        indRows.push(['MACD', `${fmt(b.dif)} / ${fmt(b.dea)} / ${fmt(b.macd)}`]);
      }
      if (st.opts.kdj) {
        indRows.push(['KDJ', `${fmt(b.k)} / ${fmt(b.d)} / ${fmt(b.j)}`]);
      }
      if (st.opts.rsi) {
        const periods = (st.params && st.params.rsi) || [6, 12, 24];
        indRows.push(['RSI', periods.map((d) => fmt(b['rsi' + d])).join(' / ')]);
      }

      tip.hidden = false;
      tip.innerHTML = `
        <div class="tip-date">${b.date}${b.limit_up ? ' <span class="tag red" style="padding:0 5px">涨停</span>' : ''}</div>
        <div class="tip-grid">
          <span>开</span><b class="num">${b.open.toFixed(2)}</b>
          <span>高</span><b class="num">${b.high.toFixed(2)}</b>
          <span>低</span><b class="num">${b.low.toFixed(2)}</b>
          <span>收</span><b class="num ${cls}">${b.close.toFixed(2)}</b>
          <span>涨幅</span><b class="num ${cls}">${b.pct >= 0 ? '+' : ''}${b.pct.toFixed(2)}%</b>
          <span>量</span><b class="num">${volText}</b>
        </div>
        ${indRows.length ? `<div class="tip-inds">${indRows.map(([k, v]) =>
          `<div class="tip-ind"><i>${k}</i><b class="num">${v}</b></div>`).join('')}</div>` : ''}`;
      const px = cxPx / rect.width;
      tip.style.left = px > 0.6 ? `${cxPx - 8}px` : `${cxPx + 12}px`;
      tip.style.transform = px > 0.6 ? 'translateX(-100%)' : 'none';
    });

    // 滚轮缩放：以光标下的 K 线为锚点
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      const i = ux2idx(e.clientX);
      const abs = st.start + i;
      const rel = i / st.count;
      const factor = e.deltaY > 0 ? 1.2 : 0.83;
      st.count = clamp(Math.round(st.count * factor), MIN_BARS, Math.min(MAX_BARS, st.bars.length));
      st.start = clamp(Math.round(abs - rel * st.count), 0, st.bars.length - st.count);
      schedule(el);
    }, { passive: false });

    // 拖动平移
    let startX = 0, startStart = 0;
    svg.addEventListener('mousedown', (e) => {
      st.dragging = true;
      startX = e.clientX;
      startStart = st.start;
      el.classList.add('grabbing');
      e.preventDefault();
    });
    window.addEventListener('mouseup', () => {
      if (!st.dragging) return;
      st.dragging = false;
      el.classList.remove('grabbing');
    });
    svg.addEventListener('mousemove', (e) => {
      if (!st.dragging) return;
      const dx = e.clientX - startX;
      const shift = Math.round(dx / stepPx());
      st.start = clamp(startStart - shift, 0, st.bars.length - st.count);
      schedule(el);
    });
    svg.addEventListener('dblclick', () => reset(el));

    el.querySelectorAll('[data-z]').forEach((btn) => {
      btn.onclick = () => {
        const z = btn.dataset.z;
        if (z === 'reset') return reset(el);
        const c = st.count;
        st.count = clamp(Math.round(c * (z === 'in' ? 0.7 : 1.43)), MIN_BARS,
          Math.min(MAX_BARS, st.bars.length));
        st.start = clamp(st.start + Math.round((c - st.count) / 2), 0, st.bars.length - st.count);
        schedule(el);
      };
    });
  }

  function reset(el) {
    const st = store.get(el);
    if (!st) return;
    st.count = Math.min(120, st.bars.length);
    st.start = st.bars.length - st.count;
    schedule(el);
  }

  /* ---------------------------------------------------------------- 入口 */
  function mount(el, resp, annot = {}, opts = {}) {
    const bars = resp.bars || [];
    if (!bars.length) {
      el.innerHTML = '<div class="empty">没有可展示的 K 线数据</div>';
      return;
    }
    const prev = store.get(el);
    const sameCode = prev && prev.code === resp.code;
    const indicators = {
      macd: opts.macd !== false,
      kdj: opts.kdj !== false,
      rsi: opts.rsi !== false,
    };
    const count = sameCode ? prev.count : Math.min(120, bars.length);
    const start = sameCode ? clamp(prev.start, 0, Math.max(0, bars.length - count))
      : bars.length - count;

    store.set(el, {
      code: resp.code, bars, annot, opts: indicators,
      params: {
        labels: resp.labels || {},
        rsi: resp.rsi_periods || [6, 12, 24],
      },
      count: Math.min(count, bars.length), start, dragging: false,
    });
    render(el);
  }

  return { mount, reset };
})();
