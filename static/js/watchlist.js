/**
 * watchlist.js — Watchlist page logic
 */

let _watchlistData = [];

// ── Load & render ─────────────────────────────────────────────────────────────

async function loadWatchlist() {
    const tbody = document.getElementById('wl-body');
    tbody.innerHTML = '<tr><td colspan="13" class="text-center py-8 text-base-content/40 text-sm"><span class="loading loading-spinner loading-sm mr-2"></span>Loading…</td></tr>';

    try {
        const res  = await fetch('/api/watchlist');
        const data = await res.json();
        _watchlistData = Array.isArray(data) ? data : [];
        renderTable();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="13" class="text-center py-8 text-error text-sm">Failed to load watchlist: ${e.message}</td></tr>`;
    }
}

function renderTable() {
    const tbody = document.getElementById('wl-body');
    const count = document.getElementById('wl-count');

    if (count) count.textContent = _watchlistData.length;

    if (_watchlistData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="13" class="text-center py-10 text-base-content/40 text-sm">No symbols on your watchlist yet.<br>Click <strong>Add to Watchlist</strong> on the Analysis Dashboard to add one.</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    const frag = document.createDocumentFragment();
    _watchlistData.forEach(e => frag.appendChild(_buildRow(e)));
    tbody.appendChild(frag);
}

function _buildRow(e) {
    const tr = document.createElement('tr');
    tr.className = 'hover';
    tr.id = `wl-row-${e.id}`;

    const addedDate = e.added_at ? new Date(e.added_at) : null;
    const addedStr  = addedDate
        ? addedDate.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
        : '—';
    const addedTime = addedDate
        ? addedDate.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
        : '';

    const priceAtAdd   = e.price_at_add   != null ? `$${Number(e.price_at_add).toFixed(2)}`   : '—';
    const currentPrice = e.current_price  != null ? `$${Number(e.current_price).toFixed(2)}`  : '—';

    // Price change since add
    let deltaPct = '—';
    let deltaClass = 'text-base-content/40';
    if (e.price_at_add != null && e.current_price != null && e.price_at_add > 0) {
        const pct = ((e.current_price - e.price_at_add) / e.price_at_add) * 100;
        deltaPct   = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
        deltaClass = pct >= 0 ? 'text-success font-semibold' : 'text-error font-semibold';
    }

    const scoreAtAdd   = e.score_at_add   != null ? `<span class="font-mono">${e.score_at_add}</span>`   : '<span class="text-base-content/30">—</span>';
    const gradeAtAdd   = e.grade_at_add   ? _gradeBadge(e.grade_at_add)   : '<span class="text-base-content/30">—</span>';
    const signalAtAdd  = e.signal_at_add  ? _signalBadge(e.signal_at_add) : '<span class="text-base-content/30">—</span>';

    const currentScore  = e.current_score  != null ? `<span class="font-mono">${e.current_score}</span>`  : '<span class="text-base-content/30">—</span>';
    const currentGrade  = e.current_grade  ? _gradeBadge(e.current_grade)  : '<span class="text-base-content/30">—</span>';
    const currentSignal = e.current_signal ? _signalBadge(e.current_signal) : '<span class="text-base-content/30">—</span>';

    const notesId = `wl-notes-${e.id}`;

    tr.innerHTML = `
        <td class="font-mono font-semibold text-sm">
            <a href="/?symbol=${encodeURIComponent(e.symbol)}" target="_blank"
               class="link link-hover text-primary">${e.symbol}</a>
        </td>
        <td class="text-xs text-base-content/70">
            <div>${addedStr}</div>
            <div class="text-base-content/40">${addedTime}</div>
        </td>
        <!-- Snapshot @ Add -->
        <td class="text-xs text-right border-l border-base-300">${priceAtAdd}</td>
        <td class="text-xs text-right">${scoreAtAdd}</td>
        <td class="text-xs">${gradeAtAdd}</td>
        <td class="text-xs">${signalAtAdd}</td>
        <!-- Current -->
        <td class="text-xs text-right border-l border-base-300">${currentPrice}</td>
        <td class="text-xs text-right ${deltaClass}">${deltaPct}</td>
        <td class="text-xs text-right">${currentScore}</td>
        <td class="text-xs">${currentGrade}</td>
        <td class="text-xs">${currentSignal}</td>
        <td class="text-xs min-w-40">
            <input id="${notesId}" type="text" value="${_escAttr(e.notes || '')}"
                   placeholder="Add notes…"
                   class="input input-xs input-ghost w-full focus:input-bordered"
                   onblur="saveNotes('${e.id}', this.value)"
                   onkeydown="if(event.key==='Enter') this.blur()" />
        </td>
        <td>
            <button class="btn btn-xs btn-ghost text-error" onclick="removeEntry('${e.id}')" title="Remove">
                <i class="bi bi-trash3"></i>
            </button>
        </td>
    `;

    return tr;
}

function _gradeBadge(grade) {
    if (!grade || grade === '—') return '<span class="text-base-content/30 text-xs">—</span>';
    if (grade === 'A') return `<span class="badge badge-xs badge-grade-a border">${grade}</span>`;
    const cls = grade === 'B' ? 'badge-warning' :
                grade === 'C' ? 'badge-info'    : 'badge-ghost';
    return `<span class="badge badge-xs ${cls}">${grade}</span>`;
}

function _signalBadge(signal) {
    if (!signal || signal === 'NONE') return '<span class="text-base-content/30 text-xs">NONE</span>';
    const cls = signal === 'BUY' ? 'text-success font-bold' : signal === 'SELL' ? 'text-error font-bold' : 'text-base-content/40';
    return `<span class="text-xs ${cls}">${signal}</span>`;
}

function _escAttr(str) {
    return str.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Actions ───────────────────────────────────────────────────────────────────

async function removeEntry(id) {
    try {
        const res = await fetch(`/api/watchlist/${id}`, { method: 'DELETE' });
        if (!res.ok) {
            const d = await res.json();
            showStatus('error', d.error || 'Remove failed');
            return;
        }
        _watchlistData = _watchlistData.filter(e => e.id !== id);
        renderTable();
        showStatus('success', 'Removed from watchlist');
    } catch (e) {
        showStatus('error', `Remove failed: ${e.message}`);
    }
}

async function saveNotes(id, notes) {
    try {
        await fetch(`/api/watchlist/${id}`, {
            method:  'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ notes }),
        });
        // Update local data silently
        const entry = _watchlistData.find(e => e.id === id);
        if (entry) entry.notes = notes;
    } catch (_) {}
}

// ── Add from dashboard (called by analysis.js via postMessage or direct call) ─

async function addToWatchlist(payload) {
    try {
        const res  = await fetch('/api/watchlist', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) {
            return { ok: false, error: data.error || 'Failed to add' };
        }
        return { ok: true, entry: data };
    } catch (e) {
        return { ok: false, error: e.message };
    }
}

// ── Status toast ──────────────────────────────────────────────────────────────

function showStatus(type, msg) {
    const el  = document.getElementById('wl-status');
    if (!el) return;
    const cls = type === 'error' ? 'alert-error' : type === 'info' ? 'alert-info' : 'alert-success';
    el.innerHTML = `<div class="alert ${cls} py-2 text-xs">${msg}</div>`;
    setTimeout(() => { el.innerHTML = ''; }, 4000);
}

// ── High-Quality Setups (Alerts) ──────────────────────────────────────────────

let _alertsData    = [];
let _alertsPage    = 0;
const _ALERTS_PAGE_SIZE = 10;
let _alertsLoaded  = false;

async function loadAlerts() {
    const tbody = document.getElementById('alerts-body');
    tbody.innerHTML = '<tr><td colspan="11" class="text-center py-6 text-base-content/40 text-sm"><span class="loading loading-spinner loading-sm mr-2"></span>Loading…</td></tr>';

    try {
        const res  = await fetch('/api/alerts?limit=100');
        const data = await res.json();
        _alertsData   = Array.isArray(data) ? data : [];
        _alertsPage   = 0;
        _alertsLoaded = true;

        const countEl = document.getElementById('alerts-count');
        if (countEl) countEl.textContent = _alertsData.length;

        _renderAlerts();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-6 text-error text-sm">Failed to load: ${e.message}</td></tr>`;
    }
}

