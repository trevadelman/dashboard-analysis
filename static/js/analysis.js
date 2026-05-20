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
    const badgeClass = sig.side === 'buy' ? 'badge-success' : sig.side === 'sell' ? 'badge-error' : 'badge-ghost';
    const riskAmt    = Math.abs(sig.entry_price - sig.stop_price).toFixed(2);
    const rewardAmt  = Math.abs(sig.target_price - sig.entry_price).toFixed(2);
    const rr         = riskAmt > 0 ? (rewardAmt / riskAmt).toFixed(1) : '—';
    const entryLabel = sig.entry_type === 'limit' ? 'Limit Entry' : 'Market Entry';
    const sigJson    = JSON.stringify(sig).replace(/"/g, '&quot;');
    return `
        <div class="card bg-base-100 border border-base-300 mt-4 tier-card" style="animation: slideIn 0.25s ease-out;">
            <div class="card-body p-4">
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <h3 class="text-xs font-semibold text-base-content/60 uppercase mb-2">Signal</h3>
                        <div class="flex items-center gap-2 mb-2">
                            <span class="badge ${badgeClass} badge-lg">${sig.side.toUpperCase()}</span>
                            <span class="text-xl font-bold">${symbol}</span>
                        </div>
                        <div class="badge badge-outline badge-sm mb-2">${entryLabel}</div>
                        <p class="text-xs text-base-content/60">${sig.reason}</p>
                    </div>
                    <div>
                        <h3 class="text-xs font-semibold text-base-content/60 uppercase mb-2">Trade Levels</h3>
                        <div class="text-xs space-y-1">
                            <div>${entryLabel}: <strong>$${sig.entry_price}</strong></div>
                            <div>Stop: <strong class="text-error">$${sig.stop_price}</strong> <span class="text-base-content/60">(-$${riskAmt})</span></div>
                            <div>Target: <strong class="text-success">$${sig.target_price}</strong> <span class="text-base-content/60">(+$${rewardAmt})</span></div>
                            <div class="text-base-content/60">R:R = ${rr}:1</div>
                        </div>
                        ${sig.ai_confidence ? `
                        <div class="mt-3">
                            <h3 class="text-xs font-semibold text-base-content/60 uppercase mb-1">AI Confidence</h3>
                            <progress class="progress progress-primary w-full" value="${sig.ai_confidence}" max="100"></progress>
                            <p class="text-xs text-base-content/60 mt-1">${sig.ai_reasoning || ''}</p>
                        </div>` : ''}
                    </div>
                    <div class="flex flex-col justify-end">
                        <button class="btn btn-success btn-sm w-full gap-2"
                                onclick="openTradeModal(${sigJson})">
                            <i class="bi bi-lightning-fill"></i> Review &amp; Execute
                        </button>
                        <div class="text-xs text-base-content/60 text-center mt-1">
                            Limit bracket · 2R target · Phase 2: runner trail
                        </div>
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
    long:  { label: 'Long-term',  icon: 'bi-calendar3',      sub: '1D · 1 year' },
    swing: { label: 'Swing',      icon: 'bi-bar-chart-line',  sub: '1H · 3 months' },
    short: { label: 'Short-term', icon: 'bi-lightning',       sub: '15m · 1 month' },
};

const VERDICT_BADGE = {
    SIGNAL:   'badge-success',
    NO_TRADE: 'badge-error',
    NO_ENTRY: 'badge-warning',
    ERROR:    'badge-ghost',
};

const OVERALL_STYLE = {
    ALIGNED:  { badge: 'badge-success', label: 'ALIGNED' },
    PARTIAL:  { badge: 'badge-warning', label: 'PARTIAL' },
    CAUTION:  { badge: 'badge-error',   label: 'CAUTION' },
    MIXED:    { badge: 'badge-ghost',   label: 'MIXED' },
};

function buildTimeframeRow(tf) {
    const meta = TF_META[tf];
    return `
        <div class="rounded-lg bg-base-100 border border-base-300 flex flex-col">
            <!-- Column header — always visible -->
            <div class="flex items-center justify-between p-3 border-b border-base-300">
                <div>
                    <span class="font-semibold text-sm">${meta.label}</span>
                    <span class="text-xs text-base-content/50 ml-2">${meta.sub}</span>
                </div>
                <span id="tf-badge-${tf}" class="badge badge-ghost badge-sm">
                    <span class="loading loading-spinner loading-xs"></span>
                </span>
            </div>
            <!-- Tier cards stream in here -->
            <div id="tf-tiers-${tf}" class="space-y-1 p-2 flex-1"></div>
            <div id="tf-commentary-${tf}" class="px-2 pb-2"></div>
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

    // Reset the consolidated AI commentary container
    const aiWrap = document.getElementById('analysis-ai-commentary');
    if (aiWrap) { aiWrap.innerHTML = ''; aiWrap.classList.add('hidden'); }

    // Build the skeleton UI — three columns side by side
    output.innerHTML = `
        <div class="mb-4">
            <div class="flex items-center justify-between mb-3">
                <h3 class="text-xs font-semibold text-base-content/60 uppercase">Multi-Timeframe Analysis — ${symbol}</h3>
                <span id="overall-badge" class="badge badge-ghost">
                    <span class="loading loading-spinner loading-xs mr-1"></span> Analyzing…
                </span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3" id="tf-rows">
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
            // Consolidated commentary (timeframe='all') goes below the grid
            if (event.timeframe === 'all') {
                const wrap = document.getElementById('analysis-ai-commentary');
                if (wrap) {
                    wrap.innerHTML = buildAiCommentaryCard(event.text);
                    wrap.classList.remove('hidden');
                }
            } else {
                const container = document.getElementById(`tf-commentary-${event.timeframe}`);
                if (container) container.innerHTML = buildAiCommentaryCard(event.text);
            }
        }

        if (event.type === 'done') {
            const verdict = event.signals && event.signals.length > 0 ? 'SIGNAL'
                : event.blocked_at && event.blocked_at.startsWith('Tier 1') ? 'NO_TRADE'
                : 'NO_ENTRY';
            updateTfBadge(event.timeframe, verdict);

            if (event.blocked_at) {
                const container = document.getElementById(`tf-tiers-${event.timeframe}`);
                if (container) container.insertAdjacentHTML('beforeend',
                    `<div class="alert alert-warning py-2 text-xs mb-1"><span>Blocked at: <strong>${event.blocked_at}</strong></span></div>`);
            }
        }

        if (event.type === 'summary') {
            const style = OVERALL_STYLE[event.overall] || OVERALL_STYLE['MIXED'];
            const overallBadge = document.getElementById('overall-badge');
            if (overallBadge) {
                overallBadge.className = `badge ${style.badge}`;
                overallBadge.textContent = style.label;
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
            overallBadge.textContent = 'Stream failed';
        }
    };
}

// ===== TRADE MODAL =====

let _pendingTrade = null;

function openTradeModal(sig) {
    _pendingTrade = sig;

    const riskAmt   = Math.abs(sig.entry_price - sig.stop_price);
    const rewardAmt = Math.abs(sig.target_price - sig.entry_price);
    const rr        = riskAmt > 0 ? (rewardAmt / riskAmt).toFixed(1) : '2.0';

    // Header
    const badge = document.getElementById('tm-badge');
    badge.textContent  = sig.side.toUpperCase();
    badge.className    = `badge badge-lg ${sig.side === 'buy' ? 'badge-success' : 'badge-error'}`;
    document.getElementById('tm-symbol').textContent     = sig.symbol;
    document.getElementById('tm-entry-type').textContent = sig.entry_type === 'limit' ? 'Limit Entry' : 'Market Entry';

    // Levels
    document.getElementById('tm-entry').textContent    = `$${sig.entry_price}`;
    document.getElementById('tm-stop').textContent     = `$${sig.stop_price}`;
    document.getElementById('tm-target').textContent   = `$${sig.target_price}`;
    document.getElementById('tm-risk-amt').textContent = `-$${riskAmt.toFixed(2)}`;
    document.getElementById('tm-reward-amt').textContent = `+$${rewardAmt.toFixed(2)}`;
    document.getElementById('tm-rr').textContent       = `${rr}:1`;

    // Position sizing — estimate from account equity if available
    const equityEl = document.getElementById('equity');
    const equityStr = equityEl ? equityEl.textContent.replace(/[$,]/g, '') : '0';
    const equity    = parseFloat(equityStr) || 0;
    const riskPct   = 0.01; // 1% default — matches bot default
    if (equity > 0 && riskAmt > 0) {
        const dollarRisk = equity * riskPct;
        const qty        = Math.max(1, Math.floor(dollarRisk / riskAmt));
        const totalRisk  = qty * riskAmt;
        document.getElementById('tm-qty').textContent        = `${qty} shares`;
        document.getElementById('tm-dollar-risk').textContent = `~$${totalRisk.toFixed(0)} at risk (1% equity)`;
    } else {
        document.getElementById('tm-qty').textContent        = '—';
        document.getElementById('tm-dollar-risk').textContent = 'Connect account for sizing';
    }

    // Reason
    document.getElementById('tm-reason').textContent = sig.reason || '';

    // Reset confirm button
    const btn = document.getElementById('tm-confirm-btn');
    btn.disabled    = false;
    btn.textContent = '';
    btn.innerHTML   = '<i class="bi bi-lightning-fill"></i> Place Order';

    document.getElementById('trade-modal').showModal();
}

async function confirmTrade() {
    if (!_pendingTrade) return;

    const btn = document.getElementById('tm-confirm-btn');
    btn.disabled  = true;
    btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Placing…';

    const tif = document.getElementById('tm-tif').value;

    try {
        const res  = await fetch('/api/execute_trade', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ ..._pendingTrade, time_in_force: tif }),
        });
        const data = await res.json();

        if (data.error) {
            btn.disabled  = false;
            btn.innerHTML = '<i class="bi bi-exclamation-triangle-fill"></i> Error — retry';
            alert(`Order failed: ${data.error}`);
            return;
        }

        document.getElementById('trade-modal').close();
        _pendingTrade = null;

        // Refresh positions/trades after a short delay
        setTimeout(() => { updatePositions(); updateTrades(); }, 1500);

    } catch (err) {
        btn.disabled  = false;
        btn.innerHTML = '<i class="bi bi-exclamation-triangle-fill"></i> Error — retry';
        alert(`Network error: ${err.message}`);
    }
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
