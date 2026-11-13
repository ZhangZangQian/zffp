/* 巨阴突破战法 · 复盘纪律系统  ——  前端（无框架，单文件） */

const S = {
  tab: 'today',
  overview: null,
  pool: null,
  plans: [],
  trades: [],
  reviews: [],
  stats: null,
  settings: null,
  chart: null,
  searchItems: [],
  signalTab: 'actionable',
  pendingSignal: null,
  preview: null,
  jobTimer: null,
  jobActive: false,
  lastJobMsg: '',
};

/* ------------------------------------------------------------------ 工具 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const n2 = (v) => (v === null || v === undefined || isNaN(v)) ? '—' : Number(v).toFixed(2);
const pct = (v) => (v === null || v === undefined || isNaN(v)) ? '—'
  : (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
const money = (v) => {
  if (v === null || v === undefined || isNaN(v)) return '—';
  const n = Number(v);
  return (n < 0 ? '-' : '') + '¥' + Math.abs(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
};
/* 成交量单位是「手」（1 手 = 100 股） */
const vol = (v) => {
  const n = Number(v);
  if (!n || isNaN(n)) return '—';
  if (n >= 10000) return (n / 10000).toFixed(1) + ' 万手';
  return Math.round(n).toLocaleString('zh-CN') + ' 手';
};
const cls = (v) => Number(v) > 0 ? 'up' : (Number(v) < 0 ? 'down' : '');

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || data.message || '请求失败');
    err.data = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = esc(msg);
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function openModal(title, html, wide) {
  $('#modal').className = 'modal' + (wide ? ' wide' : '');
  $('#modal').innerHTML = `<h2>${esc(title)}</h2>${html}`;
  $('#modal-mask').hidden = false;
}
function closeModal() { $('#modal-mask').hidden = true; $('#modal').innerHTML = ''; }

/* ------------------------------------------------------------------ 启动 */
const TABS = ['today', 'signals', 'quote', 'plan', 'trades', 'review', 'stats', 'settings'];

async function boot() {
  const h = (location.hash || '').replace('#', '');
  if (TABS.includes(h)) S.tab = h;
  await refresh();
  if (S.tab !== 'today') await loadTabData(S.tab);
  render();
  tickJob();
}

async function loadTabData(tab) {
  try {
    if (tab === 'quote' && !S.chart) {
      const pick = S.pool.actionable[0] || S.pool.triggered[0] || S.pool.waiting[0];
      if (pick) await loadChart(pick.code, signalAnnot(pick));
    }
    if (tab === 'plan' && !S.plans.length) await loadPlans();
    if (tab === 'trades' || tab === 'review') await loadTrades();
    if (tab === 'stats') await loadStats();
    if (tab === 'settings') await loadSettings();
  } catch (e) { toast(e.message, 'err'); }
}

async function goTab(tab) {
  S.tab = tab;
  location.hash = tab;
  await loadTabData(tab);
  render();
}

/* --------------------------------------------------------------- 行情图 */
function findSignal(id) {
  if (!S.pool) return null;
  for (const k of ['actionable', 'triggered', 'waiting', 'missed', 'expired']) {
    const hit = (S.pool[k] || []).find((s) => s.id === id);
    if (hit) return hit;
  }
  return null;
}

function signalAnnot(s) {
  if (!s) return {};
  const marks = [
    { date: s.t_date, label: 'T涨停', color: '#f0475c' },
    { date: s.t1_date, label: '巨阴', color: '#17b978' },
  ];
  if (s.breakout_date) marks.push({ date: s.breakout_date, label: '突破', color: '#f5a524' });
  if (s.buy_date) marks.push({ date: s.buy_date, label: '买点', color: '#4f8cf7' });
  return { marks, lines: [{ price: s.t1_high, label: '突破位', color: '#f5a524' }] };
}

async function fetchKline(code, days) {
  return api(`/api/kline?code=${encodeURIComponent(code)}&days=${days}`);
}

async function loadChart(code, annot) {
  const days = (S.chart && S.chart.days) || 120;
  const ind = (S.chart && S.chart.ind) || { macd: true, kdj: true, rsi: true };
  let resp = await fetchKline(code, days);
  // 形态日期比窗口更早时，自动拉更长的历史
  const first = resp.bars.length ? resp.bars[0].date : '';
  const needMore = (annot && annot.marks || []).some((m) => m.date && m.date < first);
  if (needMore && days < 250) resp = await fetchKline(code, 250);
  S.chart = { code: resp.code, name: resp.name, resp, annot: annot || {}, days, ind };
}

async function openChart(code, annot) {
  closeModal();
  try {
    await loadChart(code, annot);
    await goTab('quote');
  } catch (e) { toast(e.message, 'err'); }
}

async function refresh() {
  try {
    const [ov, pool] = await Promise.all([api('/api/overview'), api('/api/signals')]);
    S.overview = ov;
    S.pool = pool;
    renderTop();
    render();
  } catch (e) {
    $('#view').innerHTML = `<div class="card"><div class="empty">
      <div class="big">连接后端失败</div>${esc(e.message)}<br>
      请确认 <code>python run.py</code> 仍在运行。</div></div>`;
  }
}

/* ------------------------------------------------------------------ 顶栏 */
function renderTop() {
  const o = S.overview;
  if (!o) return;
  const snap = o.snapshot;
  const score = o.daily_score ? o.daily_score.score : 100;
  const scoreColor = score >= 90 ? 'var(--ok)' : score >= 70 ? 'var(--accent)' : 'var(--danger)';
  $('#topstats').innerHTML = `
    <div class="stat"><span class="k">交易日</span><span class="v num">${esc(o.trade_date || '—')}</span></div>
    <div class="stat"><span class="k">总权益</span><span class="v num">${money(snap.equity)}</span></div>
    <div class="stat"><span class="k">可用资金</span><span class="v num">${money(snap.available)}</span></div>
    <div class="stat"><span class="k">今日盈亏</span>
      <span class="v num ${cls(snap.today_pnl)}">${money(snap.today_pnl)}</span></div>
    <div class="stat"><span class="k">纪律分</span>
      <span class="v num" style="color:${scoreColor}">${score}</span></div>`;

  $('#tab-signals-badge').textContent = o.actionable.length || '';
  $('#tab-review-badge').textContent = o.pending_reviews.length || '';
}

