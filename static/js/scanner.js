/**
 * scanner.js — Market Scanner SSE consumer and results table
 */

let scanSource  = null;
let scanResults = [];

// Saved custom lists: { name: string, symbols: string }[]
let savedLists = JSON.parse(localStorage.getItem('scanner_saved_lists') || '[]');

const LIST_LABELS = {
    sp500_top100:    'S&P 500 Top 100',
    nasdaq100_top50: 'NASDAQ 100 Top 50',
    sector_etfs:     'Sector ETFs',
    russell2000:     'Russell 2000 Sample',
    custom:          'Custom List',
};

// ── Scan control ──────────────────────────────────────────────────────────────

function getSelectedTimeframe() {
    const checked = document.querySelector('input[name="scan-timeframe"]:checked');
    return checked ? checked.value : 'long';
}

function startScan() {
    const listName  = document.getElementById('scan-list-select').value;
    const custom    = document.getElementById('scan-custom-input').value.trim();
    const timeframe = getSelectedTimeframe();

    if (listName === 'custom' && !custom) {
        showScanStatus('error', 'Enter at least one symbol in the custom field.');
        return;
    }

    if (scanSource) { scanSource.close(); scanSource = null; }

    scanResults = [];
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
        '<tr><td colspan="10" class="text-center text-base-content/40 py-6 text-sm">Scanning…</td></tr>';
}

function onResult(evt) {
    scanResults.push(evt);
    renderRow(evt);
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
            '<tr><td colspan="10" class="text-center text-base-content/40 py-6 text-sm">No results returned.</td></tr>';
    }
}

function onError(evt) {
    if (scanSource) { scanSource.close(); scanSource = null; }
    document.getElementById('scan-stop-btn').classList.add('hidden');
    document.getElementById('scan-start-btn').classList.remove('hidden');
    showScanStatus('error', evt.message || 'Unknown error');
}

// ── Table rendering ───────────────────────────────────────────────────────────

function renderRow(r) {
    const tbody = document.getElementById('scan-results-body');

    const placeholder = tbody.querySelector('td[colspan]');
    if (placeholder) placeholder.closest('tr').remove();

    const signalClass = r.signal === 'BUY'  ? 'text-success font-bold' :
                        r.signal === 'SELL' ? 'text-error font-bold'   : 'text-base-content/40';
    const regimeClass = r.regime === 'BULLISH' ? 'badge-success' :
                        r.regime === 'BEARISH' ? 'badge-error'   : 'badge-ghost';
    const rowClass    = r.signal === 'BUY'  ? 'bg-success/5' :
                        r.signal === 'SELL' ? 'bg-error/5'   : '';

    const fmt  = (v, dec=2) => v != null ? Number(v).toFixed(dec) : '—';
    const fmtP = (v)        => v != null ? `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%` : '—';

    const rowId = `row-${r.symbol}`;
    const detailId = `detail-${r.symbol}`;

    const row = document.createElement('tr');
    row.id        = rowId;
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
        <td class="text-sm ${r.rs_vs_spy != null && r.rs_vs_spy > 0 ? 'text-success' : 'text-error'}">${fmtP(r.rs_vs_spy)}</td>
        <td class="text-sm">${fmt(r.rvol)}</td>
        <td class="text-sm">${fmt(r.bb_width_pct, 1)}</td>
        <td class="text-sm">${fmt(r.rsi, 1)}</td>
    `;

    if (r.signal !== 'NONE') {
        tbody.insertBefore(row, tbody.firstChild);
    } else {
        tbody.appendChild(row);
    }
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
        <td colspan="10" class="px-6 py-3">
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

// ── Saved custom lists ────────────────────────────────────────────────────────

function renderSavedLists() {
    const sel = document.getElementById('scan-list-select');

    // Remove any previously injected saved-list options
    sel.querySelectorAll('option[data-saved]').forEach(o => o.remove());

    if (!savedLists.length) return;

    const group = document.createElement('optgroup');
    group.label = 'Saved Lists';

    savedLists.forEach((sl, idx) => {
        const opt = document.createElement('option');
        opt.value = `saved_${idx}`;
        opt.textContent = sl.name;
        opt.dataset.saved = '1';
        group.appendChild(opt);
    });

    sel.appendChild(group);
}

function saveCurrentList() {
    const raw = document.getElementById('scan-custom-input').value.trim();
    if (!raw) { showScanStatus('error', 'Enter symbols before saving.'); return; }

    const name = prompt('Name this list:');
    if (!name || !name.trim()) return;

    savedLists.push({ name: name.trim(), symbols: raw });
    localStorage.setItem('scanner_saved_lists', JSON.stringify(savedLists));
    renderSavedLists();
    showScanStatus('success', `Saved list "${name.trim()}"`);
}

function deleteSavedList(idx) {
    savedLists.splice(idx, 1);
    localStorage.setItem('scanner_saved_lists', JSON.stringify(savedLists));
    renderSavedLists();
    // Reset selector to custom if the deleted list was selected
    const sel = document.getElementById('scan-list-select');
    if (!sel.value || sel.value.startsWith('saved_')) {
        sel.value = 'custom';
        toggleCustomInput();
    }
}

function toggleCustomInput() {
    const sel    = document.getElementById('scan-list-select').value;
    const wrap   = document.getElementById('scan-custom-wrap');
    const saveBtn = document.getElementById('scan-save-list-btn');

    if (sel === 'custom') {
        wrap.classList.remove('hidden');
        saveBtn.classList.remove('hidden');
        document.getElementById('scan-custom-input').value = '';
    } else if (sel.startsWith('saved_')) {
        const idx = parseInt(sel.replace('saved_', ''), 10);
        wrap.classList.remove('hidden');
        saveBtn.classList.add('hidden');
        document.getElementById('scan-custom-input').value = savedLists[idx]?.symbols || '';
    } else {
        wrap.classList.add('hidden');
        saveBtn.classList.add('hidden');
    }
}

// Resolve saved list to custom symbols before scanning
const _origStartScan = startScan;
// Override startScan to handle saved_N list values
(function() {
    const origStart = startScan;
    window.startScan = function() {
        const sel = document.getElementById('scan-list-select').value;
        if (sel.startsWith('saved_')) {
            // Treat as custom scan using the saved symbols
            document.getElementById('scan-list-select').value = 'custom';
            // symbols already in the input from toggleCustomInput
        }
        origStart();
    };
})();

// ── Export ────────────────────────────────────────────────────────────────────

function exportCSV() {
    if (!scanResults.length) return;
    const headers = ['symbol','price','regime','tier_reached','signal','rs_vs_spy','rvol','bb_width_pct','atr_pct_rank','rsi'];
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
        '<tr><td colspan="10" class="text-center text-base-content/40 py-6 text-sm">Run a scan to see results.</td></tr>';
}

function showScanStatus(type, msg) {
    const el  = document.getElementById('scan-status-msg');
    const cls = type === 'error' ? 'alert-error' : type === 'info' ? 'alert-info' : 'alert-success';
    el.innerHTML = `<div class="alert ${cls} py-2 text-xs">${msg}</div>`;
    setTimeout(() => { el.innerHTML = ''; }, 5000);
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    renderSavedLists();
});