function _renderAlerts() {
    const tbody    = document.getElementById('alerts-body');
    const pagDiv   = document.getElementById('alerts-pagination');
    const pageInfo = document.getElementById('alerts-page-info');
    const prevBtn  = document.getElementById('alerts-prev');
    const nextBtn  = document.getElementById('alerts-next');

    if (_alertsData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="11" class="text-center py-6 text-base-content/40 text-sm">No high-quality setups logged yet. Run a swing or short scan to start building the log.</td></tr>';
        if (pagDiv) pagDiv.classList.add('hidden');
        return;
    }

    const totalPages = Math.ceil(_alertsData.length / _ALERTS_PAGE_SIZE);
    const start      = _alertsPage * _ALERTS_PAGE_SIZE;
    const pageRows   = _alertsData.slice(start, start + _ALERTS_PAGE_SIZE);

    tbody.innerHTML = '';
    const frag = document.createDocumentFragment();
    pageRows.forEach(e => frag.appendChild(_buildAlertRow(e)));
    tbody.appendChild(frag);

    if (pagDiv) {
        if (totalPages > 1) {
            pagDiv.classList.remove('hidden');
            if (pageInfo) pageInfo.textContent = `Page ${_alertsPage + 1} of ${totalPages} (${_alertsData.length} entries)`;
            if (prevBtn)  prevBtn.disabled = _alertsPage === 0;
            if (nextBtn)  nextBtn.disabled = _alertsPage >= totalPages - 1;
        } else {
            pagDiv.classList.add('hidden');
        }
    }
}

