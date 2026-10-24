/* Sentinel – Fraud Monitor dashboard (vanilla JS) */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function api(url, opts = {}) {
  const r = await fetch(url, {
    method: opts.method || 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || 'Request failed');
  return j;
}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const inr = n => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const inrShort = n => {
  n = Number(n || 0);
  if (n >= 1e7) return '₹' + (n / 1e7).toFixed(2) + ' Cr';
  if (n >= 1e5) return '₹' + (n / 1e5).toFixed(2) + ' L';
  return inr(n);
};
const label = v => String(v || '').toLowerCase().replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
const pill = v => `<span class="pill p-${esc(v)}">${esc(label(v))}</span>`;
const fmtDT = iso => new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
const ago = iso => {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (m < 1) return 'just now';
  if (m < 60) return m + 'm ago';
  if (m < 1440) return Math.round(m / 60) + 'h ago';
  return Math.round(m / 1440) + 'd ago';
};
const SEV = { LOW: '#3b82f6', MEDIUM: '#f5a524', HIGH: '#f97339', CRITICAL: '#e5484d' };
const GRADS = ['#6c5ce7,#8f7dff', '#ff7a59,#ffb86b', '#17b26a,#5fd6a0', '#3b82f6,#7db4ff', '#e5484d,#ff8a8e', '#a855f7,#d19bff'];
const initials = n => String(n || '?').split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
const grad = n => { let h = 0; for (const c of String(n)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return `linear-gradient(135deg,${GRADS[h % GRADS.length]})`; };
const who = (name, sub) => `<div class="who"><div class="av" style="background:${grad(name)}">${esc(initials(name))}</div><div><b>${esc(name || 'Unknown')}</b><small>${esc(sub || '')}</small></div></div>`;
const scoreBar = (s, sev) => {
  const c = SEV[sev] || (s >= 90 ? SEV.CRITICAL : s >= 70 ? SEV.HIGH : s >= 50 ? SEV.MEDIUM : s >= 30 ? SEV.LOW : '#c9cde0');
  return `<div class="score"><div class="bar"><i style="width:${s}%;background:${c}"></i></div><b>${s}</b></div>`;
};
const emptyRow = (cols, t, s) => `<tr><td colspan="${cols}"><div class="empty"><b>${t}</b>${s}</div></td></tr>`;

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('on'), 2800);
}

/* ------------------------------------------------------------------ state / nav */
const PAGES = {
  dashboard: ['Overview', 'Live view of transaction risk across all accounts'],
  alerts: ['Alerts', 'Review potentially fraudulent transactions and record a decision'],
  transactions: ['Transactions', 'Search the full ledger — deposits, withdrawals and transfers'],
  rules: ['Detection rules', 'Configurable rules that score every transaction'],
  audit: ['Audit log', 'Immutable history of important system actions'],
};
const state = { page: 'dashboard', trendDays: 14, alertStatus: '', txnPage: 1, charts: {} };

function go(page) {
  state.page = page;
  $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.page === page));
  $('#pageTitle').textContent = PAGES[page][0];
  $('#pageSub').textContent = PAGES[page][1];
  refresh();
}
function refresh() {
  ({ dashboard: loadDashboard, alerts: loadAlerts, transactions: loadTxns, rules: loadRules, audit: loadAudit })[state.page]();
  updateBadge();
}
async function updateBadge() {
  try {
    const s = await api('/api/summary');
    const b = $('#navAlertCount'); b.textContent = s.open_alerts; b.dataset.n = s.open_alerts;
  } catch (e) { /* ignore */ }
}
$('#nav').addEventListener('click', e => { const a = e.target.closest('a'); if (a) go(a.dataset.page); });
document.addEventListener('click', e => { const g = e.target.closest('[data-go]'); if (g) go(g.dataset.go); });

