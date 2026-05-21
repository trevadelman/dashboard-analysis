/**
 * scanner.js — Market Scanner SSE consumer and results table
 */

let scanSource  = null;
let scanResults = [];   // all results from the current/loaded scan

// Sort state
let _sortKey = 'score';
let _sortAsc = false;

// Text filter state
let _filterText = '';
let _filterTimer = null;

// Row-append batching — accumulate incoming rows and flush to the DOM
// at most once per animation frame.  Without this, appending 4,000 rows
// one-by-one causes a layout/paint storm that freezes the browser.
let _pendingRows = [];
let _rafPending  = false;

function _flushPendingRows() {
    _rafPending = false;
    if (_pendingRows.length === 0) return;

    const tbody = document.getElementById('scan-results-body');
    const placeholder = tbody.querySelector('td[colspan]');
    if (placeholder) placeholder.closest('tr').remove();

    // Signals go to the top, non-signals to the bottom.
    // Build two fragments so we only touch the DOM twice per flush.
    const topFrag    = document.createDocumentFragment();
    const bottomFrag = document.createDocumentFragment();

    for (const r of _pendingRows) {
        if (!_matchesFilter(r)) continue;
        const row = _buildRow(r);
        if (r.signal !== 'NONE') {
            topFrag.appendChild(row);
        } else {
            bottomFrag.appendChild(row);
        }
    }

    if (topFrag.childNodes.length > 0) {
        tbody.insertBefore(topFrag, tbody.firstChild);
    }
    tbody.appendChild(bottomFrag);

    _pendingRows = [];
}

const LIST_LABELS = {
    all_sectors:  'All Sectors',
    all_universe: 'Full Universe',
    crypto_all:   'All Crypto',
    custom:       'Custom List',
};

// ── Scan control ──────────────────────────────────────────────────────────────

function getSelectedTimeframe() {
    const checked = document.querySelector('input[name="scan-timeframe"]:checked');
    return checked ? checked.value : 'long';
}

function startScan() {
    let listName = document.getElementById('scan-list-select').value;
    const custom = document.getElementById('scan-custom-input').value.trim();
    const timeframe = getSelectedTimeframe();

    // Resolve saved_N → custom
    if (listName.startsWith('saved_')) {
        listName = 'custom';
    }

    if (listName === 'custom' && !custom) {
        showScanStatus('error', 'Enter at least one symbol in the custom field.');
        return;
    }

    if (scanSource) { scanSource.close(); scanSource = null; }

    scanResults = [];
    _filterText = '';
    document.getElementById('scan-filter-input').value = '';
    resetUI();

    const params = new URLSearchParams({ list_name: listName, custom, timeframe });
    scanSource   = new EventSource(`/api/scan/stream?${params}`);

    scanSource.onmessage = (e) => {
        try { handleEvent(JSON.parse(e.data)); }
        catch (_) {}
    };

    scanSource.onerror = () => {
        showScanStatus('error', 'Connection lost. Try again.');
        scanSource.close();
        scanSource = null;
    };
}

function stopScan() {
    if (scanSource) { scanSource.close(); scanSource = null; }
    document.getElementById('scan-stop-btn').classList.add('hidden');
    document.getElementById('scan-start-btn').classList.remove('hidden');
    showScanStatus('info', 'Scan stopped.');
}

// ── Cache ─────────────────────────────────────────────────────────────────────

async function checkCache() {
    try {
        const res = await fetch('/api/scan/cache/info');
        if (!res.ok) return;
        const info = await res.json();
        if (!info.cached) return;

        const popoverBtn = document.getElementById('cache-popover-btn');
        const label      = document.getElementById('cache-btn-label');
        const detail     = document.getElementById('cache-detail-text');

        const age = _cacheAge(info.last_updated);
        label.textContent = `Cache · ${age} ago`;

        const tfs = Object.entries(info.timeframes || {});
        if (tfs.length === 0) {
            detail.innerHTML = '<span>No results cached yet.</span>';
        } else {
            detail.innerHTML = tfs.map(([tf, d]) =>
                `<div class="flex justify-between"><span class="font-medium capitalize">${tf}</span><span>${d.total.toLocaleString()} symbols · ${d.signals} signals</span></div>`
            ).join('');
        }

        popoverBtn.classList.remove('hidden');
    } catch (_) {}
}

