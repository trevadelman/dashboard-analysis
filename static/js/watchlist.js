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
    const cls = grade === 'A' ? 'badge-success' :
                grade === 'B' ? 'badge-warning'  :
                grade === 'C' ? 'badge-info'     : 'badge-ghost';
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

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', loadWatchlist);
