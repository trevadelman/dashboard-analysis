/**
 * analysis.js — Multi-timeframe analysis streaming and UI rendering
 */

// ===== CARD BUILDERS =====

function buildTierCard(tier) {
    const resultClass = (tier.result === 'NO_TRADE' || tier.result === 'FAIL')
        ? 'text-error' : tier.result === 'SKIPPED' ? 'text-base-content/60' : 'text-success';
    const detailLines = Array.isArray(tier.details)
        ? tier.details.map(d => `<div class="text-xs text-base-content/60 font-mono">${d}</div>`).join('')
        : `<div class="text-xs text-base-content/60">${tier.details}</div>`;
    const confidenceBadge = tier.confidence !== undefined
        ? `<span class="badge badge-sm badge-ghost ml-2">${tier.confidence}%</span>` : '';
    const borderColor = (tier.result === 'PASS' || tier.result === 'BULLISH' || tier.result === 'BEARISH')
        ? 'border-success' : tier.result === 'SKIPPED' ? 'border-base-300' : 'border-error';
    return `
        <div class="card bg-base-100 border-l-4 ${borderColor} mb-2 tier-card" style="animation: slideIn 0.25s ease-out;">
            <div class="card-body p-3">
                <div class="flex justify-between items-center">
                    <strong class="text-sm">Tier ${tier.tier}: ${tier.name}</strong>
                    <span class="${resultClass} text-sm font-bold">${tier.result}${confidenceBadge}</span>
                </div>
                ${detailLines}
            </div>
        </div>`;
}

function buildSignalCard(sig, symbol) {
    const badgeClass  = sig.side === 'buy' ? 'badge-success' : sig.side === 'sell' ? 'badge-error' : 'badge-ghost';
    const riskAmt     = Math.abs(sig.entry_price - sig.stop_price).toFixed(2);
    const rewardAmt   = Math.abs(sig.target_price - sig.entry_price).toFixed(2);
    const rr          = riskAmt > 0 ? (rewardAmt / riskAmt).toFixed(1) : '—';
    return `
        <div class="card bg-base-100 border border-base-300 mt-4 tier-card" style="animation: slideIn 0.25s ease-out;">
            <div class="card-body p-4">
                <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div>
                        <h3 class="text-xs font-semibold text-base-content/60 uppercase mb-2">Signal</h3>
                        <div class="flex items-center gap-2 mb-2">
                            <span class="badge ${badgeClass} badge-lg">${sig.side.toUpperCase()}</span>
                            <span class="text-xl font-bold">${symbol}</span>
                        </div>
                        <p class="text-xs text-base-content/60">${sig.reason}</p>
                    </div>
                    <div>
                        <h3 class="text-xs font-semibold text-base-content/60 uppercase mb-2">Trade Levels</h3>
                        <div class="text-xs space-y-1">
                            <div>Entry: <strong>$${sig.entry_price}</strong></div>
                            <div>Stop: <strong class="text-error">$${sig.stop_price}</strong> <span class="text-base-content/60">(-$${riskAmt})</span></div>
                            <div>Target: <strong class="text-success">$${sig.target_price}</strong> <span class="text-base-content/60">(+$${rewardAmt})</span></div>
                            <div class="text-base-content/60">R:R = ${rr}:1</div>
                        </div>
                    </div>
                    <div>
                        <h3 class="text-xs font-semibold text-base-content/60 uppercase mb-2">AI Confirmation</h3>
                        <progress class="progress progress-primary w-full mb-2" value="${sig.ai_confidence || 0}" max="100"></progress>
                        <p class="text-xs text-base-content/60">${sig.ai_reasoning || 'No AI reasoning provided'}</p>
                    </div>
                    <div>
                        <label class="label"><span class="label-text text-xs">Order Duration</span></label>
                        <select id="tif-select-${sig.symbol}" class="select select-bordered select-sm w-full mb-2">
                            <option value="day">Day</option>
                            <option value="gtc" selected>GTC</option>
                        </select>
                        <button class="btn btn-success btn-sm w-full" onclick="executeTrade(${JSON.stringify(sig).replace(/"/g, '&quot;')}, document.getElementById('tif-select-${sig.symbol}').value)">
                            <i class="bi bi-lightning-fill"></i> Execute Trade
                        </button>
                        <div class="text-xs text-base-content/60 text-center mt-1">Bracket order</div>
                    </div>
                </div>
            </div>
        </div>`;
}