/* ------------------------------------------------------------------ dashboard */
async function loadDashboard() {
  const [s, trend, sev, alerts, top] = await Promise.all([
    api('/api/summary'), api('/api/trends?days=' + state.trendDays), api('/api/severity'),
    api('/api/alerts?limit=40'), api('/api/top-accounts'),
  ]);

  const ico = {
    vol: '<path d="M3 17l5-5 4 4 8-9"/><path d="M15 7h5v5"/>',
    alert: '<path d="M12 3l9.5 17h-19L12 3z"/><path d="M12 10v4M12 17.5v.01"/>',
    block: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    ok: '<path d="M12 3l8 3v6c0 4.5-3.2 8.3-8 9-4.8-.7-8-4.5-8-9V6l8-3z"/><path d="M8.5 12.2l2.5 2.5 4.5-5"/>',
  };
  const kpi = (cls, i, l, v, sub) => `<div class="kpi ${cls}"><div class="kpi-ico"><svg viewBox="0 0 24 24">${ico[i]}</svg></div><div class="lbl">${l}</div><div class="val">${v}</div><div class="sub">${sub}</div></div>`;
  $('#kpis').innerHTML =
    kpi('k-brand', 'vol', 'Volume · last 30 days', inrShort(s.volume_30d), `${s.txn_30d} transactions processed`) +
    kpi('k-high', 'alert', 'Open alerts', s.open_alerts, s.urgent_alerts ? `<b class="p-CRITICAL" style="background:var(--crit-bg);color:var(--crit)">${s.urgent_alerts} urgent</b> high / critical` : 'No urgent alerts') +
    kpi('k-crit', 'block', 'Blocked by engine', inrShort(s.blocked_amount), `${s.blocked_count} transaction${s.blocked_count === 1 ? '' : 's'} stopped before funds moved`) +
    kpi('k-ok', 'ok', 'Confirmed fraud', s.confirmed, `${s.dismissed} dismissed · ${s.frozen_accounts} account${s.frozen_accounts === 1 ? '' : 's'} frozen`);
  const b = $('#navAlertCount'); b.textContent = s.open_alerts; b.dataset.n = s.open_alerts;

  drawTrend(trend);
  drawSeverity(sev);

  const pending = alerts.filter(a => a.status === 'OPEN' || a.status === 'UNDER_REVIEW').slice(0, 6);
  $('#recentAlerts').innerHTML = pending.length ? pending.map(a => `
    <div class="row-item click" data-alert="${a.alert_id}">
      <div class="rank" style="background:${SEV[a.severity]}1f;color:${SEV[a.severity]}">${a.risk_score}</div>
      <div class="grow"><b>${esc(a.customer_name)} · ${inr(a.amount)}</b><small>${esc(a.reasons.split(' | ')[0])}</small></div>
      <div style="text-align:right">${pill(a.severity)}<small class="muted" style="display:block;margin-top:4px">${ago(a.created_at)}</small></div>
    </div>`).join('') : '<div class="empty"><b>All clear</b>No alerts are waiting for review.</div>';

  $('#topAccounts').innerHTML = top.length ? top.map((a, i) => `
    <div class="row-item">
      <div class="rank ${+a.rank === 1 ? 'r1' : ''}">${a.rank}</div>
      <div class="grow"><b>${esc(a.full_name)}</b><small>${esc(a.account_no)} · ${a.alerts} alert${a.alerts > 1 ? 's' : ''}</small></div>
      <div style="text-align:right"><b class="amt">${a.total_risk}</b><small class="muted" style="display:block">${a.account_status === 'FROZEN' ? '❄ frozen' : 'risk pts'}</small></div>
    </div>`).join('') : '<div class="empty">No risky accounts yet.</div>';
}

