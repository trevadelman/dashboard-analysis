/**
 * market.js — Market Pulse page
 *
 * Sections:
 *   1. Cross-Timeframe Overview cards (stance, regime, score, signals, setups)
 *   2. Active Signals table (all BUY/SELL across all timeframes)
 *   3. Top Setups by Timeframe (tabbed, top 10 by score)
 *   4. Timeframe Report Cards (collapsed accordion)
 *   5. AI Commentary (collapsed, streamed on expand)
 */

// ── Constants ─────────────────────────────────────────────────────────────────

const TF_LABEL = {
    long:  'Long-term',
    swing: 'Swing',
    short: 'Short-term',
};

const TF_ICON = {
    long:  'bi-calendar3',
    swing: 'bi-bar-chart-line',
    short: 'bi-lightning',
};

const TF_SUB = {
    long:  'Daily · 1 year',
    swing: 'Hourly · 3 months',
    short: '15-min · 1 month',
};

const STANCE_BADGE = {
    'RISK ON':   'badge-success',
    'SELECTIVE': 'badge-warning',
    'COILING':   'badge-info',
    'WAIT':      'badge-ghost',
    'RISK OFF':  'badge-error',
};

// ── State ─────────────────────────────────────────────────────────────────────

let _overviewData   = null;   // full /api/market/overview response
let _setupsData     = {};     // top_setups keyed by timeframe
let _activeSetupTab = 'long';
let _aiLoaded       = false;
let _aiStreaming     = false;

// ── Boot ──────────────────────────────────────────────────────────────────────

window.addEventListener('load', loadOverview);

async function loadOverview() {
    try {
        const res = await fetch('/api/market/overview');
        if (!res.ok) {
            showNoCache();
            return;
        }
        const data = await res.json();
        if (data.error) {
            showNoCache();
            return;
        }

        _overviewData = data;
        _setupsData   = data.top_setups || {};

        renderLastUpdated(data.last_updated);
        renderTfOverviewCards(data.timeframes);
        renderSignalsTable(data.signals || []);
        renderSetupTab(_activeSetupTab);
        loadReportCards(data.timeframes);

        document.getElementById('pulse-content').classList.remove('hidden');
    } catch (e) {
        showNoCache();
    }
}

function showNoCache() {
    document.getElementById('no-cache-msg').classList.remove('hidden');
    document.getElementById('pulse-last-updated').textContent = 'No scan cache found';
}

// ── Section 1: Cross-Timeframe Overview Cards ─────────────────────────────────

function renderTfOverviewCards(timeframes) {
    const container = document.getElementById('tf-overview-cards');
    if (!timeframes || Object.keys(timeframes).length === 0) {
        container.innerHTML = '<p class="text-sm text-base-content/40 col-span-3 text-center py-4">No timeframe data in cache.</p>';
        return;
    }

    container.innerHTML = ['long', 'swing', 'short'].map(tf => {
        const d = timeframes[tf];
        if (!d) return `
            <div class="card bg-base-100 shadow border border-base-300">
                <div class="card-body p-4">
                    <h3 class="font-semibold text-sm text-base-content/60">${TF_LABEL[tf]}</h3>
                    <p class="text-xs text-base-content/40 mt-2">No data — run a ${tf} scan</p>
                </div>
            </div>`;

        const stanceCls = STANCE_BADGE[d.stance] || 'badge-ghost';
        const bullW     = d.bullish_pct;
        const bearW     = d.bearish_pct;
        const noW       = Math.max(0, 100 - bullW - bearW);

        return `
            <div class="card bg-base-100 shadow border border-base-300">
                <div class="card-body p-4 gap-3">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <i class="bi ${TF_ICON[tf]} text-base-content/40"></i>
                            <div>
                                <h3 class="font-semibold text-sm">${TF_LABEL[tf]}</h3>
                                <p class="text-xs text-base-content/40">${TF_SUB[tf]}</p>
                            </div>
                        </div>
                        <span class="badge ${stanceCls} badge-sm">${d.stance}</span>
                    </div>

                    <!-- Regime bar — muted, order: bull | neutral | bear -->
                    <div>
                        <div class="flex text-xs text-base-content/40 justify-between mb-1">
                            <span>${d.bullish_pct}% Bull</span>
                            <span>${d.bearish_pct}% Bear</span>
                        </div>
                        <div class="flex h-1.5 rounded-full overflow-hidden w-full bg-base-300">
                            <div style="width:${bullW}%; background: oklch(var(--su) / 0.35)"></div>
                            <div style="width:${noW}%; background: oklch(var(--b3))"></div>
                            <div style="width:${bearW}%; background: oklch(var(--er) / 0.35)"></div>
                        </div>
                    </div>

                    <!-- Stats grid — neutral colors -->
                    <div class="grid grid-cols-3 gap-2 text-center">
                        <div>
                            <div class="text-lg font-bold text-base-content">${d.avg_score}</div>
                            <div class="text-xs text-base-content/50">Avg Score</div>
                        </div>
                        <div>
                            <div class="text-lg font-bold text-base-content">${d.signals}</div>
                            <div class="text-xs text-base-content/50">Signals</div>
                        </div>
                        <div>
                            <div class="text-lg font-bold text-base-content">${d.setups_forming}</div>
                            <div class="text-xs text-base-content/50">Setups</div>
                        </div>
                    </div>

                    <div class="text-xs text-base-content/40 text-center">${d.total} symbols scanned</div>
                </div>
            </div>`;
    }).join('');
}