function alertsPage(delta) {
    const totalPages = Math.ceil(_alertsData.length / _ALERTS_PAGE_SIZE);
    _alertsPage = Math.max(0, Math.min(totalPages - 1, _alertsPage + delta));
    _renderAlerts();
}

function onAlertsToggle(details) {
    const chevron = document.getElementById('alerts-chevron');
    if (chevron) chevron.style.transform = details.open ? 'rotate(180deg)' : '';
    if (details.open && !_alertsLoaded) {
        loadAlerts();
    }
}

function _buildAlertRow(e) {
    const tr = document.createElement('tr');
    tr.className = 'hover';

    const ts  = e.timestamp ? new Date(e.timestamp) : null;
    const tsStr = ts
        ? ts.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
          + ' ' + ts.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
        : '—';

    const tfLabel = e.timeframe === 'swing' ? 'Swing' : e.timeframe === 'short' ? '15m' : e.timeframe || '—';

    const regimeCls = e.regime === 'BULLISH' ? 'text-success font-semibold'
                    : e.regime === 'BEARISH' ? 'text-error font-semibold'
                    : 'text-base-content/50';

    const rs    = e.rs_vs_spy   != null ? (e.rs_vs_spy >= 0 ? '+' : '') + Number(e.rs_vs_spy).toFixed(2)   : '—';
    const rsCls = e.rs_vs_spy   != null && e.rs_vs_spy >= 0 ? 'text-success' : 'text-error';
    const rvol  = e.rvol        != null ? Number(e.rvol).toFixed(2)        : '—';
    const bb    = e.bb_width_pct != null ? Number(e.bb_width_pct).toFixed(1) + '%' : '—';
    const rsi   = e.rsi         != null ? Number(e.rsi).toFixed(1)         : '—';
    const price = e.price       != null ? '$' + Number(e.price).toFixed(2) : '—';

    tr.innerHTML = `
        <td class="text-xs text-base-content/60 whitespace-nowrap">${tsStr}</td>
        <td class="font-mono font-semibold text-sm">
            <a href="/?symbol=${encodeURIComponent(e.symbol || '')}" target="_blank"
               class="link link-hover text-primary">${e.symbol || '—'}</a>
        </td>
        <td class="text-xs"><span class="badge badge-xs badge-ghost">${tfLabel}</span></td>
        <td class="text-xs text-right font-mono font-semibold">${e.score != null ? e.score : '—'}</td>
        <td class="text-xs ${regimeCls}">${e.regime || '—'}</td>
        <td class="text-xs text-right ${rsCls}">${rs}</td>
        <td class="text-xs text-right">${rvol}</td>
        <td class="text-xs text-right">${bb}</td>
        <td class="text-xs text-right">${rsi}</td>
        <td class="text-xs text-right">${price}</td>
        <td class="text-xs text-base-content/50 max-w-xs truncate" title="${_escAttr(e.blocked_at || '')}">${e.blocked_at || '—'}</td>
    `;
    return tr;
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadWatchlist();
    // Pre-load the alerts count badge without opening the section
    fetch('/api/alerts?limit=100')
        .then(r => r.json())
        .then(data => {
            const countEl = document.getElementById('alerts-count');
            if (countEl) countEl.textContent = Array.isArray(data) ? data.length : 0;
        })
        .catch(() => {});
});