function drawTrend(rows) {
  if (!window.Chart) return;
  Chart.defaults.font.family = "'Plus Jakarta Sans', system-ui, -apple-system, 'Segoe UI', sans-serif";
  state.charts.trend?.destroy();
  const labels = rows.map(r => new Date(r.day + 'T00:00').toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }));
  state.charts.trend = new Chart($('#trendChart'), {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Volume', data: rows.map(r => r.volume), backgroundColor: 'rgba(108,92,231,.88)', hoverBackgroundColor: '#4b3fd1', borderRadius: 8, borderSkipped: false, maxBarThickness: 26, yAxisID: 'y' },
        { type: 'line', label: 'Alerts', data: rows.map(r => r.alerts), borderColor: '#e5484d', backgroundColor: '#e5484d', tension: .4, pointRadius: 4, pointBorderColor: '#fff', pointBorderWidth: 2, borderWidth: 3, yAxisID: 'y1' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', align: 'end', labels: { usePointStyle: true, boxWidth: 8, font: { weight: '600' } } },
        tooltip: { backgroundColor: '#1a1c2e', padding: 12, cornerRadius: 12, callbacks: { label: c => c.dataset.label === 'Volume' ? ' Volume ' + inr(c.raw) : ' Alerts ' + c.raw } },
      },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: '#8c91ab', font: { weight: '600' }, maxRotation: 0, autoSkip: true, maxTicksLimit: 15 } },
        y: { grid: { color: '#eceef7' }, border: { display: false }, ticks: { color: '#8c91ab', callback: v => v >= 1e5 ? (v / 1e5) + 'L' : v / 1e3 + 'k' } },
        y1: { position: 'right', grid: { display: false }, border: { display: false }, beginAtZero: true, ticks: { precision: 0, color: '#e5484d' }, suggestedMax: 5 },
      },
    },
  });
}

function drawSeverity(rows) {
  const order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
  const m = Object.fromEntries(rows.map(r => [r.severity, r.n]));
  const total = order.reduce((t, k) => t + (m[k] || 0), 0);
  $('#sevTotal').textContent = total;
  $('#sevLegend').innerHTML = order.map(k => `<div><i style="background:${SEV[k]}"></i>${label(k)}<b>${m[k] || 0}</b></div>`).join('');
  if (!window.Chart) return;
  state.charts.sev?.destroy();
  state.charts.sev = new Chart($('#sevChart'), {
    type: 'doughnut',
    data: { labels: order.map(label), datasets: [{ data: order.map(k => m[k] || 0), backgroundColor: order.map(k => SEV[k]), borderWidth: 4, borderColor: '#fff', borderRadius: 8, spacing: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: '76%', plugins: { legend: { display: false }, tooltip: { backgroundColor: '#1a1c2e', cornerRadius: 10 } } },
  });
}
$('#trendSeg').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  state.trendDays = +b.dataset.days;
  $$('#trendSeg button').forEach(x => x.classList.toggle('on', x === b));
  api('/api/trends?days=' + state.trendDays).then(drawTrend);
});
document.addEventListener('click', e => { const r = e.target.closest('[data-alert]'); if (r) openAlert(r.dataset.alert); });

/* ------------------------------------------------------------------ alerts page */
async function loadAlerts() {
  const qs = new URLSearchParams();
  if (state.alertStatus) qs.set('status', state.alertStatus);
  if ($('#alertSev').value) qs.set('severity', $('#alertSev').value);
  const rows = await api('/api/alerts?' + qs);
  $('#alertsTbl').innerHTML = `<thead><tr><th>Customer</th><th>Amount</th><th>Risk score</th><th>Severity</th><th>Status</th><th>Raised</th></tr></thead><tbody>` +
    (rows.length ? rows.map(a => `<tr class="click" data-alert="${a.alert_id}">
      <td>${who(a.customer_name, '#' + a.alert_id + ' · ' + a.txn_type.toLowerCase())}</td>
      <td><span class="amt">${inr(a.amount)}</span><br><small class="muted">${esc(a.channel)} · ${esc(a.txn_country)}</small></td>
      <td>${scoreBar(a.risk_score, a.severity)}</td><td>${pill(a.severity)}</td><td>${pill(a.status)}</td>
      <td><span title="${esc(fmtDT(a.created_at))}">${ago(a.created_at)}</span></td></tr>`).join('')
      : emptyRow(6, 'No alerts match these filters', 'Try a different status or severity.')) + '</tbody>';
}
$('#alertChips').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  state.alertStatus = b.dataset.status;
  $$('#alertChips button').forEach(x => x.classList.toggle('on', x === b));
  loadAlerts();
});
$('#alertSev').addEventListener('change', loadAlerts);