// ── Section 2: Active Signals Table ──────────────────────────────────────────

function renderSignalsTable(signals) {
    const badge = document.getElementById('signals-count-badge');
    badge.textContent = signals.length;
    badge.className   = signals.length > 0 ? 'badge badge-warning badge-sm' : 'badge badge-ghost badge-sm';

    const wrap = document.getElementById('signals-table-wrap');
    if (!signals.length) {
        wrap.innerHTML = '<p class="text-sm text-base-content/40 text-center py-4">No active signals across any timeframe.</p>';
        return;
    }

    wrap.innerHTML = `
        <div class="overflow-x-auto">
            <table class="table table-sm table-zebra w-full">
                <thead>
                    <tr class="bg-base-200">
                        <th class="text-xs">Symbol</th>
                        <th class="text-xs">Timeframe</th>
                        <th class="text-xs">Signal</th>
                        <th class="text-xs">Score</th>
                        <th class="text-xs">Grade</th>
                        <th class="text-xs">RS vs SPY</th>
                        <th class="text-xs">RVOL</th>
                        <th class="text-xs">BB Width%</th>
                        <th class="text-xs">RSI</th>
                        <th class="text-xs">Price</th>
                    </tr>
                </thead>
                <tbody>
                    ${signals.map(r => signalRow(r)).join('')}
                </tbody>
            </table>
        </div>`;
}

function signalRow(r) {
    const sigCls  = r.signal === 'BUY' ? 'badge-success' : 'badge-error';
    const tfLabel = { long: 'Long', swing: 'Swing', short: 'Short' }[r.timeframe] || r.timeframe;
    // Only color RS vs SPY if meaningfully positive/negative (> ±2pp)
    const rsVal   = r.rs_vs_spy;
    const rsCls   = rsVal != null && Math.abs(rsVal) >= 2 ? (rsVal > 0 ? 'text-success' : 'text-error') : '';
    const rsStr   = rsVal != null ? (rsVal > 0 ? '+' : '') + rsVal : '—';
    return `
        <tr class="cursor-pointer hover:bg-base-200" onclick="window.location='/?symbol=${r.symbol}'">
            <td class="font-mono font-semibold text-sm">${r.symbol}</td>
            <td class="text-xs text-base-content/50">${tfLabel}</td>
            <td><span class="badge ${sigCls} badge-sm">${r.signal}</span></td>
            <td class="font-semibold">${r.score ?? '—'}</td>
            <td class="text-base-content/70">${r.grade ?? '—'}</td>
            <td class="${rsCls}">${rsStr}</td>
            <td>${r.rvol != null ? r.rvol + 'x' : '—'}</td>
            <td>${r.bb_width_pct != null ? r.bb_width_pct + '%' : '—'}</td>
            <td>${r.rsi ?? '—'}</td>
            <td class="font-mono">$${r.price ?? '—'}</td>
        </tr>`;
}

// ── Section 3: Top Setups by Timeframe ───────────────────────────────────────

function showSetupTab(tf, btn) {
    _activeSetupTab = tf;
    document.querySelectorAll('#setups-tabs .tab').forEach(b => {
        b.classList.remove('tab-active', '!bg-base-300', '!text-base-content');
        b.classList.add('!text-base-content/60');
    });
    btn.classList.add('tab-active', '!bg-base-300', '!text-base-content');
    btn.classList.remove('!text-base-content/60');
    renderSetupTab(tf);
}