/* ------------------------------------------------------------------ 路由 */
function render() {
  const map = {
    today: renderToday, signals: renderSignals, quote: renderQuote, plan: renderPlan,
    trades: renderTrades, review: renderReview, stats: renderStats,
    settings: renderSettings,
  };
  $$('#tabs .tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === S.tab));
  $('#view').innerHTML = (map[S.tab] || renderToday)();
  afterRender();
}

function afterRender() {
  if (S.tab === 'plan') {
    const sel = $('#f-signal');
    if (sel) {
      sel.value = S.pendingSignal || sel.value;
      if (S.pendingSignal) { S.pendingSignal = null; previewPlan(true); }
      else previewPlan();
    }
  }
  if (S.tab === 'quote' && S.chart && S.chart.resp) {
    const box = $('#kline-box');
    if (box && typeof KLine !== 'undefined') {
      KLine.mount(box, S.chart.resp, S.chart.annot || {}, S.chart.ind || {});
    }
  }
}

/* ================================================================== 今日 */
function renderToday() {
  const o = S.overview;
  const snap = o.snapshot;
  const coverage = o.data.coverage;
  if (!coverage[1]) {
    return `<div class="card"><div class="empty">
      <div class="big">还没有行情数据</div>
      点击右上角「更新行情」，拉取全市场日线（首次约 3-5 分钟）。<br>
      之后系统会在这里告诉你：今天有没有符合战法的票、该买多少、错了在哪走。
    </div></div>`;
  }

  const score = o.daily_score.score;
  const streak = o.streak;
  const cd = snap.in_cooldown;

  const cards = `
  <div class="grid g4">
    <div class="card metric hero">
      <span class="label">今日纪律分</span>
      <span class="value num" style="color:${score >= 90 ? 'var(--ok)' : score >= 70 ? 'var(--accent)' : 'var(--danger)'}">${score}</span>
      <span class="foot">${o.daily_score.events.length ? o.daily_score.events.length + ' 项扣分' : '无扣分项'}</span>
    </div>
    <div class="card metric">
      <span class="label">连续合规</span>
      <span class="value num">${streak.days} <span style="font-size:13px;color:var(--muted)">天</span></span>
      <span class="foot">${streak.since ? '自 ' + esc(streak.since) : '还没有完整合规的一天'}</span>
    </div>
    <div class="card metric">
      <span class="label">今日可下手</span>
      <span class="value num" style="color:${o.actionable.length ? 'var(--accent)' : 'var(--muted)'}">${o.actionable.length}</span>
      <span class="foot">等待突破 ${o.waiting_count} 只</span>
    </div>
    <div class="card metric">
      <span class="label">持仓 / 连亏</span>
      <span class="value num">${snap.open_count} <span style="font-size:13px;color:var(--muted)">/</span> ${snap.loss_streak}</span>
      <span class="foot">${cd ? '<span style="color:var(--danger)">冷静期到 ' + esc(snap.cooldown_until) + '</span>' : '可以正常开仓'}</span>
    </div>
  </div>`;

  const flow = `<div class="grid g2" style="margin-top:14px">
    <div class="card">
      <h3>今日流程 <span class="sub">系统自动核验，不靠自觉</span></h3>
      <div class="flow">
        ${o.flow.map((f) => `
          <div class="flow-item ${f.done ? 'done' : ''}">
            <div class="flow-dot">${f.done ? '✓' : ''}</div>
            <div class="flow-label">${esc(f.label)}</div>
            <div class="flow-hint">${esc(f.hint)}</div>
          </div>`).join('')}
      </div>
    </div>
    <div class="card">
      <h3>纪律状态</h3>
      ${disciplinePanel(o)}
    </div>
  </div>`;

  const actionable = o.actionable.length ? `
    <div class="section-title">今天可以买（机器选出 ${o.actionable.length} 只）
      ${o.missed_count ? `<span class="sub" style="font-weight:400">另有 ${o.missed_count} 个信号买点已过</span>` : ''}
    </div>
    <div class="card tight">
      <table>
        <thead><tr>
          <th>标的</th><th class="r">T日涨停</th><th class="r">T+1 巨阴</th>
          <th class="r">高开</th><th class="r">阴线实体</th><th class="r">阴线高点</th>
          <th class="r">放量突破</th><th class="r">量比</th><th class="r">买点</th><th></th>
        </tr></thead>
        <tbody>${o.actionable.map(signalRow2).join('')}</tbody>
      </table>
    </div>` : `
    <div class="section-title">今天可以买</div>
    <div class="card"><div class="empty">
      <div class="big">今天没有符合战法的买点</div>
      这不是坏事。战法的价值在于「没有信号就不做」——<br>
      空仓等待也是执行纪律的一部分。
    </div></div>`;

  const positions = o.positions.length ? `
    <div class="section-title">持仓监控</div>
    <div class="grid g3">${o.positions.map(positionCard).join('')}</div>` : '';

  const pending = o.pending_reviews.length ? `
    <div class="section-title">待复盘（${o.pending_reviews.length}）</div>
    <div class="card tight">
      <table><thead><tr>
        <th>标的</th><th class="r">买入</th><th class="r">卖出</th><th class="r">盈亏</th>
        <th class="r">持有</th><th></th>
      </tr></thead><tbody>
      ${o.pending_reviews.map((t) => `
        <tr>
          <td>${stockCell(t.code, t.name)}</td>
          <td class="r num">${n2(t.buy_price)}</td>
          <td class="r num">${n2(t.sell_price)}</td>
          <td class="r num ${cls(t.pnl)}">${money(t.pnl)} <span class="muted">${pct(t.pnl_pct)}</span></td>
          <td class="r num">${t.hold_days || 1} 日</td>
          <td class="r"><button class="btn sm primary" data-action="review" data-id="${t.id}">去复盘</button></td>
        </tr>`).join('')}
      </tbody></table>
    </div>` : '';

  const events = o.daily_score.events.length ? `
    <div class="section-title">今日纪律事件</div>
    <div class="card tight"><div class="gate">
      ${o.daily_score.events.map((e) => `
        <div class="gate-item block"><div>
          <div class="t">${esc(e.text)}</div>
          <div class="d">${e.delta} 分</div>
        </div></div>`).join('')}
    </div></div>` : '';

  return cards + flow + actionable + positions + pending + events;
}

function disciplinePanel(o) {
  const snap = o.snapshot;
  const r = o.params.risk;
  const lines = [
    ['单笔风险上限', `${(r.risk_per_trade_pct * 100).toFixed(1)}% ≈ ${money(snap.equity * r.risk_per_trade_pct)}`],
    ['单票仓位上限', `${(r.max_position_pct * 100).toFixed(0)}%`],
    ['最低盈亏比', `${r.min_rr}:1`],
    ['每日计划上限', `${r.max_plans_per_day} 个`],
    ['连亏冷静期', `${r.loss_streak_cooldown} 笔 → 停 ${r.cooldown_days} 日`],
    ['当日停手线', `-${(r.daily_stop_loss_pct * 100).toFixed(1)}%`],
  ];
  return `<div class="kv">${lines.map(([k, v]) =>
    `<span class="k">${esc(k)}</span><span class="num">${esc(v)}</span>`).join('')}</div>
    <div style="margin-top:12px;font-size:11.5px;color:var(--muted);line-height:1.7">
      这些数字不是你写在纸上的承诺，是系统拦你下单的门槛。
    </div>`;
}

function stockCell(code, name) {
  return `<div class="stock clickable" data-action="chart" data-code="${esc(code)}"
    title="查看 K 线"><span class="name">${esc(name || code)}</span>
    <span class="code">${esc(code)}</span></div>`;
}

function stockCellSignal(s) {
  return `<div class="stock clickable" data-action="chart-signal" data-id="${s.id}"
    data-code="${esc(s.code)}" title="查看带战法标注的 K 线">
    <span class="name">${esc(s.name || s.code)}</span>
    <span class="code">${esc(s.code)}</span></div>`;
}

function signalRow2(s) {
  return `<tr>
    <td>${stockCellSignal(s)}</td>
    <td class="r num">${esc(s.t_date)}<br><span class="muted">${n2(s.t_close)}</span></td>
    <td class="r num">${esc(s.t1_date)}</td>
    <td class="r num up">${pct(s.t1_gap * 100)}</td>
    <td class="r num">${(s.t1_body * 100).toFixed(1)}%</td>
    <td class="r num">${n2(s.t1_high)}</td>
    <td class="r num">${esc(s.breakout_date || '—')}<br><span class="muted">${n2(s.breakout_close)}</span></td>
    <td class="r num">${s.breakout_vol_ratio ? s.breakout_vol_ratio + '×' : '—'}</td>
    <td class="r num">${esc(s.buy_date || '次日开盘')}</td>
    <td class="r">
      ${s.planned ? '<span class="tag grey">已计划</span>'
        : `<button class="btn sm primary" data-action="plan" data-id="${s.id}">写计划</button>`}
    </td>
  </tr>`;
}

function positionCard(p) {
  const stateTag = p.state === '持有中' ? 'blue'
    : (p.state.includes('破止损') || p.state.includes('超期')) ? 'red' : 'green';
  const progress = Math.max(-100, Math.min(100, p.progress || 0));
  return `<div class="card">
    <h3>${esc(p.name)} <span class="sub">${esc(p.code)}</span></h3>
    <div style="display:flex;justify-content:space-between;align-items:baseline">
      <div><span class="num" style="font-size:22px;font-weight:700"
        class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</span>
        <span class="muted num" style="font-size:12px">${money(p.unrealized)}</span></div>
      <span class="tag ${stateTag}">${esc(p.state)}</span>
    </div>
    <div class="bar" style="margin:10px 0 8px">
      <i class="${progress >= 0 ? 'up' : 'down'}" style="width:${Math.abs(progress)}%"></i>
    </div>
    <div class="kv" style="font-size:12px">
      <span class="k">买价 / 现价</span><span class="num">${n2(p.buy_price)} → ${n2(p.last)}</span>
      <span class="k">止损 / 目标</span><span class="num">${n2(p.stop_price)} / ${n2(p.target_price)}</span>
      <span class="k">持有</span><span class="num">${p.held} 个交易日</span>
    </div>
    ${p.hint ? `<div style="margin-top:9px;font-size:11.5px;color:var(--accent)">${esc(p.hint)}</div>` : ''}
    <div style="margin-top:10px;display:flex;gap:6px">
      <button class="btn sm" data-action="close-trade" data-id="${p.id}">卖出</button>
      <button class="btn sm ghost" data-action="trade-detail" data-id="${p.id}">详情</button>
    </div>
  </div>`;
}

/* ================================================================== 信号池 */
function renderSignals() {
  const pool = S.pool;
  const tabs = [
    ['actionable', '可下手', pool.actionable.length],
    ['waiting', '等待突破', pool.waiting.length],
    ['triggered', '已触发', pool.triggered.length],
    ['failed', '突破无量', pool.failed ? pool.failed.length : 0],
    ['expired', '已过期', pool.expired.length],
  ];
  const rows = pool[S.signalTab] || [];

  return `
  <div class="card tight" style="margin-bottom:14px">
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      ${tabs.map(([k, label, n]) => `
        <button class="btn sm ${S.signalTab === k ? 'primary' : 'ghost'}"
          data-action="signal-tab" data-tab="${k}">${label} <span class="muted">${n}</span></button>`).join('')}
      <span style="flex:1"></span>
      <span class="hint">T日涨停 → T+1 高开低走巨阴（收盘 > T日开盘）→ 10 日内放量涨停突破阴线高点 → 次日买入</span>
    </div>
  </div>
  ${rows.length ? `
  <div class="card tight">
    <table>
      <thead><tr>
        <th>标的</th><th class="r">T日</th><th class="r">T日开盘/收盘</th>
        <th class="r">阴线日期</th><th class="r">高开</th><th class="r">阴线实体</th>
        <th class="r">阴线高点</th><th class="r">突破</th><th class="r">量比</th>
        <th class="r">买点</th><th></th>
      </tr></thead>
      <tbody>${rows.map((s) => `
        <tr>
          <td>${stockCellSignal(s)}</td>
          <td class="r num">${esc(s.t_date)}</td>
          <td class="r num">${n2(s.t_open)} / ${n2(s.t_close)}</td>
          <td class="r num">${esc(s.t1_date)}</td>
          <td class="r num up">${pct(s.t1_gap * 100)}</td>
          <td class="r num">${(s.t1_body * 100).toFixed(1)}%</td>
          <td class="r num">${n2(s.t1_high)}</td>
          <td class="r num">${s.breakout_date ? `${esc(s.breakout_date)} <span class="muted">${n2(s.breakout_close)}</span>` : `<span class="muted">剩 ${s.days_left} 日</span>`}</td>
          <td class="r num">${s.breakout_vol_ratio ? s.breakout_vol_ratio + '×' : '—'}</td>
          <td class="r num">${s.state === 'failed'
            ? '<span class="tag red">无量·作废</span>'
            : esc(s.buy_date || (s.state === 'triggered' ? '次日' : '—'))}</td>
          <td class="r">${s.traded ? '<span class="tag green">已交易</span>'
            : s.planned ? '<span class="tag grey">已计划</span>'
              : s.state === 'triggered'
                ? `<button class="btn sm primary" data-action="plan" data-id="${s.id}">写计划</button>`
                : ''}</td>
        </tr>`).join('')}
      </tbody>
    </table>
  </div>` : `<div class="card"><div class="empty">
      <div class="big">这个分组没有信号</div>
      扫描后，这里会列出全市场符合巨阴突破形态的票。
    </div></div>`}`;
}

/* ================================================================== 行情 */
function renderQuote() {
  const c = S.chart;
  const ok = c && c.resp && c.resp.bars && c.resp.bars.length;
  const q = ok ? c.resp.quote : null;
  const quick = (S.pool.actionable || []).slice(0, 4);
  const ranges = [60, 120, 250];
  const ind = (c && c.ind) || { macd: true, kdj: true, rsi: true };

  return `
  <div class="card tight" style="margin-bottom:14px">
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <input id="q-search" placeholder="输入代码或名称，回车搜索" style="max-width:290px" />
      <div id="q-results" style="display:flex;gap:6px;flex-wrap:wrap"></div>
      <span style="flex:1"></span>
      ${ranges.map((d) => `<button class="btn sm ${c && c.days === d ? 'primary' : 'ghost'}"
        data-action="chart-days" data-days="${d}">${d}日</button>`).join('')}
    </div>
    <div style="margin-top:10px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
      <span class="hint">副图指标：</span>
      ${['macd', 'kdj', 'rsi'].map((k) => `<button class="chip ${ind[k] ? 'on' : ''}"
        data-action="ind" data-ind="${k}">${k.toUpperCase()}</button>`).join('')}
      ${quick.length ? `<span class="hint" style="margin-left:14px">今日信号：</span>
        ${quick.map((s) => `<button class="chip" data-action="chart-signal" data-id="${s.id}"
          data-code="${esc(s.code)}">${esc(s.name)} <span class="muted">${esc(s.code)}</span></button>`).join('')}` : ''}
    </div>
  </div>

  ${ok ? `
  <div class="card" style="margin-bottom:14px">
    <div class="quote-head">
      <div>
        <div style="display:flex;gap:9px;align-items:baseline">
          <span style="font-size:18px;font-weight:700">${esc(c.resp.name)}</span>
          <span class="muted num">${esc(c.resp.code)}</span>
          <span class="tag grey">${esc(q.date)}</span>
        </div>
        <div class="quote-meta" style="margin-top:8px">
          <span>前收 <b class="num">${n2(q.prev_close)}</b></span>
          <span>最高 <b class="num up">${n2(q.high)}</b></span>
          <span>最低 <b class="num down">${n2(q.low)}</b></span>
          <span>成交 <b class="num">${vol(q.volume)}</b></span>
          <span>区间 <b class="num">${n2(q.low_52)} ~ ${n2(q.high_52)}</b></span>
        </div>
      </div>
      <div style="flex:1"></div>
      <div style="text-align:right">
        <div class="quote-price num ${cls(q.pct)}">${n2(q.price)}</div>
        <div class="num ${cls(q.pct)}" style="font-size:14px;font-weight:600">${pct(q.pct)}</div>
      </div>
    </div>
  </div>
  <div class="card"><div id="kline-box"></div></div>` : `
  <div class="card"><div class="empty">
    <div class="big">选一只股票看行情</div>
    在上方搜索代码/名称，或点击任意股票名，都会打开带战法标注的 K 线。
  </div></div>`}`;
}

/* ================================================================== 写计划 */
function renderPlan() {
  const pool = S.pool;
  const seen = new Set();
  const opts = [...pool.actionable, ...pool.triggered]
    .filter((s) => {
      if (s.planned || seen.has(s.id)) return false;
      if (s.buy_date !== null && s.buy_date !== undefined) return false;
      seen.add(s.id);
      return true;
    });
  const planList = S.plans || [];

  return `
  <div class="grid g2">
    <div class="card">
      <h3>新建交易计划 <span class="sub">先写计划，才允许下单</span></h3>
      <div class="form-grid">
        <div class="field wide">
          <label>① 选择信号（必须是机器选出来的）</label>
          <select id="f-signal">
            <option value="">— 选择一只 —</option>
            ${opts.map((s) => `<option value="${s.id}">
              ${esc(s.name)} ${esc(s.code)} · 买点 ${esc(s.buy_date || '次日开盘')} · 突破 ${n2(s.breakout_close)}
            </option>`).join('')}
          </select>
          ${opts.length ? '' : '<div class="hint" style="color:var(--accent)">当前没有可下手的信号，今天不该交易。</div>'}
        </div>

        <div class="field">
          <label>② 买入价上限 <span class="hint">高于此价不成交</span></label>
          <input id="f-price" type="number" step="0.01" placeholder="0.00" />
        </div>
        <div class="field">
          <label>股数 <span class="hint">由系统按风险算出</span></label>
          <input id="f-shares" type="number" step="100" placeholder="自动" />
        </div>
        <div class="field">
          <label>③ 止损价 <span class="hint">错了在哪走</span></label>
          <input id="f-stop" type="number" step="0.01" placeholder="0.00" />
        </div>
        <div class="field">
          <label>④ 目标价 <span class="hint">对了在哪收</span></label>
          <input id="f-target" type="number" step="0.01" placeholder="0.00" />
        </div>

        <div class="field wide">
          <label>⑤ 买入理由 <span class="hint">少于 10 个字会被拒绝</span></label>
          <textarea id="f-thesis" placeholder="为什么是它？为什么是这个位置？错了怎么走？"></textarea>
        </div>
        <div class="field wide">
          <label>当下的情绪</label>
          <div class="chips" id="f-emotion">
            ${(S.overview.emotions || []).map((e, i) =>
              `<button type="button" class="chip ${i === 0 ? 'on' : ''}" data-emotion="${esc(e)}">${esc(e)}</button>`).join('')}
          </div>
        </div>
      </div>
      <div style="margin-top:14px;display:flex;gap:8px">
        <button class="btn primary" id="btn-submit-plan" disabled>锁定计划</button>
        <button class="btn ghost" id="btn-suggest">用系统建议值</button>
      </div>
    </div>

    <div class="card">
      <h3>闸门检查 <span class="sub">实时</span></h3>
      <div id="gate-panel">
        <div class="empty">选择信号后，这里会实时告诉你：能不能买、买多少、为什么。</div>
      </div>
    </div>
  </div>

  <div class="section-title">我的计划</div>
  ${planList.length ? `<div class="card tight"><table>
    <thead><tr><th>标的</th><th class="r">买点</th><th class="r">价格上限</th>
      <th class="r">股数</th><th class="r">止损</th><th class="r">目标</th>
      <th class="r">盈亏比</th><th>状态</th><th></th></tr></thead>
    <tbody>${planList.map((p) => `<tr>
      <td>${stockCell(p.code, p.name)}</td>
      <td class="r num">${esc(p.buy_date)}</td>
      <td class="r num">${n2(p.buy_price_limit)}</td>
      <td class="r num">${p.shares || '—'}</td>
      <td class="r num">${n2(p.stop_price)}</td>
      <td class="r num">${n2(p.target_price)}</td>
      <td class="r num">${p.rr ? p.rr + ':1' : '—'}</td>
      <td>${planStatusTag(p.status)}</td>
      <td class="r">${p.status === 'locked'
        ? `<button class="btn sm primary" data-action="execute" data-id="${p.id}">记录成交</button>
           <button class="btn sm ghost" data-action="cancel-plan" data-id="${p.id}">作废</button>`
        : ''}</td>
    </tr>`).join('')}</tbody></table></div>`
    : `<div class="card"><div class="empty">还没有计划。计划是你和冲动之间的唯一屏障。</div></div>`}`;
}

function planStatusTag(s) {
  const map = { locked: ['amber', '待执行'], executed: ['green', '已成交'], cancelled: ['grey', '已作废'] };
  const [c, t] = map[s] || ['grey', s];
  return `<span class="tag ${c}">${t}</span>`;
}

function renderGate(res) {
  if (!res) return '<div class="empty">选择信号后自动检查</div>';
  const blocks = res.blocks || [];
  const warns = res.warns || [];
  const calc = res.calc || {};
  const sig = res.signal;

  let html = '';
  if (res.ok) {
    html += `<div class="gate-item pass"><div>
      <div class="t">✓ 通过全部 ${blocks.length === 0 ? '闸门' : ''}检查</div>
      <div class="d">这笔交易符合纪律，可以锁定计划。</div></div></div>`;
  }
  if (blocks.length) {
    html += blocks.map((b) => `<div class="gate-item block"><div>
      <div class="t">✕ ${esc(b.title)}</div>
      <div class="d">${esc(b.detail)}</div>
      ${b.fix ? `<div class="f">→ ${esc(b.fix)}</div>` : ''}
    </div></div>`).join('');
  }
  if (warns.length) {
    html += warns.map((w) => `<div class="gate-item warn"><div>
      <div class="t">! ${esc(w.title)}</div>
      <div class="d">${esc(w.detail)}</div>
    </div></div>`).join('');
  }

  if (calc.shares !== undefined) {
    html += `<div class="card tight" style="margin-top:10px;background:rgba(255,255,255,.02)">
      <div class="kv">
        <span class="k">建议股数</span><span class="num"><b>${calc.shares}</b> 股</span>
        <span class="k">占用资金</span><span class="num">${money(calc.amount)}（${calc.position_pct}% 仓位）</span>
        <span class="k">单笔风险</span><span class="num ${calc.risk_pct > 2 ? 'warn' : ''}">${money(calc.risk_amount)}（${calc.risk_pct}%）</span>
        <span class="k">盈亏比</span><span class="num">${calc.rr || '—'}:1</span>
      </div>
    </div>`;
  }
  if (sig) {
    html += `<div class="card tight" style="margin-top:10px;background:rgba(255,255,255,.02)">
      <div class="ladder">
        <div class="ladder-row"><span class="k">T日 ${esc(sig.t_date)} 开盘 / 收盘</span>
          <span class="num">${n2(sig.t_open)} / ${n2(sig.t_close)}</span></div>
        <div class="ladder-row"><span class="k">T+1 ${esc(sig.t1_date)} 高开</span>
          <span class="num up">${pct(sig.t1_gap * 100)}</span></div>
        <div class="ladder-row"><span class="k">T+1 最高（突破位）</span>
          <span class="num">${n2(sig.t1_high)}</span></div>
        <div class="ladder-row"><span class="k">突破日涨停</span>
          <span class="num">${esc(sig.breakout_date || '—')} · ${n2(sig.breakout_close)} · ${sig.breakout_vol_ratio || '—'}×</span></div>
        <div class="ladder-row"><span class="k">买点</span>
          <span class="num">${esc(sig.buy_date || '次日开盘')}</span></div>
      </div>
    </div>`;
  }
  return html || '<div class="empty">—</div>';
}

function collectPlan() {
  const emotion = $('#f-emotion .chip.on');
  return {
    signal_id: Number($('#f-signal')?.value) || null,
    buy_price_limit: Number($('#f-price')?.value) || 0,
    stop_price: Number($('#f-stop')?.value) || 0,
    target_price: Number($('#f-target')?.value) || 0,
    shares: Number($('#f-shares')?.value) || 0,
    thesis: $('#f-thesis')?.value || '',
    emotion: emotion ? emotion.dataset.emotion : '',
  };
}

let previewTimer = null;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => previewPlan(), 280);
}