/* ------------------------------------------------------------------ transactions page */
async function loadTxns() {
  const qs = new URLSearchParams({ page: state.txnPage });
  for (const [k, id] of [['q', 'fSearch'], ['type', 'fType'], ['status', 'fStatus'], ['min', 'fMin'], ['max', 'fMax']]) {
    const v = $('#' + id).value.trim(); if (v) qs.set(k, v);
  }
  const d = await api('/api/transactions?' + qs);
  const route = t => t.txn_type === 'DEPOSIT' ? `→ ${esc(t.to_account)}` : t.txn_type === 'WITHDRAWAL' ? `${esc(t.from_account)} → cash` : `${esc(t.from_account)} → ${esc(t.to_account)}`;
  $('#txnTbl').innerHTML = `<thead><tr><th>Txn</th><th>Customer</th><th>Type</th><th>Amount</th><th>Risk</th><th>Status</th><th>When</th></tr></thead><tbody>` +
    (d.rows.length ? d.rows.map(t => `<tr>
      <td><b>#${t.txn_id}</b><br><small class="muted">${esc(t.channel)} · ${esc(t.country)}</small></td>
      <td>${who(t.customer_name, route(t))}</td><td><span class="tag">${esc(label(t.txn_type))}</span></td>
      <td><span class="amt">${inr(t.amount)}</span></td><td>${t.score == null ? '<span class="muted">–</span>' : scoreBar(t.score)}</td>
      <td>${pill(t.status)}</td><td><span title="${esc(fmtDT(t.created_at))}">${fmtDT(t.created_at)}</span></td></tr>`).join('')
      : emptyRow(7, 'No transactions found', 'Adjust your search or filters.')) + '</tbody>';
  $('#pageInfo').textContent = `${d.total} transactions · page ${d.page} of ${d.pages}`;
  $('#prevPg').disabled = d.page <= 1; $('#nextPg').disabled = d.page >= d.pages;
}
const debounce = (f, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), ms); }; };
const reload = () => { state.txnPage = 1; loadTxns(); };
['fSearch', 'fMin', 'fMax'].forEach(id => $('#' + id).addEventListener('input', debounce(reload)));
['fType', 'fStatus'].forEach(id => $('#' + id).addEventListener('change', reload));
$('#prevPg').onclick = () => { state.txnPage--; loadTxns(); };
$('#nextPg').onclick = () => { state.txnPage++; loadTxns(); };
$('#globalSearch').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  $('#fSearch').value = e.target.value; state.txnPage = 1; go('transactions');
});