function buildAiCommentaryCard(text) {
    return `
        <div class="card bg-base-100 border border-primary/30 mt-4 tier-card" style="animation: slideIn 0.25s ease-out;">
            <div class="card-body p-4">
                <h3 class="text-xs font-semibold text-primary uppercase mb-2 flex items-center gap-1">
                    <i class="bi bi-cpu-fill"></i> AI Commentary
                </h3>
                <p class="text-sm text-base-content/80 leading-relaxed">${text}</p>
            </div>
        </div>`;
}

// ===== TIMEFRAME ROW HELPERS =====

const TF_META = {
    long:  { label: '📅 Long-term',  sub: '1D · 1 year' },
    swing: { label: '📊 Swing',      sub: '1H · 3 months' },
    short: { label: '⚡ Short-term', sub: '15m · 1 month' },
};

const VERDICT_BADGE = {
    SIGNAL:   'badge-success',
    NO_TRADE: 'badge-error',
    NO_ENTRY: 'badge-warning',
    ERROR:    'badge-ghost',
};

const OVERALL_STYLE = {
    ALIGNED:  { badge: 'badge-success', icon: '✅', label: 'ALIGNED' },
    PARTIAL:  { badge: 'badge-warning', icon: '⚠️', label: 'PARTIAL' },
    CAUTION:  { badge: 'badge-error',   icon: '⛔', label: 'CAUTION' },
    MIXED:    { badge: 'badge-ghost',   icon: '↔️', label: 'MIXED' },
};

function buildTimeframeRow(tf) {
    const meta = TF_META[tf];
    return `
        <div id="tf-row-${tf}" class="flex items-center justify-between p-3 rounded-lg bg-base-100 border border-base-300 cursor-pointer hover:bg-base-200 transition-colors" onclick="toggleTfPanel('${tf}')">
            <div>
                <span class="font-semibold text-sm">${meta.label}</span>
                <span class="text-xs text-base-content/50 ml-2">${meta.sub}</span>
            </div>
            <div class="flex items-center gap-2">
                <span id="tf-badge-${tf}" class="badge badge-ghost badge-sm">
                    <span class="loading loading-spinner loading-xs"></span>
                </span>
                <i id="tf-chevron-${tf}" class="bi bi-chevron-down text-xs text-base-content/40 transition-transform"></i>
            </div>
        </div>
        <div id="tf-panel-${tf}" class="hidden pl-2 pr-1 pb-2">
            <div id="tf-tiers-${tf}" class="space-y-1 mt-2"></div>
            <div id="tf-commentary-${tf}"></div>
        </div>`;
}

function updateTfBadge(tf, verdict) {
    const badge = document.getElementById(`tf-badge-${tf}`);
    if (!badge) return;
    const cls = VERDICT_BADGE[verdict] || 'badge-ghost';
    badge.className = `badge ${cls} badge-sm`;
    badge.textContent = verdict;
}

function toggleTfPanel(tf) {
    const panel   = document.getElementById(`tf-panel-${tf}`);
    const chevron = document.getElementById(`tf-chevron-${tf}`);
    if (!panel) return;
    const isOpen = !panel.classList.contains('hidden');
    // Close all panels first
    ['long', 'swing', 'short'].forEach(t => {
        document.getElementById(`tf-panel-${t}`)?.classList.add('hidden');
        document.getElementById(`tf-chevron-${t}`)?.classList.remove('rotate-180');
    });
    // Toggle the clicked one
    if (!isOpen) {
        panel.classList.remove('hidden');
        chevron?.classList.add('rotate-180');
    }
}

// ===== MAIN ANALYSIS RUNNER =====

