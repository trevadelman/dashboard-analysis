/**
 * market.js — Market Pulse page
 * Loads aggregate scan cache stats and streams AI commentary.
 */

let _pulseData   = null;
let _aiSource    = null;
let _timeframe   = 'long';
let _aiRawText   = '';   // accumulates raw markdown chunks

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadPulse();
});

// ── Data loading ──────────────────────────────────────────────────────────────

function getSelectedTimeframe() {
    const checked = document.querySelector('input[name="pulse-timeframe"]:checked');
    return checked ? checked.value : 'long';
}

async function loadPulse() {
    _timeframe = getSelectedTimeframe();

    // Reset AI state
    if (_aiSource) { _aiSource.close(); _aiSource = null; }
    document.getElementById('ai-commentary').textContent = '';
    document.getElementById('ai-commentary').classList.add('hidden');
    document.getElementById('ai-error').classList.add('hidden');
    document.getElementById('ai-loading').classList.remove('hidden');

    try {
        const res = await fetch(`/api/market/pulse?timeframe=${encodeURIComponent(_timeframe)}`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showError(err.error || 'No scan cache found. Run a scan in the Market Scanner first.');
            return;
        }

        _pulseData = await res.json();
        renderPulse(_pulseData);
        streamAI();

    } catch (e) {
        showError(`Failed to load market pulse: ${e.message}`);
    }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function renderPulse(data) {
    document.getElementById('pulse-error').classList.add('hidden');
    document.getElementById('pulse-content').classList.remove('hidden');

    const o = data.overall;

    // Cache age
    if (data.last_updated) {
        const diff = Math.round((Date.now() - new Date(data.last_updated).getTime()) / 1000);
        const age  = diff < 60 ? `${diff}s` : diff < 3600 ? `${Math.round(diff/60)}m` : `${Math.round(diff/3600)}h`;
        document.getElementById('pulse-cache-age').textContent = `Cache: ${age} ago`;
    }

    // Stance badge
    const stanceBadge = document.getElementById('stance-badge');
    const stanceClass = {
        'RISK ON':   'badge-success',
        'SELECTIVE': 'badge-warning',
        'WAIT':      'badge-info',
        'RISK OFF':  'badge-error',
    }[o.stance] || 'badge-ghost';
    stanceBadge.className = `badge badge-lg text-base font-bold px-4 py-3 ${stanceClass}`;
    stanceBadge.textContent = o.stance;

    // Stance summary
    document.getElementById('stance-summary').textContent = _stanceSummary(o);

    // Stats
    document.getElementById('stat-signal-rate').textContent = `${o.signal_rate}%`;
    document.getElementById('stat-signal-count').textContent = `${o.signals} of ${o.total} symbols`;
    document.getElementById('stat-setup-rate').textContent = `${o.setup_rate}%`;
    document.getElementById('stat-setup-count').textContent = `${o.setups_forming} compressed + outperforming`;
    document.getElementById('stat-bullish-pct').textContent = `${o.bullish_pct}%`;
    document.getElementById('stat-bearish-pct').textContent = `${o.bearish_pct}% bearish`;
    document.getElementById('stat-avg-score').textContent = o.avg_score;

    // Failure bars
    renderBars('failure-bars', o.failure_counts, 'bg-error');

    // Grade bars — all symbols graded by score
    const gradeCounts = {
        'A (80-100)': o.grade_dist['A'] || 0,
        'B (60-79)':  o.grade_dist['B'] || 0,
        'C (40-59)':  o.grade_dist['C'] || 0,
        'D (0-39)':   o.grade_dist['D'] || 0,
    };
    const gradeColors = {
        'A (80-100)': 'bg-success',
        'B (60-79)':  'bg-warning',
        'C (40-59)':  'bg-info',
        'D (0-39)':   'bg-base-300',
    };
    renderBars('grade-bars', gradeCounts, null, gradeColors);

    // Sector table
    renderSectorTable(data.sectors);
}

function _stanceSummary(o) {
    const signalWord = o.signal_rate < 2 ? 'very few' : o.signal_rate < 5 ? 'few' : o.signal_rate < 15 ? 'some' : 'many';
    const regimeWord = o.bullish_pct > 70 ? 'broadly bullish' : o.bullish_pct > 50 ? 'mostly bullish' : o.bullish_pct > 30 ? 'mixed' : 'mostly bearish';
    const failWord   = o.top_failure !== 'N/A' ? `Most setups are failing on ${o.top_failure}.` : '';
    return `${o.total} symbols scanned — ${regimeWord} regime, ${signalWord} signals (${o.signal_rate}%). ${failWord}`;
}

function renderBars(containerId, counts, defaultColor, colorMap) {
    const container = document.getElementById(containerId);
    if (!counts || Object.keys(counts).length === 0) {
        container.innerHTML = '<span class="text-xs text-base-content/40">No data</span>';
        return;
    }

    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);

    container.innerHTML = sorted.map(([label, count]) => {
        const pct   = total > 0 ? Math.round((count / total) * 100) : 0;
        const color = (colorMap && colorMap[label]) || defaultColor || 'bg-primary';
        return `
            <div>
                <div class="flex justify-between text-xs mb-0.5">
                    <span class="font-medium">${label}</span>
                    <span class="text-base-content/50">${count} (${pct}%)</span>
                </div>
                <div class="w-full bg-base-300 rounded-full h-1.5">
                    <div class="${color} h-1.5 rounded-full" style="width: ${pct}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderSectorTable(sectors) {
    const tbody = document.getElementById('sector-table-body');
    if (!sectors || sectors.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-base-content/40 py-6 text-sm">No sector data available.</td></tr>';
        return;
    }

    tbody.innerHTML = sectors.map((s, i) => {
        const rankClass = i === 0 ? 'text-success font-semibold' : i === sectors.length - 1 ? 'text-error' : '';
        const bullishClass = s.bullish_pct > 70 ? 'text-success' : s.bullish_pct < 30 ? 'text-error' : '';
        const signalClass  = s.signal_rate >= 10 ? 'text-success font-semibold' : '';
        const setupClass   = (s.setup_rate || 0) >= 10 ? 'text-warning font-semibold' : '';
        return `
            <tr class="hover">
                <td class="font-medium text-sm ${rankClass}">${s.sector}</td>
                <td class="text-right text-sm">${s.total}</td>
                <td class="text-right text-sm font-semibold">${s.avg_score}</td>
                <td class="text-right text-sm ${signalClass}">${s.signals} (${s.signal_rate}%)</td>
                <td class="text-right text-sm ${setupClass}">${s.setups_forming || 0} (${s.setup_rate || 0}%)</td>
                <td class="text-right text-sm ${bullishClass}">${s.bullish_pct}%</td>
                <td class="text-xs text-base-content/50">${s.top_failure || '—'}</td>
            </tr>
        `;
    }).join('');
}

// ── AI Commentary ─────────────────────────────────────────────────────────────

function streamAI() {
    if (_aiSource) { _aiSource.close(); _aiSource = null; }

    const commentary = document.getElementById('ai-commentary');
    const loading    = document.getElementById('ai-loading');
    const errEl      = document.getElementById('ai-error');

    commentary.textContent = '';
    commentary.classList.add('hidden');
    errEl.classList.add('hidden');
    loading.classList.remove('hidden');

    document.getElementById('ai-regen-btn').disabled = true;

    _aiSource = new EventSource(`/api/market/pulse/stream?timeframe=${encodeURIComponent(_timeframe)}`);

    _aiRawText = '';

    _aiSource.onmessage = (e) => {
        try {
            const evt = JSON.parse(e.data);
            if (evt.type === 'chunk') {
                loading.classList.add('hidden');
                commentary.classList.remove('hidden');
                _aiRawText += evt.text;
                // Re-render the full accumulated markdown on each chunk
                commentary.innerHTML = marked.parse(_aiRawText);
            } else if (evt.type === 'done') {
                _aiSource.close();
                _aiSource = null;
                document.getElementById('ai-regen-btn').disabled = false;
            } else if (evt.type === 'error') {
                loading.classList.add('hidden');
                errEl.textContent = evt.message || 'AI commentary unavailable.';
                errEl.classList.remove('hidden');
                _aiSource.close();
                _aiSource = null;
                document.getElementById('ai-regen-btn').disabled = false;
            }
        } catch (_) {}
    };

    _aiSource.onerror = () => {
        loading.classList.add('hidden');
        errEl.textContent = 'AI connection lost.';
        errEl.classList.remove('hidden');
        _aiSource.close();
        _aiSource = null;
        document.getElementById('ai-regen-btn').disabled = false;
    };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function showError(msg) {
    document.getElementById('pulse-error-msg').textContent = msg;
    document.getElementById('pulse-error').classList.remove('hidden');
    document.getElementById('pulse-content').classList.add('hidden');
    document.getElementById('ai-loading').classList.add('hidden');
}