/* ------------------------------------------------------------------ rules page */
const RULE_META = {
  LARGE_AMOUNT: { thr: 'Amount ₹', win: null }, HIGH_VELOCITY: { thr: 'Txn count', win: 'Window (min)' },
  FAILED_LOGINS: { thr: 'Failures', win: 'Window (min)' }, FAILED_TXNS: { thr: 'Failures', win: 'Window (min)' },
  AMOUNT_SPIKE: { thr: '× average', win: 'History (min)' }, STRUCTURING: { thr: 'Limit ₹', win: 'Window (min)' },
  FOREIGN_COUNTRY: { thr: null, win: null }, ODD_HOURS: { thr: null, win: null }, NEW_ACCOUNT_LARGE: { thr: 'Amount ₹', win: 'Max age (min)' },
};
async function loadRules() {
  const rules = await api('/api/rules');
  $('#sideRules').textContent = rules.filter(r => r.is_active).length;
  $('#rulesGrid').innerHTML = rules.map(r => {
    const m = RULE_META[r.rule_code] || { thr: 'Threshold', win: 'Window (min)' };
    return `<div class="rule ${r.is_active ? '' : 'off'}" data-id="${r.rule_id}">
      <div class="rule-top"><code>${esc(r.rule_code)}</code>
        <label class="sw"><input type="checkbox" class="r-on" ${r.is_active ? 'checked' : ''}><span></span></label></div>
      <p>${esc(r.description)}</p>
      <div class="f3">
        <label>${m.thr || 'Threshold'}<input class="input r-thr" type="number" value="${+r.threshold}" ${m.thr ? '' : 'disabled'}></label>
        <label>${m.win || 'Window'}<input class="input r-win" type="number" value="${r.window_mins}" ${m.win ? '' : 'disabled'}></label>
        <label>Weight<input class="input r-w" type="number" min="1" max="100" value="${r.weight}"></label>
      </div>
      <button class="btn ghost sm r-save">Save changes</button></div>`;
  }).join('');
}
$('#rulesGrid').addEventListener('click', async e => {
  const btn = e.target.closest('.r-save'); if (!btn) return;
  await saveRule(btn.closest('.rule'));
});
$('#rulesGrid').addEventListener('change', async e => { if (e.target.matches('.r-on')) await saveRule(e.target.closest('.rule')); });
async function saveRule(el) {
  try {
    await api('/api/rules/' + el.dataset.id, { method: 'PUT', body: {
      threshold: +$('.r-thr', el).value, window_mins: +$('.r-win', el).value, weight: +$('.r-w', el).value, is_active: $('.r-on', el).checked } });
    toast('Rule saved — logged in the audit trail');
    loadRules();
  } catch (err) { toast(err.message); }
}

/* ------------------------------------------------------------------ audit page */
async function loadAudit() {
  const rows = await api('/api/audit');
  $('#auditTbl').innerHTML = `<thead><tr><th>When</th><th>Entity</th><th>Action</th><th>Change</th><th>Actor</th></tr></thead><tbody>` +
    (rows.length ? rows.map(l => `<tr><td>${fmtDT(l.logged_at)}</td>
      <td><span class="tag">${esc(label(l.entity))}</span> <small class="muted">#${l.entity_id ?? ''}</small></td>
      <td><b>${esc(label(l.action))}</b></td>
      <td>${l.old_value ? `<span class="muted">${esc(l.old_value)}</span> → ` : ''}<b>${esc(l.new_value || '')}</b></td>
      <td>${esc(l.actor)}</td></tr>`).join('') : emptyRow(5, 'Nothing logged yet', 'Review an alert or edit a rule.')) + '</tbody>';
}

/* ------------------------------------------------------------------ alert drawer */
const closeDrawer = () => { $('#drawer').classList.remove('on'); $('#scrim').classList.remove('on'); };
$('#scrim').addEventListener('click', closeDrawer);