async function previewPlan(fill = false) {
  const payload = collectPlan();
  if (!payload.signal_id) {
    $('#gate-panel').innerHTML = '<div class="empty">先选择一只信号</div>';
    $('#btn-submit-plan').disabled = true;
    return;
  }
  try {
    const res = await api('/api/plans/preview', { method: 'POST', body: payload });
    S.preview = res;
    if (fill && res.suggest) {
      const s = res.suggest;
      if (!$('#f-price').value) $('#f-price').value = s.buy_price_limit;
      if (!$('#f-stop').value) $('#f-stop').value = s.stop_price;
      if (!$('#f-target').value) $('#f-target').value = s.target_price;
      if (!$('#f-shares').value) $('#f-shares').value = s.shares;
      return previewPlan(false);
    }
    $('#gate-panel').innerHTML = renderGate(res);
    $('#btn-submit-plan').disabled = !res.ok;
  } catch (e) {
    $('#gate-panel').innerHTML = `<div class="gate-item block"><div>
      <div class="t">检查失败</div><div class="d">${esc(e.message)}</div></div></div>`;
  }
}

async function submitPlan() {
  const payload = collectPlan();
  try {
    await api('/api/plans', { method: 'POST', body: payload });
    toast('计划已锁定。明天只按这个计划执行。', 'ok');
    $('#f-price').value = ''; $('#f-stop').value = ''; $('#f-target').value = '';
    $('#f-shares').value = ''; $('#f-thesis').value = ''; $('#f-signal').value = '';
    await refresh();
    await loadPlans();
  } catch (e) {
    if (e.data && e.data.blocks) {
      $('#gate-panel').innerHTML = renderGate(e.data);
    }
    toast(e.message, 'err');
  }
}