function renderSetupTab(tf) {
    const wrap = document.getElementById('setups-table-wrap');
    const rows = _setupsData[tf];
    if (!rows || !rows.length) {
        wrap.innerHTML = `<p class="text-sm text-base-content/40 text-center py-4">No ${tf} data in cache — run a ${tf} scan.</p>`;
        return;
    }

    wrap.innerHTML = `
        <div class="overflow-x-auto">
            <table class="table table-sm table-zebra w-full">
                <thead>
                    <tr class="bg-base-200">
                        <th class="text-xs">#</th>
                        <th class="text-xs">Symbol</th>
                        <th class="text-xs">Signal</th>
                        <th class="text-xs">Score</th>
                        <th class="text-xs">Grade</th>
                        <th class="text-xs">Regime</th>
                        <th class="text-xs">RS vs SPY</th>
                        <th class="text-xs">RVOL</th>
                        <th class="text-xs">BB Width%</th>
                        <th class="text-xs">RSI</th>
                        <th class="text-xs">Price</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map((r, i) => setupRow(r, i + 1)).join('')}
                </tbody>
            </table>
        </div>`;
}

function setupRow(r, rank) {
    const sigCls   = r.signal === 'BUY' ? 'badge-success' : r.signal === 'SELL' ? 'badge-error' : 'badge-ghost';
    const sigLabel = r.signal === 'NONE' ? '—' : r.signal;
    // Only color RS vs SPY if meaningfully positive/negative (> ±2pp)
    const rsVal    = r.rs_vs_spy;
    const rsCls    = rsVal != null && Math.abs(rsVal) >= 2 ? (rsVal > 0 ? 'text-success' : 'text-error') : '';
    const rsStr    = rsVal != null ? (rsVal > 0 ? '+' : '') + rsVal : '—';
    return `
        <tr class="cursor-pointer hover:bg-base-200" onclick="window.location='/?symbol=${r.symbol}'">
            <td class="text-xs text-base-content/40">${rank}</td>
            <td class="font-mono font-semibold text-sm">${r.symbol}</td>
            <td><span class="badge ${sigCls} badge-sm">${sigLabel}</span></td>
            <td class="font-semibold">${r.score ?? '—'}</td>
            <td class="text-base-content/70">${r.grade ?? '—'}</td>
            <td class="text-xs text-base-content/50">${r.regime ?? '—'}</td>
            <td class="${rsCls}">${rsStr}</td>
            <td>${r.rvol != null ? r.rvol + 'x' : '—'}</td>
            <td>${r.bb_width_pct != null ? r.bb_width_pct + '%' : '—'}</td>
            <td>${r.rsi ?? '—'}</td>
            <td class="font-mono">$${r.price ?? '—'}</td>
        </tr>`;
}

// ── Section 4: Timeframe Report Cards ────────────────────────────────────────

function loadReportCards(timeframes) {
    ['long', 'swing', 'short'].forEach(tf => {
        const d = timeframes[tf];
        const el = document.getElementById(`report-${tf}`);
        if (!el) return;
        if (!d) {
            el.innerHTML = '<p class="text-xs text-base-content/40">No data — run a scan.</p>';
            return;
        }
        el.innerHTML = buildReportCard(d, tf);
    });
}

function buildReportCard(d, tf) {
    const stanceCls = STANCE_BADGE[d.stance] || 'badge-ghost';
    const gd = d.grade_dist || {};
    const fc = d.failure_counts || {};
    const fcSorted = Object.entries(fc).sort((a, b) => b[1] - a[1]).slice(0, 5);

    return `
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div class="stat bg-base-200 rounded-lg p-3">
                <div class="stat-title text-xs">Stance</div>
                <div class="stat-value text-base"><span class="badge ${stanceCls}">${d.stance}</span></div>
            </div>
            <div class="stat bg-base-200 rounded-lg p-3">
                <div class="stat-title text-xs">Avg Score</div>
                <div class="stat-value text-xl text-primary">${d.avg_score}</div>
            </div>
            <div class="stat bg-base-200 rounded-lg p-3">
                <div class="stat-title text-xs">Signals</div>
                <div class="stat-value text-xl text-success">${d.signals} <span class="text-sm text-base-content/40">(${d.signal_rate}%)</span></div>
            </div>
            <div class="stat bg-base-200 rounded-lg p-3">
                <div class="stat-title text-xs">Setups Forming</div>
                <div class="stat-value text-xl text-warning">${d.setups_forming} <span class="text-sm text-base-content/40">(${d.setup_rate}%)</span></div>
            </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <!-- Grade distribution -->
            <div class="bg-base-200 rounded-lg p-3">
                <h4 class="text-xs font-semibold text-base-content/50 uppercase mb-2">Grade Distribution</h4>
                <div class="space-y-1">
                    ${['A','B','C','D'].map(g => {
                        const cnt = gd[g] || 0;
                        const pct = d.total ? Math.round(cnt / d.total * 100) : 0;
                        const barCls = g === 'A' ? 'progress-success' : g === 'B' ? 'progress-info' : g === 'C' ? 'progress-warning' : 'progress-error';
                        return `<div class="flex items-center gap-2 text-xs">
                            <span class="w-4 shrink-0 font-bold">${g}</span>
                            <progress class="progress ${barCls} flex-1 h-2 min-w-0" value="${pct}" max="100"></progress>
                            <span class="w-20 shrink-0 text-right text-base-content/60 whitespace-nowrap">${cnt} (${pct}%)</span>
                        </div>`;
                    }).join('')}
                </div>
            </div>

            <!-- Top failure gates -->
            <div class="bg-base-200 rounded-lg p-3">
                <h4 class="text-xs font-semibold text-base-content/50 uppercase mb-2">Why Signals Aren't Triggering</h4>
                ${fcSorted.length ? `<div class="space-y-1.5">
                    ${fcSorted.map(([gate, cnt]) => {
                        const pct = d.total ? Math.round(cnt / d.total * 100) : 0;
                        return `<div class="flex items-center gap-2 text-xs">
                            <span class="w-32 shrink-0 text-base-content/70">${gate}</span>
                            <progress class="progress progress-ghost flex-1 h-2" value="${pct}" max="100"></progress>
                            <span class="w-10 text-right text-base-content/60 shrink-0">${cnt}</span>
                        </div>`;
                    }).join('')}
                </div>` : '<p class="text-xs text-base-content/40">No failure data.</p>'}
            </div>
        </div>`;
}