async function openAlert(id) {
  const d = await api('/api/alerts/' + id);
  const { alert: a, txn, account, history, investigations, reasons } = d;
  const c = SEV[a.severity];
  const closed = a.status === 'CONFIRMED' || a.status === 'DISMISSED';
  const route = a.txn_type === 'DEPOSIT' ? `Deposit into ${a.to_account}` : a.txn_type === 'WITHDRAWAL' ? `Withdrawal from ${a.from_account}` : `${a.from_account} → ${a.to_account}`;
  $('#drawer').innerHTML = `
    <div class="dr-head"><h2>Alert #${a.alert_id}</h2><button class="icon-btn" id="drClose">✕</button></div>
    <div class="dr-hero">
      <div class="ring" style="background:conic-gradient(${c} ${a.risk_score * 3.6}deg,#eceef7 0)"><div>${a.risk_score}</div></div>
      <div><div style="display:flex;gap:6px;flex-wrap:wrap">${pill(a.severity)}${pill(a.status)}${txn.status === 'BLOCKED' ? pill('BLOCKED') : ''}</div>
        <div class="amt">${inr(a.amount)}</div><small class="muted">${esc(route)}</small></div>
    </div>
    <div class="sec"><h4>Why it was flagged</h4><ul class="why">${reasons.map(r => `<li><svg viewBox="0 0 24 24"><path d="M12 3l9.5 17h-19L12 3z"/><path d="M12 10v4M12 17.5v.01"/></svg>${esc(r)}</li>`).join('')}</ul></div>
    <div class="sec"><h4>Transaction &amp; account</h4><div class="kv">
      <div><small>Customer</small><b>${esc(account.full_name)}</b></div><div><small>Account</small><b>${esc(account.account_no)} ${pill(account.status)}</b></div>
      <div><small>Balance now</small><b>${inr(account.balance)}</b></div><div><small>KYC / home country</small><b>${esc(label(account.kyc_status))} · ${esc(account.country)}</b></div>
      <div><small>Channel</small><b>${esc(label(txn.channel))}</b></div><div><small>Origin country</small><b>${esc(txn.country)}</b></div>
      <div><small>Transaction</small><b>#${txn.txn_id} · ${pill(txn.status)}</b></div><div><small>Time</small><b>${fmtDT(a.txn_time)}</b></div></div></div>
    <div class="sec"><h4>Recent activity on this account</h4>${history.length ? history.map(h => `
      <div class="row-item"><div class="grow"><b>${esc(label(h.txn_type))} · ${inr(h.amount)}</b><small>${fmtDT(h.created_at)} · ${esc(h.channel)} · ${esc(h.country)}</small></div>${pill(h.status)}</div>`).join('') : '<div class="muted">No other activity.</div>'}</div>
    <div class="sec"><h4>Investigation trail</h4>${investigations.length ? `<div class="tl">${investigations.map(i => `
      <div><b>${esc(label(i.decision))}</b> <small>· ${esc(i.analyst)} · ${fmtDT(i.created_at)}</small>${i.notes ? `<p>${esc(i.notes)}</p>` : ''}</div>`).join('')}</div>` : '<div class="muted">No analyst activity yet.</div>'}</div>
    <div class="sec"><h4>Analyst decision</h4>${closed
      ? `<div class="closed-note">This alert is ${a.status === 'CONFIRMED' ? 'confirmed as fraud — the originating account was frozen' : 'dismissed as a false positive'}.</div>`
      : `<textarea class="input" id="drNotes" placeholder="Add investigation notes (optional)…"></textarea>
         <div class="actions"><button class="btn warn" data-d="UNDER_REVIEW">Under review</button><button class="btn good" data-d="DISMISSED">Dismiss</button><button class="btn danger" data-d="CONFIRMED">Confirm fraud</button></div>`}</div>`;
  $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
  $('#drClose').onclick = closeDrawer;
  $$('#drawer [data-d]').forEach(b => b.onclick = async () => {
    const dec = b.dataset.d;
    if (dec === 'CONFIRMED' && !confirm(`Confirm fraud? This freezes account ${account.account_no}.`)) return;
    try {
      await api(`/api/alerts/${id}/review`, { method: 'POST', body: { decision: dec, notes: $('#drNotes').value, analyst: 'admin' } });
      toast(dec === 'CONFIRMED' ? 'Fraud confirmed — account frozen' : dec === 'DISMISSED' ? 'Alert dismissed' : 'Marked under review');
      refresh(); openAlert(id);
    } catch (err) { toast(err.message); }
  });
}