/* ================================================================== 交易 */
function renderTrades() {
  const open = S.trades.filter((t) => t.status === 'open');
  const closed = S.trades.filter((t) => t.status === 'closed');
  const win = closed.filter((t) => Number(t.pnl) > 0).length;

  return `
  <div class="grid g4" style="margin-bottom:6px">
    <div class="card metric"><span class="label">持仓</span>
      <span class="value num">${open.length}</span></div>
    <div class="card metric"><span class="label">已平仓</span>
      <span class="value num">${closed.length}</span></div>
    <div class="card metric"><span class="label">胜率</span>
      <span class="value num">${closed.length ? (win / closed.length * 100).toFixed(1) : '—'}%</span></div>
    <div class="card metric"><span class="label">累计盈亏</span>
      <span class="value num ${cls(closed.reduce((a, t) => a + Number(t.pnl || 0), 0))}">
        ${money(closed.reduce((a, t) => a + Number(t.pnl || 0), 0))}</span></div>
  </div>

  <div class="section-title">当前持仓</div>
  ${open.length ? `<div class="card tight"><table>
    <thead><tr><th>标的</th><th class="r">买入日</th><th class="r">成本</th><th class="r">股数</th>
      <th class="r">止损</th><th class="r">目标</th><th>合规</th><th></th></tr></thead>
    <tbody>${open.map((t) => `<tr>
      <td>${stockCell(t.code, t.name)}</td>
      <td class="r num">${esc(t.buy_date)}</td>
      <td class="r num">${n2(t.buy_price)}</td>
      <td class="r num">${t.shares}</td>
      <td class="r num">${n2(t.stop_price)}</td>
      <td class="r num">${n2(t.target_price)}</td>
      <td>${complianceTag(t)}</td>
      <td class="r"><button class="btn sm primary" data-action="close-trade" data-id="${t.id}">卖出</button></td>
    </tr>`).join('')}</tbody></table></div>`
    : `<div class="card"><div class="empty">空仓。</div></div>`}

  <div class="section-title">历史成交</div>
  ${closed.length ? `<div class="card tight"><table>
    <thead><tr><th>标的</th><th class="r">买入</th><th class="r">卖出</th><th class="r">持有</th>
      <th class="r">盈亏</th><th class="r">%</th><th>合规</th><th>复盘</th><th></th></tr></thead>
    <tbody>${closed.map((t) => `<tr>
      <td>${stockCell(t.code, t.name)}</td>
      <td class="r num">${esc(t.buy_date)}<br><span class="muted">${n2(t.buy_price)}</span></td>
      <td class="r num">${esc(t.sell_date)}<br><span class="muted">${n2(t.sell_price)}</span></td>
      <td class="r num">${t.hold_days || 1} 日</td>
      <td class="r num ${cls(t.pnl)}">${money(t.pnl)}</td>
      <td class="r num ${cls(t.pnl_pct)}">${pct(t.pnl_pct)}</td>
      <td>${complianceTag(t)}</td>
      <td>${S.reviews.some((r) => r.trade_id === t.id) ? '<span class="tag green">已复盘</span>'
        : `<span class="tag red">待复盘</span>`}</td>
      <td class="r"><button class="btn sm ghost" data-action="trade-detail" data-id="${t.id}">详情</button></td>
    </tr>`).join('')}</tbody></table></div>`
    : `<div class="card"><div class="empty">还没有平仓记录。</div></div>`}`;
}