async function runAnalysis() {
    const symbol = document.getElementById('symbol-input').value.toUpperCase();
    const output = document.getElementById('analysis-results-content');
    window.currentSymbol = symbol;

    // Build the skeleton UI
    output.innerHTML = `
        <div class="mb-4">
            <div class="flex items-center justify-between mb-3">
                <h3 class="text-xs font-semibold text-base-content/60 uppercase">Multi-Timeframe Analysis — ${symbol}</h3>
                <span id="overall-badge" class="badge badge-ghost">
                    <span class="loading loading-spinner loading-xs mr-1"></span> Analyzing…
                </span>
            </div>
            <div class="space-y-2" id="tf-rows">
                ${buildTimeframeRow('long')}
                ${buildTimeframeRow('swing')}
                ${buildTimeframeRow('short')}
            </div>
        </div>
        <div id="analysis-signals"></div>`;

    await loadChart();

    const evtSource = new EventSource(`/api/analyze/stream/multi?symbol=${encodeURIComponent(symbol)}&use_ai=true`);

    evtSource.onmessage = (e) => {
        const event = JSON.parse(e.data);

        if (event.type === 'error') {
            output.innerHTML = `<div class="alert alert-error"><span>${event.message}</span></div>`;
            evtSource.close();
            return;
        }

        if (event.type === 'tf_error') {
            updateTfBadge(event.timeframe, 'ERROR');
            return;
        }

        if (event.type === 'tier') {
            const container = document.getElementById(`tf-tiers-${event.timeframe}`);
            if (container) container.insertAdjacentHTML('beforeend', buildTierCard(event));
        }

        if (event.type === 'ai_commentary') {
            const container = document.getElementById(`tf-commentary-${event.timeframe}`);
            if (container) container.innerHTML = buildAiCommentaryCard(event.text);
        }

        if (event.type === 'done') {
            const verdict = event.signals && event.signals.length > 0 ? 'SIGNAL'
                : event.blocked_at && event.blocked_at.startsWith('Tier 1') ? 'NO_TRADE'
                : 'NO_ENTRY';
            updateTfBadge(event.timeframe, verdict);

            if (event.blocked_at) {
                const container = document.getElementById(`tf-tiers-${event.timeframe}`);
                if (container) container.insertAdjacentHTML('beforeend',
                    `<div class="alert alert-warning py-2 text-xs mb-1"><span>⛔ Blocked at: <strong>${event.blocked_at}</strong></span></div>`);
            }
        }

        if (event.type === 'summary') {
            const style = OVERALL_STYLE[event.overall] || OVERALL_STYLE['MIXED'];
            const overallBadge = document.getElementById('overall-badge');
            if (overallBadge) {
                overallBadge.className = `badge ${style.badge}`;
                overallBadge.textContent = `${style.icon} ${style.label}`;
            }

            if (event.signals && event.signals.length > 0) {
                const signalHtml = event.signals.map(sig => buildSignalCard(sig, symbol)).join('');
                // Insert signal cards ABOVE the timeframe rows so they're immediately visible
                const output = document.getElementById('analysis-results-content');
                output.insertAdjacentHTML('afterbegin', `<div id="signal-cards-top">${signalHtml}</div>`);
                // Also keep a reference in the signals container below for context
                const signalsContainer = document.getElementById('analysis-signals');
                if (signalsContainer) signalsContainer.innerHTML = '';
            }

            evtSource.close();
        }
    };

    evtSource.onerror = () => {
        evtSource.close();
        const overallBadge = document.getElementById('overall-badge');
        if (overallBadge) {
            overallBadge.className = 'badge badge-error';
            overallBadge.textContent = '❌ Stream failed';
        }
    };
}

async function refreshAll() {
    document.getElementById('loading-overlay').style.display = 'flex';
    await Promise.all([updateAccount(), updatePositions(), updateTrades(), loadConfig(), loadChart()]);
    document.getElementById('loading-overlay').style.display = 'none';
}

// ── URL ?symbol= pre-fill and auto-run ───────────────────────────────────────

(function() {
    const urlSymbol = new URLSearchParams(window.location.search).get('symbol');
    if (urlSymbol) {
        // Pre-fill the input as soon as the DOM is ready (before window.onload)
        document.addEventListener('DOMContentLoaded', () => {
            const input = document.getElementById('symbol-input');
            if (input) input.value = urlSymbol.toUpperCase();
        });
    }

    // dashboard.html sets window.onload after this script loads.
    // We use a load event listener so both can coexist without overwriting each other.
    // runAnalysis() is called after refreshAll() completes via a small delay.
    if (urlSymbol) {
        window.addEventListener('load', () => {
            // Give refreshAll() time to finish before starting the analysis stream
            setTimeout(runAnalysis, 800);
        });
    }
})();