// ── Section 5: AI Commentary ──────────────────────────────────────────────────

function toggleAiCommentary() {
    const body    = document.getElementById('ai-commentary-body');
    const chevron = document.getElementById('ai-chevron');
    const isOpen  = !body.classList.contains('hidden');

    if (isOpen) {
        body.classList.add('hidden');
        chevron.classList.remove('rotate-180');
        return;
    }

    body.classList.remove('hidden');
    chevron.classList.add('rotate-180');

    if (!_aiLoaded && !_aiStreaming) {
        streamAiCommentary();
    }
}

function streamAiCommentary() {
    _aiStreaming = true;
    const textEl  = document.getElementById('ai-commentary-text');
    const badge   = document.getElementById('ai-status-badge');
    badge.className   = 'badge badge-warning badge-sm';
    badge.textContent = 'Generating…';
    textEl.innerHTML  = '<span class="loading loading-dots loading-sm"></span>';

    // Use the "all" timeframe for the AI so it gets cross-timeframe context
    const evtSource = new EventSource('/api/market/pulse/stream?timeframe=all');
    let   fullText  = '';

    evtSource.onmessage = (e) => {
        const event = JSON.parse(e.data);
        if (event.type === 'chunk') {
            fullText += event.text;
            textEl.innerHTML = markdownToHtml(fullText);
        }
        if (event.type === 'done') {
            evtSource.close();
            _aiLoaded    = true;
            _aiStreaming  = false;
            badge.className   = 'badge badge-success badge-sm';
            badge.textContent = 'Ready';
        }
        if (event.type === 'error') {
            evtSource.close();
            _aiStreaming = false;
            badge.className   = 'badge badge-error badge-sm';
            badge.textContent = 'Error';
            textEl.innerHTML  = `<p class="text-error text-sm">${event.message}</p>`;
        }
    };

    evtSource.onerror = () => {
        evtSource.close();
        _aiStreaming = false;
        badge.className   = 'badge badge-error badge-sm';
        badge.textContent = 'Stream failed';
    };
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function toggleSection(bodyId, chevronId) {
    const body    = document.getElementById(bodyId);
    const chevron = document.getElementById(chevronId);
    const isOpen  = !body.classList.contains('hidden');
    body.classList.toggle('hidden', isOpen);
    chevron?.classList.toggle('rotate-180', !isOpen);
}

function renderLastUpdated(iso) {
    const el = document.getElementById('pulse-last-updated');
    if (!iso) { el.textContent = 'Cache age unknown'; return; }
    try {
        const d   = new Date(iso);
        const ago = Math.round((Date.now() - d.getTime()) / 60000);
        el.textContent = `Last scan: ${d.toLocaleString()} (${ago < 60 ? ago + 'm ago' : Math.round(ago / 60) + 'h ago'})`;
    } catch {
        el.textContent = `Last scan: ${iso}`;
    }
}

function markdownToHtml(md) {
    return md
        .replace(/^### (.+)$/gm, '<h3 class="text-sm font-bold mt-4 mb-1">$1</h3>')
        .replace(/^## (.+)$/gm,  '<h2 class="text-base font-bold mt-5 mb-2">$1</h2>')
        .replace(/^# (.+)$/gm,   '<h1 class="text-lg font-bold mt-6 mb-2">$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g,     '<em>$1</em>')
        .replace(/^- (.+)$/gm,    '<li class="ml-4 list-disc">$1</li>')
        .replace(/\n\n/g,          '<br><br>');
}