function complianceTag(t) {
  const v = t.violations || [];
  const real = v.filter((x) => !String(x).startsWith('BACKFILL'));
  if (!real.length) return '<span class="tag green">合规</span>';
  return `<span class="tag red" title="${esc(real.join(', '))}">${real.length} 项违规</span>`;
}

/* ================================================================== 复盘 */
function renderReview() {
  const pending = S.overview.pending_reviews;
  return `
  <div class="section-title">待复盘（${pending.length}）</div>
  ${pending.length ? `<div class="grid g2">${pending.map((t) => `
    <div class="card">
      <h3>${esc(t.name)} <span class="sub">${esc(t.code)} · ${esc(t.sell_date)}</span></h3>
      <div class="kv" style="font-size:12.5px">
        <span class="k">买入</span><span class="num">${esc(t.buy_date)} @ ${n2(t.buy_price)} × ${t.shares}</span>
        <span class="k">卖出</span><span class="num">${n2(t.sell_price)}（${esc(t.exit_reason || '未填')}）</span>
        <span class="k">盈亏</span><span class="num ${cls(t.pnl)}">${money(t.pnl)} · ${pct(t.pnl_pct)}</span>
        <span class="k">止损位</span><span class="num">${n2(t.stop_price)}</span>
      </div>
      <div style="margin-top:12px">
        <button class="btn sm primary" data-action="review" data-id="${t.id}">填写复盘</button>
      </div>
    </div>`).join('')}</div>`
    : `<div class="card"><div class="empty">
        <div class="big">没有待复盘的交易</div>
        每笔平仓后 24 小时内必须复盘，否则当日纪律分扣 20。
      </div></div>`}

  <div class="section-title">复盘记录</div>
  ${S.reviews.length ? `<div class="card tight"><table>
    <thead><tr><th>日期</th><th>标的</th><th class="r">盈亏</th><th>是否按计划</th>
      <th class="r">执行分</th><th>错误标签</th><th>教训</th></tr></thead>
    <tbody>${S.reviews.map((r) => {
      const t = S.trades.find((x) => x.id === r.trade_id) || {};
      let tags = [];
      try { tags = JSON.parse(r.mistake_tags || '[]'); } catch (e) { }
      return `<tr>
        <td class="num">${esc(r.review_date)}</td>
        <td>${stockCell(t.code || '', t.name || '')}</td>
        <td class="r num ${cls(t.pnl)}">${money(t.pnl)}</td>
        <td>${r.followed_plan === '是' ? '<span class="tag green">是</span>'
          : r.followed_plan === '否' ? '<span class="tag red">否</span>'
            : '<span class="tag amber">部分</span>'}</td>
        <td class="r num" style="color:${r.score >= 80 ? 'var(--ok)' : r.score >= 60 ? 'var(--accent)' : 'var(--danger)'}">${r.score}</td>
        <td>${tags.length ? tags.map((x) => `<span class="tag grey">${esc(x)}</span>`).join(' ') : '—'}</td>
        <td style="max-width:280px;color:#b7c4d4">${esc(r.lesson)}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`
    : `<div class="card"><div class="empty">还没有复盘记录。</div></div>`}`;
}

/* ================================================================== 统计 */
function renderStats() {
  const st = S.stats;
  if (!st) return '<div class="card"><div class="empty">加载中…</div></div>';
  const a = st.all, c = st.compliant, v = st.violating;
  const ed = st.signal_edge || {};

  const cards = `
  <div class="grid g4">
    <div class="card metric"><span class="label">交易笔数</span>
      <span class="value num">${a.count}</span>
      <span class="foot">胜率 ${a.win_rate}%</span></div>
    <div class="card metric"><span class="label">期望值 / 笔</span>
      <span class="value num ${cls(a.expectancy)}">${money(a.expectancy)}</span>
      <span class="foot">盈亏比 ${a.profit_factor}:1</span></div>
    <div class="card metric"><span class="label">平均盈亏</span>
      <span class="value num" style="font-size:20px">
        <span class="up">${money(a.avg_win)}</span> / <span class="down">${money(-a.avg_loss)}</span>
      </span>
      <span class="foot">平均持有 ${a.avg_hold} 日</span></div>
    <div class="card metric"><span class="label">最大回撤</span>
      <span class="value num down">${st.max_drawdown}%</span>
      <span class="foot">最大连亏 ${a.max_loss_streak} 笔</span></div>
  </div>`;

  const compare = `
  <div class="section-title">合规 vs 违规 —— 这就是答案</div>
  <div class="card tight">
    <table>
      <thead><tr><th>分组</th><th class="r">笔数</th><th class="r">胜率</th>
        <th class="r">期望值/笔</th><th class="r">总盈亏</th><th class="r">平均持有</th></tr></thead>
      <tbody>
        <tr><td><span class="tag green">按计划执行</span></td>
          <td class="r num">${c.count}</td><td class="r num">${c.win_rate}%</td>
          <td class="r num ${cls(c.expectancy)}">${money(c.expectancy)}</td>
          <td class="r num ${cls(c.total_pnl)}">${money(c.total_pnl)}</td>
          <td class="r num">${c.avg_hold} 日</td></tr>
        <tr><td><span class="tag red">违规交易</span></td>
          <td class="r num">${v.count}</td><td class="r num">${v.win_rate}%</td>
          <td class="r num ${cls(v.expectancy)}">${money(v.expectancy)}</td>
          <td class="r num ${cls(v.total_pnl)}">${money(v.total_pnl)}</td>
          <td class="r num">${v.avg_hold} 日</td></tr>
        <tr style="background:rgba(255,255,255,.03)">
          <td><b>差距</b></td><td class="r num">—</td>
          <td class="r num">${(c.win_rate - v.win_rate).toFixed(1)} pt</td>
          <td class="r num ${cls(c.expectancy - v.expectancy)}"><b>${money(c.expectancy - v.expectancy)}</b></td>
          <td class="r num">—</td><td class="r num">—</td></tr>
      </tbody>
    </table>
    ${c.count + v.count === 0 ? '<div class="hint" style="padding:10px 9px">还没有已平仓交易。当你积累 10 笔以上，这张表会明确告诉你：破戒到底让你损失了多少钱。</div>' : ''}
  </div>`;

  const curve = st.curve.length > 1 ? `
  <div class="section-title">权益曲线</div>
  <div class="card">${equitySvg(st.curve, st.snapshot.total_capital)}</div>` : '';

  const monthly = st.monthly.length ? `
  <div class="section-title">月度表现</div>
  <div class="card tight"><table>
    <thead><tr><th>月份</th><th class="r">笔数</th><th class="r">胜率</th><th class="r">盈亏</th></tr></thead>
    <tbody>${st.monthly.map((m) => `<tr>
      <td class="num">${esc(m.month)}</td><td class="r num">${m.count}</td>
      <td class="r num">${m.win_rate}%</td>
      <td class="r num ${cls(m.pnl)}">${money(m.pnl)}</td></tr>`).join('')}</tbody></table></div>` : '';

  const tags = st.mistake_tags.length ? `
  <div class="section-title">你最容易犯的错</div>
  <div class="card">
    ${st.mistake_tags.map(([tag, n]) => {
      const max = st.mistake_tags[0][1] || 1;
      return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        <span style="width:130px;font-size:12.5px">${esc(tag)}</span>
        <div class="bar" style="flex:1"><i style="width:${n / max * 100}%;background:var(--danger)"></i></div>
        <span class="num muted" style="width:26px;text-align:right">${n}</span>
      </div>`;
    }).join('')}
  </div>` : '';

  const sc = st.strategy || {};
  const health = st.funnel.signals ? `
  <div class="section-title">战法体检 · 信号本身的表现 <span class="sub">不管你有没有做</span></div>
  <div class="grid g2">
    <div class="card tight"><table>
      <thead><tr><th>买入后</th><th class="r">样本</th><th class="r">胜率</th>
        <th class="r">平均收益</th><th class="r">最好</th><th class="r">最差</th></tr></thead>
      <tbody>
        ${['d1', 'd3', 'd5'].map((k, i) => {
          const h = sc[k] || {};
          return `<tr><td>第 ${[1, 3, 5][i]} 日收盘</td>
            <td class="r num">${h.n || 0}</td>
            <td class="r num">${h.win_rate || 0}%</td>
            <td class="r num ${cls(h.avg)}">${pct(h.avg)}</td>
            <td class="r num up">${pct(h.best)}</td>
            <td class="r num down">${pct(h.worst)}</td></tr>`;
        }).join('')}
      </tbody>
    </table>
    <div class="hint" style="padding:10px 9px">
      买入价按买点当日开盘价计。平均最大回撤 ${pct(sc.avg_adverse)}。
      这就是这套战法在你选股之外的"基础概率"——它决定你该不该重仓。
    </div>
    </div>
    <div class="card tight"><div class="kv">
      <span class="k">已触发信号</span><span class="num">${st.funnel.triggered} / ${st.funnel.signals}</span>
      <span class="k">生成计划</span><span class="num">${st.funnel.plans}</span>
      <span class="k">实际成交</span><span class="num">${st.funnel.trades}</span>
      <span class="k">完成复盘</span><span class="num">${st.funnel.reviewed}</span>
      <span class="k">执行率</span><span class="num">${st.funnel.plans ? (st.funnel.trades / st.funnel.plans * 100).toFixed(0) : 0}%</span>
    </div>
    <div class="hint" style="margin-top:10px;line-height:1.8">
      信号 → 计划 → 成交，每一层都会流失。流失在哪里，问题就在哪里。
    </div></div>
  </div>` : '';

  return cards + compare + curve + monthly + tags + health;
}

function equitySvg(curve, base) {
  const w = 1000, h = 200, pad = 10;
  const vals = curve.map((p) => p.equity);
  const min = Math.min(base, ...vals), max = Math.max(base, ...vals);
  const span = (max - min) || 1;
  const X = (i) => pad + (i / Math.max(1, curve.length - 1)) * (w - pad * 2);
  const Y = (v) => h - pad - ((v - min) / span) * (h - pad * 2);
  const path = curve.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.equity).toFixed(1)}`).join(' ');
  const baseY = Y(base);
  const last = vals[vals.length - 1];
  const color = last >= base ? 'var(--up)' : 'var(--down)';
  return `<svg class="mini-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <line x1="${pad}" y1="${baseY}" x2="${w - pad}" y2="${baseY}"
      stroke="#2b3648" stroke-dasharray="4 4" stroke-width="1"/>
    <path d="${path} L${X(curve.length - 1)},${h - pad} L${pad},${h - pad} Z"
      fill="${last >= base ? 'rgba(240,71,92,.10)' : 'rgba(23,185,120,.10)'}"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="2"/>
    <text x="${pad}" y="${pad + 10}" fill="#8290a3" font-size="12">
      起始 ${money(base)} → 当前 ${money(last)}
    </text>
  </svg>`;
}

/* ================================================================== 设置 */
function renderSettings() {
  const st = S.settings;
  if (!st) return '<div class="card"><div class="empty">加载中…</div></div>';
  const s = st.strategy, r = st.risk;
  const o = S.overview;

  const num = (id, label, val, step, hint) => `
    <div class="field">
      <label>${esc(label)}${hint ? ` <span class="hint">${esc(hint)}</span>` : ''}</label>
      <input id="${id}" type="number" step="${step}" value="${val}" />
    </div>`;

  return `
  <div class="grid g2">
    <div class="card">
      <h3>战法参数 <span class="sub">改变信号数量</span></h3>
      <div class="form-grid">
        ${num('s-gap', 'T+1 高开幅度 ≥', (s.min_gap_pct * 100).toFixed(1), 0.1, '%')}
        ${num('s-body', '阴线实体 ≥', (s.min_body_pct * 100).toFixed(1), 0.1, '%')}
        ${num('s-window', '突破窗口', s.window, 1, '交易日')}
        ${num('s-vol', '放量倍数 ≥', s.vol_ratio, 0.1, '倍均量')}
        ${num('s-volma', '均量基准天数', s.vol_ma_days, 1, '日')}
        ${num('s-chase', '追高上限', (s.max_chase_pct * 100).toFixed(0), 1, '% 相对突破日收盘')}
        <div class="field wide">
          <label>参与市场</label>
          <div class="chips" id="s-boards">
            ${[['main', '主板'], ['chinext', '创业板'], ['star', '科创板'], ['bj', '北交所']].map(([k, label]) => `
              <button type="button" class="chip ${(s.boards || []).includes(k) ? 'on' : ''}"
                data-board="${k}">${label}</button>`).join('')}
          </div>
        </div>
        <div class="field wide">
          <label>排除</label>
          <label style="display:flex;gap:8px;align-items:center;color:var(--text)">
            <input type="checkbox" id="s-st" ${s.exclude_st ? 'checked' : ''}
              style="width:auto" /> 排除 ST 股
          </label>
          <label style="display:flex;gap:8px;align-items:center;color:var(--text)">
            <input type="checkbox" id="s-firstvol" ${s.first_break_must_volume ? 'checked' : ''}
              style="width:auto" /> 第一次涨停突破必须放量，否则形态作废
          </label>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>风控参数 <span class="sub">决定你能不能下单</span></h3>
      <div class="form-grid">
        ${num('r-capital', '账户总资金', r.total_capital, 10000, '元')}
        ${num('r-risk', '单笔风险', (r.risk_per_trade_pct * 100).toFixed(1), 0.1, '% 总资金')}
        ${num('r-pos', '单票仓位上限', (r.max_position_pct * 100).toFixed(0), 1, '%')}
        ${num('r-stop', '止损幅度上限', (r.max_stop_pct * 100).toFixed(0), 1, '%')}
        ${num('r-rr', '最低盈亏比', r.min_rr, 0.1, ':1')}
        ${num('r-plans', '每日计划上限', r.max_plans_per_day, 1, '个')}
        ${num('r-holds', '同时持仓上限', r.max_new_positions, 1, '只')}
        ${num('r-dayloss', '当日停手线', (r.daily_stop_loss_pct * 100).toFixed(1), 0.1, '%')}
        ${num('r-streak', '连亏冷静阈值', r.loss_streak_cooldown, 1, '笔')}
        ${num('r-cooldown', '冷静期', r.cooldown_days, 1, '交易日')}
      </div>
    </div>
  </div>

  <div style="margin-top:14px;display:flex;gap:8px">
    <button class="btn primary" id="btn-save-settings">保存参数</button>
    <span class="hint" style="align-self:center">保存后立即生效，下一次扫描按新参数执行</span>
  </div>

  <div class="section-title">数据</div>
  <div class="card">
    <div class="kv">
      <span class="k">行情覆盖</span><span class="num">${o.data.coverage[0]} / ${o.data.coverage[1]} 只</span>
      <span class="k">最新交易日</span><span class="num">${esc(o.data.trade_date || '—')}</span>
      <span class="k">上次更新</span><span class="num">${esc(((st.last_update && st.last_update.at) || '从未').replace('T', ' ').slice(0, 16))}</span>
    </div>
    <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn sm" data-action="update-data" data-mode="incremental">增量更新</button>
      <button class="btn sm ghost" data-action="update-data" data-mode="full">全量重拉</button>
      <button class="btn sm ghost" data-action="scan-now">重新扫描</button>
    </div>
  </div>`;
}

/* ================================================================== 交互 */
document.addEventListener('click', async (e) => {
  const tab = e.target.closest('#tabs .tab');
  if (tab) {
    await goTab(tab.dataset.tab);
    return;
  }

  const el = e.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  const id = Number(el.dataset.id);

  if (action === 'plan') {
    S.pendingSignal = id;
    await goTab('plan');
  } else if (action === 'signal-tab') {
    S.signalTab = el.dataset.tab;
    render();
  } else if (action === 'chart') {
    await openChart(el.dataset.code);
  } else if (action === 'chart-signal') {
    await openChart(el.dataset.code, signalAnnot(findSignal(id)));
  } else if (action === 'chart-days') {
    if (!S.chart) return;
    S.chart.days = Number(el.dataset.days);
    await openChart(S.chart.code, S.chart.annot);
  } else if (action === 'ind') {
    if (!S.chart) return;
    S.chart.ind = S.chart.ind || { macd: true, kdj: true, rsi: true };
    const k = el.dataset.ind;
    S.chart.ind[k] = !S.chart.ind[k];
    el.classList.toggle('on', S.chart.ind[k]);
    const box = $('#kline-box');
    if (box && typeof KLine !== 'undefined') {
      KLine.mount(box, S.chart.resp, S.chart.annot || {}, S.chart.ind);
    }
  } else if (action === 'execute') {
    await openExecuteModal(id);
  } else if (action === 'cancel-plan') {
    try { await api(`/api/plans/${id}/cancel`, { method: 'POST' }); toast('计划已作废', 'ok'); await loadPlans(); await refresh(); }
    catch (err) { toast(err.message, 'err'); }
  } else if (action === 'close-trade') {
    await openCloseModal(id);
  } else if (action === 'review') {
    await openReviewModal(id);
  } else if (action === 'trade-detail') {
    await openTradeDetail(id);
  } else if (action === 'update-data') {
    startUpdate(el.dataset.mode);
  } else if (action === 'scan-now') {
    runScan();
  }
});

document.addEventListener('input', (e) => {
  if (['f-price', 'f-stop', 'f-target', 'f-shares', 'f-thesis'].includes(e.target.id)) schedulePreview();
});
document.addEventListener('change', (e) => {
  if (e.target.id === 'f-signal') previewPlan(true);
});

document.addEventListener('click', (e) => {
  const chip = e.target.closest('#f-emotion .chip');
  if (chip) {
    $$('#f-emotion .chip').forEach((c) => c.classList.remove('on'));
    chip.classList.add('on');
    return;
  }
  const board = e.target.closest('[data-board]');
  if (board) { board.classList.toggle('on'); }
  const tag = e.target.closest('[data-tag]');
  if (tag) { tag.classList.toggle('on'); }
});

$('#modal-mask').addEventListener('click', (e) => {
  if (e.target.id === 'modal-mask' || e.target.dataset.close !== undefined) closeModal();
});

$('#btn-scan').addEventListener('click', runScan);
$('#btn-update').addEventListener('click', () => startUpdate('incremental'));

document.addEventListener('click', async (e) => {
  if (e.target.id === 'btn-submit-plan') await submitPlan();
  if (e.target.id === 'btn-suggest') {
    const t = S.preview && S.preview.suggest;
    if (!t) { toast('先选择信号', 'err'); return; }
    $('#f-price').value = t.buy_price_limit;
    $('#f-stop').value = t.stop_price;
    $('#f-target').value = t.target_price;
    $('#f-shares').value = t.shares;
    previewPlan();
  }
  if (e.target.id === 'btn-save-settings') await saveSettings();
});

/* ------------------------------------------------------------- 数据任务 */
async function startUpdate(mode) {
  try {
    await api('/api/data/update', { method: 'POST', body: { mode } });
    toast('开始拉取行情…');
    S.jobActive = true;
    $('#jobbar').hidden = false;
    tickJob();
  } catch (e) { toast(e.message, 'err'); }
}

async function tickJob() {
  clearTimeout(S.jobTimer);
  try {
    const st = await api('/api/data/status');
    const j = st.job;
    if (j.running) {
      $('#jobbar').hidden = false;
      const p = j.total ? Math.round(j.done / j.total * 100) : 0;
      $('#jobbar-fill').style.width = p + '%';
      $('#jobbar-text').textContent = `${j.message} · ${j.done}/${j.total}（${p}%）`;
      S.jobTimer = setTimeout(tickJob, 1200);
    } else if (S.jobActive) {
      S.jobActive = false;
      $('#jobbar-text').textContent = j.message || '完成';
      $('#jobbar-fill').style.width = '100%';
      setTimeout(() => { $('#jobbar').hidden = true; }, 2500);
      await runScan(true);
    } else {
      $('#jobbar').hidden = true;
    }
  } catch (e) { /* 忽略 */ }
}

async function runScan(silent) {
  try {
    $('#jobbar').hidden = false;
    $('#jobbar-text').textContent = '扫描全市场形态…';
    $('#jobbar-fill').style.width = '60%';
    const res = await api('/api/scan', { method: 'POST', body: {} });
    $('#jobbar-fill').style.width = '100%';
    $('#jobbar-text').textContent =
      `扫描 ${res.scanned} 只 · 命中 ${res.signals} · 可下手 ${res.today_actionable}`;
    setTimeout(() => { $('#jobbar').hidden = true; }, 2600);
    await refresh();
    if (S.tab === 'signals') render();
    if (!silent) toast(`扫描完成：${res.today_actionable} 只可下手`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* ------------------------------------------------------------- 数据加载 */
async function loadPlans() {
  const r = await api('/api/plans');
  S.plans = r.plans;
}
async function loadTrades() {
  const [t, rv] = await Promise.all([api('/api/trades'), api('/api/reviews/pending')]);
  S.trades = t.trades;
  S.reviews = rv.pending;
}
async function loadStats() { S.stats = await api('/api/stats'); }
async function loadSettings() { S.settings = await api('/api/settings'); }

/* ------------------------------------------------------------- 弹窗流程 */
async function openExecuteModal(planId) {
  const plan = S.plans.find((p) => p.id === planId);
  if (!plan) return;
  openModal('记录成交', `
    <div class="kv" style="margin-bottom:14px">
      <span class="k">标的</span><span>${esc(plan.name)} ${esc(plan.code)}</span>
      <span class="k">计划买点</span><span class="num">${esc(plan.buy_date)}</span>
      <span class="k">价格上限</span><span class="num">${n2(plan.buy_price_limit)}</span>
      <span class="k">计划股数</span><span class="num">${plan.shares}</span>
      <span class="k">止损 / 目标</span><span class="num">${n2(plan.stop_price)} / ${n2(plan.target_price)}</span>
    </div>
    <div class="form-grid">
      <div class="field"><label>实际成交价</label>
        <input id="x-price" type="number" step="0.01" value="${plan.buy_price_limit}" /></div>
      <div class="field"><label>股数</label>
        <input id="x-shares" type="number" step="100" value="${plan.shares}" /></div>
      <div class="field wide"><label>备注</label>
        <input id="x-note" type="text" placeholder="可选" /></div>
    </div>
    <div id="x-errors" style="margin-top:12px"></div>
    <div class="modal-actions">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn primary" id="x-submit">确认买入</button>
    </div>
  `);
  $('#x-submit').onclick = async () => {
    const payload = {
      plan_id: planId,
      buy_date: plan.buy_date,
      buy_price: Number($('#x-price').value),
      shares: Number($('#x-shares').value),
      note: $('#x-note').value,
    };
    try {
      await api('/api/trades', { method: 'POST', body: payload });
      closeModal();
      toast('已记录成交。止损：' + n2(plan.stop_price), 'ok');
      await loadPlans(); await loadTrades(); await refresh();
    } catch (e) {
      if (e.data && e.data.blocks) {
        $('#x-errors').innerHTML = `<div class="gate">${e.data.blocks.map((b) => `
          <div class="gate-item block"><div><div class="t">✕ ${esc(b.title)}</div>
          <div class="d">${esc(b.detail)}</div></div></div>`).join('')}</div>`;
      } else { toast(e.message, 'err'); }
    }
  };
}

async function openCloseModal(tradeId) {
  const d = await api(`/api/trades/${tradeId}`);
  const t = d;
  const last = d.bars && d.bars.length ? d.bars[d.bars.length - 1].close : t.buy_price;
  openModal('卖出', `
    <div class="kv" style="margin-bottom:14px">
      <span class="k">标的</span><span>${esc(t.name)} ${esc(t.code)}</span>
      <span class="k">成本</span><span class="num">${n2(t.buy_price)} × ${t.shares}</span>
      <span class="k">最新收盘</span><span class="num">${n2(last)}</span>
      <span class="k">止损 / 目标</span><span class="num">${n2(t.stop_price)} / ${n2(t.target_price)}</span>
    </div>
    <div class="form-grid">
      <div class="field"><label>卖出价</label>
        <input id="c-price" type="number" step="0.01" value="${n2(last)}" /></div>
      <div class="field"><label>卖出日期</label>
        <input id="c-date" type="date" value="${esc(S.overview.trade_date)}" /></div>
      <div class="field wide"><label>卖出原因（必填）</label>
        <input id="c-reason" type="text" placeholder="按计划止盈 / 破止损 / 形态失败 …" /></div>
    </div>
    <div id="c-errors" style="margin-top:12px"></div>
    <div class="modal-actions">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn primary" id="c-submit">确认卖出</button>
    </div>
  `);
  $('#c-submit').onclick = async () => {
    try {
      const r = await api(`/api/trades/${tradeId}/close`, {
        method: 'POST',
        body: {
          sell_price: Number($('#c-price').value),
          sell_date: $('#c-date').value,
          exit_reason: $('#c-reason').value,
        },
      });
      closeModal();
      toast(`已平仓 ${money(r.pnl)}（${pct(r.pnl_pct)}）`, r.pnl >= 0 ? 'ok' : '');
      await loadTrades(); await refresh();
    } catch (e) {
      if (e.data && e.data.blocks) {
        $('#c-errors').innerHTML = `<div class="gate">${e.data.blocks.map((b) => `
          <div class="gate-item block"><div><div class="t">✕ ${esc(b.title)}</div>
          <div class="d">${esc(b.detail)}</div></div></div>`).join('')}</div>`;
      } else { toast(e.message, 'err'); }
    }
  };
}

async function openReviewModal(tradeId) {
  const t = S.overview.pending_reviews.find((x) => x.id === tradeId)
    || S.trades.find((x) => x.id === tradeId) || {};
  const tags = S.overview.mistake_tags || [];
  openModal('复盘', `
    <div class="kv" style="margin-bottom:14px">
      <span class="k">标的</span><span>${esc(t.name)} ${esc(t.code)}</span>
      <span class="k">盈亏</span>
      <span class="num ${cls(t.pnl)}">${money(t.pnl)} · ${pct(t.pnl_pct)}</span>
      <span class="k">卖出原因</span><span>${esc(t.exit_reason || '未填')}</span>
    </div>

    <div class="field" style="margin-bottom:12px">
      <label>这笔交易是否按计划执行？</label>
      <div class="chips" id="v-followed">
        <button type="button" class="chip on" data-follow="是">是</button>
        <button type="button" class="chip" data-follow="部分">部分</button>
        <button type="button" class="chip" data-follow="否">否</button>
      </div>
    </div>

    <div class="field" style="margin-bottom:12px">
      <label>当下的情绪</label>
      <div class="chips" id="v-emotion">
        ${(S.overview.emotions || []).map((x, i) =>
          `<button type="button" class="chip ${i === 0 ? 'on' : ''}" data-emo="${esc(x)}">${esc(x)}</button>`).join('')}
      </div>
    </div>

    <div class="field" style="margin-bottom:12px">
      <label>错在哪（可多选，用于统计你的高频错误）</label>
      <div class="chips" id="v-tags">
        ${tags.map((x) => `<button type="button" class="chip" data-tag="${esc(x)}">${esc(x)}</button>`).join('')}
      </div>
    </div>

    <div class="field">
      <label>教训（至少 8 个字）</label>
      <textarea id="v-lesson" placeholder="下次遇到同样的形态/同样的冲动，我具体会怎么做？"></textarea>
    </div>
    <div id="v-errors" style="margin-top:10px"></div>
    <div class="modal-actions">
      <button class="btn ghost" data-close>取消</button>
      <button class="btn primary" id="v-submit">保存复盘</button>
    </div>
  `);

  $('#v-followed').onclick = (e) => {
    const b = e.target.closest('[data-follow]'); if (!b) return;
    $$('#v-followed .chip').forEach((c) => c.classList.remove('on')); b.classList.add('on');
  };
  $('#v-emotion').onclick = (e) => {
    const b = e.target.closest('[data-emo]'); if (!b) return;
    $$('#v-emotion .chip').forEach((c) => c.classList.remove('on')); b.classList.add('on');
  };

  $('#v-submit').onclick = async () => {
    const followed = $('#v-followed .chip.on')?.dataset.follow || '部分';
    const emotion = $('#v-emotion .chip.on')?.dataset.emo || '';
    const selected = $$('#v-tags .chip.on').map((c) => c.dataset.tag);
    try {
      const r = await api('/api/reviews', {
        method: 'POST',
        body: {
          trade_id: tradeId, followed_plan: followed, emotion,
          mistake_tags: selected, exit_quality: 3, lesson: $('#v-lesson').value,
        },
      });
      closeModal();
      toast(`复盘已保存，执行分 ${r.score}`, 'ok');
      await loadTrades(); await refresh();
      if (S.tab === 'review') render();
    } catch (e) {
      if (e.data && e.data.blocks) {
        $('#v-errors').innerHTML = `<div class="gate">${e.data.blocks.map((b) => `
          <div class="gate-item block"><div><div class="t">✕ ${esc(b.title)}</div>
          <div class="d">${esc(b.detail)}</div></div></div>`).join('')}</div>`;
      } else { toast(e.message, 'err'); }
    }
  };
}

async function openTradeDetail(id) {
  const d = await api(`/api/trades/${id}`);
  const plan = d.plan || {};
  openModal('交易详情', `
    <div class="kv" style="margin-bottom:12px">
      <span class="k">标的</span><span>${esc(d.name)} ${esc(d.code)}</span>
      <span class="k">买入</span><span class="num">${esc(d.buy_date)} @ ${n2(d.buy_price)} × ${d.shares}</span>
      <span class="k">卖出</span><span class="num">${esc(d.sell_date || '—')} @ ${n2(d.sell_price)}</span>
      <span class="k">盈亏</span><span class="num ${cls(d.pnl)}">${money(d.pnl)} · ${pct(d.pnl_pct)}</span>
      <span class="k">卖出原因</span><span>${esc(d.exit_reason || '—')}</span>
    </div>
    ${plan.buy_price_limit ? `<div class="card tight" style="background:rgba(255,255,255,.02);margin-bottom:12px">
      <div class="kv">
        <span class="k">计划价上限</span><span class="num">${n2(plan.buy_price_limit)}</span>
        <span class="k">计划止损</span><span class="num">${n2(plan.stop_price)}</span>
        <span class="k">计划目标</span><span class="num">${n2(plan.target_price)}</span>
        <span class="k">计划理由</span><span>${esc(plan.thesis || '—')}</span>
      </div></div>` : ''}
    <div id="m-kline"></div>
    <div class="modal-actions"><button class="btn ghost" data-close>关闭</button></div>
  `, true);

  try {
    const resp = await fetchKline(d.code, 120);
    const annot = { marks: [], lines: [] };
    if (d.buy_date) annot.marks.push({ date: d.buy_date, label: '买入', color: '#4f8cf7' });
    if (d.sell_date) annot.marks.push({ date: d.sell_date, label: '卖出', color: '#f0475c' });
    if (d.buy_price) annot.lines.push({ price: d.buy_price, label: '成本', color: '#4f8cf7' });
    if (d.stop_price) annot.lines.push({ price: d.stop_price, label: '止损', color: '#f0475c' });
    if (d.target_price) annot.lines.push({ price: d.target_price, label: '目标', color: '#17b978' });
    if (typeof KLine !== 'undefined') KLine.mount($('#m-kline'), resp, annot);
  } catch (e) { /* 没有行情数据就不画图 */ }
}

async function doSearch(q) {
  if (!q) return;
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
    if (!r.items.length) { toast(`没找到「${q}」，检查代码或名称`, 'err'); return; }
    if (r.items.length === 1) { await openChart(r.items[0].code); return; }
    const box = $('#q-results');
    if (box) {
      box.innerHTML = r.items.map((it) => `<button class="chip" data-action="chart"
        data-code="${esc(it.code)}">${esc(it.name)} <span class="muted">${esc(it.code)}</span></button>`).join('');
    }
  } catch (e) { toast(e.message, 'err'); }
}

document.addEventListener('keydown', (e) => {
  if (e.target.id === 'q-search' && e.key === 'Enter') {
    doSearch(e.target.value.trim());
  }
});

async function saveSettings() {
  const boards = $$('#s-boards .chip.on').map((c) => c.dataset.board);
  const strategy = {
    min_gap_pct: Number($('#s-gap').value) / 100,
    min_body_pct: Number($('#s-body').value) / 100,
    window: Math.round(Number($('#s-window').value)),
    vol_ratio: Number($('#s-vol').value),
    vol_ma_days: Math.round(Number($('#s-volma').value)),
    max_chase_pct: Number($('#s-chase').value) / 100,
    boards,
    exclude_st: $('#s-st').checked,
    first_break_must_volume: $('#s-firstvol').checked,
  };
  const risk = {
    total_capital: Number($('#r-capital').value),
    risk_per_trade_pct: Number($('#r-risk').value) / 100,
    max_position_pct: Number($('#r-pos').value) / 100,
    max_stop_pct: Number($('#r-stop').value) / 100,
    min_rr: Number($('#r-rr').value),
    max_plans_per_day: Math.round(Number($('#r-plans').value)),
    max_new_positions: Math.round(Number($('#r-holds').value)),
    daily_stop_loss_pct: Number($('#r-dayloss').value) / 100,
    loss_streak_cooldown: Math.round(Number($('#r-streak').value)),
    cooldown_days: Math.round(Number($('#r-cooldown').value)),
  };
  try {
    await api('/api/settings', { method: 'PUT', body: { strategy, risk } });
    toast('参数已保存', 'ok');
    await refresh();
  } catch (e) { toast(e.message, 'err'); }
}

boot();