async function loadFromCache() {
    const timeframe = getSelectedTimeframe();
    try {
        const res = await fetch(`/api/scan/cache?timeframe=${encodeURIComponent(timeframe)}`);
        if (!res.ok) { showScanStatus('error', 'No cache found for this timeframe — run a scan first.'); return; }
        const data = await res.json();
        if (!data.results || data.results.length === 0) {
            showScanStatus('error', `No cached results for timeframe "${timeframe}" — run a scan first.`);
            return;
        }

        scanResults = data.results;
        _filterText = '';
        document.getElementById('scan-filter-input').value = '';
        resetUI();
        renderAllRows();

        const age = _cacheAge(data.last_updated);
        document.getElementById('scan-stats').textContent =
            `Loaded from cache — ${data.total} symbols, ${data.signals} signals (${timeframe}, ${age} ago)`;
        document.getElementById('scan-progress-wrap').classList.remove('hidden');
        document.getElementById('scan-progress-bar').style.width = '100%';
        document.getElementById('scan-export-btn').classList.remove('hidden');
    } catch (e) {
        showScanStatus('error', `Failed to load cache: ${e.message}`);
    }
}

function _cacheAge(isoStr) {
    if (!isoStr) return '?';
    const diff = Math.round((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (diff < 60)   return `${diff}s`;
    if (diff < 3600) return `${Math.round(diff / 60)}m`;
    return `${Math.round(diff / 3600)}h`;
}

// ── SSE event handlers ────────────────────────────────────────────────────────

function handleEvent(evt) {
    switch (evt.type) {
        case 'start':    onStart(evt);    break;
        case 'result':   onResult(evt);   break;
        case 'progress': onProgress(evt); break;
        case 'done':     onDone(evt);     break;
        case 'error':    onError(evt);    break;
    }
}

function onStart(evt) {
    document.getElementById('scan-start-btn').classList.add('hidden');
    document.getElementById('scan-stop-btn').classList.remove('hidden');
    document.getElementById('scan-stats').textContent =
        `Scanning ${evt.total} symbols from ${LIST_LABELS[evt.list] || evt.list}…`;
    document.getElementById('scan-progress-bar').style.width = '0%';
    document.getElementById('scan-progress-wrap').classList.remove('hidden');
    document.getElementById('scan-export-btn').classList.add('hidden');
    document.getElementById('scan-results-body').innerHTML =
        '<tr><td colspan="12" class="text-center text-base-content/40 py-6 text-sm">Scanning…</td></tr>';
}

function onResult(evt) {
    scanResults.push(evt);
    // Queue the row for the next animation frame flush instead of touching
    // the DOM immediately.  This coalesces rapid-fire SSE events into a
    // single layout/paint pass per frame, preventing browser freeze on
    // large scans (4,000+ symbols arriving in quick bursts).
    _pendingRows.push(evt);
    if (!_rafPending) {
        _rafPending = true;
        requestAnimationFrame(_flushPendingRows);
    }
}

function onProgress(evt) {
    const pct = Math.round((evt.scanned / evt.total) * 100);
    document.getElementById('scan-progress-bar').style.width = `${pct}%`;
    document.getElementById('scan-stats').textContent =
        `${evt.scanned} / ${evt.total} scanned — ${scanResults.length} results`;
}

function onDone(evt) {
    if (scanSource) { scanSource.close(); scanSource = null; }
    document.getElementById('scan-stop-btn').classList.add('hidden');
    document.getElementById('scan-start-btn').classList.remove('hidden');
    document.getElementById('scan-progress-bar').style.width = '100%';
    document.getElementById('scan-stats').textContent =
        `✅ Done — ${evt.scanned} scanned, ${evt.signals} signals found in ${evt.elapsed}s`;
    document.getElementById('scan-export-btn').classList.remove('hidden');

    if (scanResults.length === 0) {
        document.getElementById('scan-results-body').innerHTML =
            '<tr><td colspan="12" class="text-center text-base-content/40 py-6 text-sm">No results returned.</td></tr>';
    }

    // Update cache badge
    checkCache();
}

function onError(evt) {
    if (scanSource) { scanSource.close(); scanSource = null; }
    document.getElementById('scan-stop-btn').classList.add('hidden');
    document.getElementById('scan-start-btn').classList.remove('hidden');
    showScanStatus('error', evt.message || 'Unknown error');
}

// ── Text filter ───────────────────────────────────────────────────────────────

function onFilterInput(value) {
    // Debounce — wait 150ms after the user stops typing before re-rendering
    clearTimeout(_filterTimer);
    _filterTimer = setTimeout(() => {
        _filterText = value.trim().toLowerCase();
        renderAllRows();
    }, 150);
}

function _matchesFilter(r) {
    if (!_filterText) return true;
    // Match against symbol, regime, signal, grade — the most useful columns
    const haystack = [
        r.symbol  || '',
        r.regime  || '',
        r.signal  || '',
        r.grade   || '',
    ].join(' ').toLowerCase();
    return haystack.includes(_filterText);
}

// ── Sorting ───────────────────────────────────────────────────────────────────

function sortBy(key) {
    if (_sortKey === key) {
        _sortAsc = !_sortAsc;
    } else {
        _sortKey = key;
        _sortAsc = key === 'symbol';   // default asc for symbol, desc for numbers
    }
    renderAllRows();
}

function _sorted(rows) {
    return rows.slice().sort((a, b) => {
        let av = a[_sortKey], bv = b[_sortKey];
        if (typeof av === 'string') av = av.toLowerCase();
        if (typeof bv === 'string') bv = bv.toLowerCase();
        if (av == null) av = _sortAsc ? Infinity : -Infinity;
        if (bv == null) bv = _sortAsc ? Infinity : -Infinity;
        return _sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
}

// ── Table rendering ───────────────────────────────────────────────────────────

function renderAllRows() {
    const tbody = document.getElementById('scan-results-body');
    tbody.innerHTML = '';
    const rows = _sorted(scanResults.filter(_matchesFilter));
    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="12" class="text-center text-base-content/40 py-6 text-sm">No results match the current filter.</td></tr>';
        return;
    }
    rows.forEach(r => tbody.appendChild(_buildRow(r)));
}

function appendRow(r) {
    // During live scan: insert signals at top, others at bottom.
    // Respect active text filter — don't show rows that don't match.
    if (!_matchesFilter(r)) return;

    const tbody = document.getElementById('scan-results-body');
    const placeholder = tbody.querySelector('td[colspan]');
    if (placeholder) placeholder.closest('tr').remove();

    const row = _buildRow(r);
    if (r.signal !== 'NONE') {
        tbody.insertBefore(row, tbody.firstChild);
    } else {
        tbody.appendChild(row);
    }
}

function _buildRow(r) {
    const signalClass = r.signal === 'BUY'  ? 'text-success font-bold' :
                        r.signal === 'SELL' ? 'text-error font-bold'   : 'text-base-content/40';
    const regimeClass = r.regime === 'BULLISH' ? 'badge-success' :
                        r.regime === 'BEARISH' ? 'badge-error'   : 'badge-ghost';
    const rowClass    = r.signal === 'BUY'  ? 'bg-success/5' :
                        r.signal === 'SELL' ? 'bg-error/5'   : '';

    const gradeBadge = _gradeBadge(r.grade, r.score);

    const fmt  = (v, dec=2) => v != null ? Number(v).toFixed(dec) : '—';
    const fmtP = (v)        => v != null ? `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%` : '—';
    const rsClass = r.rs_vs_spy != null && r.rs_vs_spy > 0 ? 'text-success' : 'text-error';

    const row = document.createElement('tr');
    row.id        = `row-${r.symbol}`;
    row.className = `hover cursor-pointer ${rowClass}`;
    row.onclick   = () => toggleDetail(r);

    row.innerHTML = `
        <td class="w-6 text-center text-base-content/30">
            <i id="chevron-${r.symbol}" class="bi bi-chevron-right text-xs"></i>
        </td>
        <td class="font-mono font-semibold text-sm">${r.symbol}</td>
        <td class="text-sm">$${fmt(r.price)}</td>
        <td><span class="badge badge-xs ${regimeClass}">${r.regime}</span></td>
        <td class="text-center text-sm">${r.tier_reached}</td>
        <td class="${signalClass} text-sm">${r.signal}</td>
        <td class="text-sm font-semibold">${r.score != null ? r.score : '—'}</td>
        <td>${gradeBadge}</td>
        <td class="text-sm ${rsClass}">${fmtP(r.rs_vs_spy)}</td>
        <td class="text-sm">${fmt(r.rvol)}</td>
        <td class="text-sm">${fmt(r.bb_width_pct, 1)}</td>
        <td class="text-sm">${fmt(r.rsi, 1)}</td>
    `;

    return row;
}

function _gradeBadge(grade, score) {
    if (!grade || grade === '—') return '<span class="text-base-content/30 text-xs">—</span>';
    if (grade === 'D') return '<span class="badge badge-xs badge-ghost">D</span>';
    const cls = grade === 'A' ? 'badge-success' :
                grade === 'B' ? 'badge-warning'  :
                grade === 'C' ? 'badge-info'     : 'badge-ghost';
    return `<span class="badge badge-xs ${cls}">${grade}</span>`;
}

function toggleDetail(r) {
    const detailId = `detail-${r.symbol}`;
    const chevron  = document.getElementById(`chevron-${r.symbol}`);
    const existing = document.getElementById(detailId);

    if (existing) {
        existing.remove();
        chevron.className = 'bi bi-chevron-right text-xs';
        return;
    }

    chevron.className = 'bi bi-chevron-down text-xs text-primary';

    const detail = document.createElement('tr');
    detail.id = detailId;
    detail.className = 'bg-base-200/60';

    const tier1Html = r.tier1_reason
        ? `<div class="flex items-start gap-2 text-xs"><span class="font-semibold text-base-content/60 shrink-0">Tier 1:</span><span>${r.tier1_reason}</span></div>`
        : '';
    const tier2Html = r.tier2_reason
        ? `<div class="flex items-start gap-2 text-xs mt-1"><span class="font-semibold text-base-content/60 shrink-0">Tier 2:</span><span>${r.tier2_reason}</span></div>`
        : '';

    detail.innerHTML = `
        <td colspan="12" class="px-6 py-3">
            <div class="flex flex-wrap gap-6 items-start">
                <div class="flex-1 min-w-48">
                    ${tier1Html}
                    ${tier2Html}
                </div>
                <div class="flex gap-2 shrink-0">
                    <a href="/?symbol=${encodeURIComponent(r.symbol)}" target="_blank"
                       class="btn btn-xs btn-ghost gap-1" onclick="event.stopPropagation()">
                        <i class="bi bi-box-arrow-up-right"></i> Open in Dashboard
                    </a>
                </div>
            </div>
        </td>
    `;

    const rowEl = document.getElementById(`row-${r.symbol}`);
    rowEl.insertAdjacentElement('afterend', detail);
}

// ── Universe selector ─────────────────────────────────────────────────────────

function onListChange() {
    const sel  = document.getElementById('scan-list-select').value;
    const wrap = document.getElementById('scan-custom-wrap');

    if (sel === 'custom' || sel.startsWith('saved_')) {
        wrap.classList.remove('hidden');
        if (sel.startsWith('saved_')) {
            const idx = parseInt(sel.replace('saved_', ''), 10);
            document.getElementById('scan-custom-input').value = savedLists[idx]?.symbols || '';
        } else {
            document.getElementById('scan-custom-input').value = '';
        }
    } else {
        wrap.classList.add('hidden');
    }
}

// Keep backward-compat alias
function toggleCustomInput() { onListChange(); }

// ── Saved custom lists ────────────────────────────────────────────────────────

let savedLists = JSON.parse(localStorage.getItem('scanner_saved_lists') || '[]');

function renderSavedLists() {
    const sel = document.getElementById('scan-list-select');
    sel.querySelectorAll('option[data-saved]').forEach(o => o.remove());
    sel.querySelectorAll('optgroup[data-saved]').forEach(g => g.remove());

    if (!savedLists.length) return;

    const group = document.createElement('optgroup');
    group.label = 'Saved Lists';
    group.dataset.saved = '1';

    savedLists.forEach((sl, idx) => {
        const opt = document.createElement('option');
        opt.value = `saved_${idx}`;
        opt.textContent = sl.name;
        opt.dataset.saved = '1';
        group.appendChild(opt);
    });

    sel.appendChild(group);
}

// ── Export ────────────────────────────────────────────────────────────────────

function exportCSV() {
    if (!scanResults.length) return;
    const headers = ['symbol','price','regime','tier_reached','signal','score','grade','rs_vs_spy','rvol','bb_width_pct','atr_pct_rank','rsi'];
    const rows    = scanResults.map(r => headers.map(h => r[h] ?? '').join(','));
    const csv     = [headers.join(','), ...rows].join('\n');
    const blob    = new Blob([csv], { type: 'text/csv' });
    const url     = URL.createObjectURL(blob);
    const a       = document.createElement('a');
    a.href        = url;
    a.download    = `scan_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function resetUI() {
    document.getElementById('scan-stats').textContent = '';
    document.getElementById('scan-progress-bar').style.width = '0%';
    document.getElementById('scan-progress-wrap').classList.add('hidden');
    document.getElementById('scan-export-btn').classList.add('hidden');
    document.getElementById('scan-results-body').innerHTML =
        '<tr><td colspan="12" class="text-center text-base-content/40 py-6 text-sm">Run a scan to see results.</td></tr>';
}

function showScanStatus(type, msg) {
    const el  = document.getElementById('scan-status-msg');
    const cls = type === 'error' ? 'alert-error' : type === 'info' ? 'alert-info' : 'alert-success';
    el.innerHTML = `<div class="alert ${cls} py-2 text-xs">${msg}</div>`;
    setTimeout(() => { el.innerHTML = ''; }, 5000);
}

// ── Universe ──────────────────────────────────────────────────────────────────

async function checkUniverseInfo() {
    try {
        const res = await fetch('/api/scan/universe/info');
        if (!res.ok) return;
        const info = await res.json();

        const popoverBtn = document.getElementById('universe-popover-btn');
        const label      = document.getElementById('universe-btn-label');
        const detail     = document.getElementById('universe-detail-text');

        if (info.cached) {
            label.textContent = `${info.count.toLocaleString()} symbols`;
            detail.textContent = `Fetched from Alpaca ${info.age_hours}h ago.${info.stale ? ' Cache is stale — consider refreshing.' : ''}`;
        } else {
            label.textContent = 'Universe';
            detail.textContent = `Using static fallback (${info.count} symbols). Refresh to fetch the full Alpaca ticker list.`;
        }
        popoverBtn.classList.remove('hidden');
    } catch (_) {}
}

async function refreshUniverse() {
    const btn = document.getElementById('refresh-universe-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Fetching…';
    try {
        const res = await fetch('/api/scan/universe/refresh', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            const equityCount = (data.equity_count ?? data.count ?? 0).toLocaleString();
            const cryptoCount = data.crypto_count != null ? ` + ${data.crypto_count} crypto` : '';
            showScanStatus('success', `Universe refreshed — ${equityCount} equity${cryptoCount} symbols cached.`);
            checkUniverseInfo();
            document.activeElement?.blur();
        } else {
            showScanStatus('error', data.error || 'Universe refresh failed.');
        }
    } catch (e) {
        showScanStatus('error', `Universe refresh failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Refresh from Alpaca';
    }
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    renderSavedLists();
    checkCache();
    checkUniverseInfo();
});