/* ------------------------------------------------------------------ new transaction modal */
const modal = { type: 'TRANSFER', loaded: false };
async function openModal() {
  const accts = await api('/api/accounts');
  const opts = accts.map(a => `<option value="${a.account_id}" ${a.status !== 'ACTIVE' ? 'disabled' : ''}>${esc(a.full_name)} · ${esc(a.account_no)} · ${inr(a.balance)}${a.status !== 'ACTIVE' ? ' (' + a.status.toLowerCase() + ')' : ''}</option>`).join('');
  const f = $('#mFrom').value, t = $('#mTo').value;
  $('#mFrom').innerHTML = opts; $('#mTo').innerHTML = opts;
  if (f) $('#mFrom').value = f;
  $('#mTo').value = t || accts[1]?.account_id;
  $('#mResult').innerHTML = '';
  applyType();
  $('#txnModal').classList.add('on');
}
function applyType() {
  $('#mFromWrap').style.display = modal.type === 'DEPOSIT' ? 'none' : '';
  $('#mToWrap').style.display = modal.type === 'WITHDRAWAL' ? 'none' : '';
  $$('#mType button').forEach(b => b.classList.toggle('on', b.dataset.t === modal.type));
}
const closeModal = () => $('#txnModal').classList.remove('on');
$('#btnNewTxn').onclick = openModal;
$('#mType').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { modal.type = b.dataset.t; applyType(); } });
$$('[data-close]').forEach(b => b.onclick = closeModal);
$('#txnModal').addEventListener('click', e => { if (e.target.id === 'txnModal') closeModal(); });
$('.hints').addEventListener('click', e => {
  const a = e.target.closest('[data-fill]'); if (!a) return;
  const [amt, cc] = a.dataset.fill.split('|'); $('#mAmount').value = amt; $('#mCountry').value = cc;
});
$('#simLogins').onclick = async () => {
  const acc = modal.type === 'DEPOSIT' ? $('#mTo').value : $('#mFrom').value;
  for (let i = 0; i < 4; i++) await api('/api/login-attempts', { method: 'POST', body: { account_id: +acc, success: false, ip: '185.220.101.' + (10 + i) } });
  toast('4 failed logins recorded for this customer');
};
$('#mSubmit').onclick = async () => {
  const btn = $('#mSubmit'); btn.disabled = true;
  try {
    const r = await api('/api/transactions', { method: 'POST', body: {
      txn_type: modal.type, from_id: +$('#mFrom').value || null, to_id: +$('#mTo').value || null,
      amount: $('#mAmount').value, channel: $('#mChannel').value, country: $('#mCountry').value || 'IN' } });
    const cls = r.status === 'BLOCKED' ? 'bad' : r.status === 'FLAGGED' ? 'warn' : r.status === 'FAILED' ? 'gray' : 'good';
    const head = { BLOCKED: '⛔ Blocked — funds were not moved', FLAGGED: '⚠ Completed but flagged for review', FAILED: 'Failed', COMPLETED: '✓ Completed — no rules fired' }[r.status];
    $('#mResult').innerHTML = `<div class="box ${cls}">${head} <span style="float:right">Risk ${r.score}</span>${r.reasons.length ? `<ul>${r.reasons.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</div>`;
    refresh();
    const accts = await api('/api/accounts');   // refresh balances in the dropdowns
    const f = $('#mFrom').value, t = $('#mTo').value;
    const opts = accts.map(a => `<option value="${a.account_id}" ${a.status !== 'ACTIVE' ? 'disabled' : ''}>${esc(a.full_name)} · ${esc(a.account_no)} · ${inr(a.balance)}${a.status !== 'ACTIVE' ? ' (' + a.status.toLowerCase() + ')' : ''}</option>`).join('');
    $('#mFrom').innerHTML = opts; $('#mTo').innerHTML = opts; $('#mFrom').value = f; $('#mTo').value = t;
  } catch (err) { $('#mResult').innerHTML = `<div class="box bad">${esc(err.message)}</div>`; }
  btn.disabled = false;
};
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeDrawer(); closeModal(); } });

/* ------------------------------------------------------------------ boot */
api('/api/rules').then(r => { $('#sideRules').textContent = r.filter(x => x.is_active).length; }).catch(() => {});
loadDashboard().catch(err => toast('Could not load data: ' + err.message));
