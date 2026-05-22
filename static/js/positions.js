/**
 * positions.js — Positions page: summary bar + accordion cards with mini charts
 */

// Track LightweightCharts instances so we can destroy them when cards collapse
const _posCharts = {};

// ── Bot blacklist ─────────────────────────────────────────────────────────────

// Set of symbols currently blacklisted from bot activity (loaded on page init)
let _blacklist = new Set();

async function loadBlacklist() {
    try {
        const res  = await fetch('/api/bot/blacklist');
        const data = await res.json();
        _blacklist = new Set((data.blacklisted || []).map(s => s.toUpperCase()));
    } catch (_) {
        _blacklist = new Set();
    }
}

async function toggleBlacklist(symbol, forceState) {
    try {
        const res  = await fetch(`/api/bot/blacklist/${encodeURIComponent(symbol)}`, { method: 'POST' });
        const data = await res.json();
        if (data.blacklisted) {
            _blacklist.add(symbol.toUpperCase());
        } else {
            _blacklist.delete(symbol.toUpperCase());
        }
        _updateBlacklistBtn(symbol, data.blacklisted);
        _updateBlacklistToggle(symbol, data.blacklisted);
    } catch (err) {
        console.error('Blacklist toggle failed:', err);
    }
}

function _updateBlacklistBtn(symbol, blacklisted) {
    const btn = document.getElementById(`blacklist-btn-${symbol}`);
    if (!btn) return;
    if (blacklisted) {
        btn.title     = 'Bot blacklisted';
        btn.innerHTML = '<i class="bi bi-robot text-error"></i>';
        btn.classList.add('btn-error', 'btn-outline');
        btn.classList.remove('btn-ghost');
    } else {
        btn.title     = 'Bot active';
        btn.innerHTML = '<i class="bi bi-robot"></i>';
        btn.classList.remove('btn-error', 'btn-outline');
        btn.classList.add('btn-ghost');
    }
}

function _updateBlacklistToggle(symbol, blacklisted) {
    const toggle = document.getElementById(`blacklist-toggle-${symbol}`);
    const label  = document.getElementById(`blacklist-label-${symbol}`);
    if (toggle) toggle.checked = !blacklisted;
    if (label) {
        label.textContent = blacklisted ? 'Bot Blacklisted' : 'Bot Managed';
        label.className   = blacklisted ? 'text-xs text-error font-semibold' : 'text-xs text-base-content/60';
    }
}

// ── Price line extraction ─────────────────────────────────────────────────────

function extractPriceLevels(avgEntry, orders) {
    const levels = { entry: avgEntry, stop: null, target: null };

    for (const order of orders) {
        // Check the order itself (Alpaca returns bracket legs as sibling orders, not nested)
        if ((order.type === 'stop' || order.type === 'stop_limit') && order.stop_price) {
            levels.stop = order.stop_price;
        }
        if (order.type === 'limit' && order.side === 'sell' && order.limit_price) {
            levels.target = order.limit_price;
        }

        // Also check nested legs if present
        if (!order.legs) continue;
        for (const leg of order.legs) {
            if ((leg.type === 'stop' || leg.type === 'stop_limit') && leg.stop_price) {
                levels.stop = leg.stop_price;
            }
            if (leg.type === 'limit' && leg.limit_price) {
                levels.target = leg.limit_price;
            }
        }
    }

    return levels;
}

// ── Mini chart ────────────────────────────────────────────────────────────────

async function initPositionChart(symbol, containerId, levels) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Destroy any existing chart for this symbol
    if (_posCharts[symbol]) {
        _posCharts[symbol].remove();
        delete _posCharts[symbol];
    }

    try {
        const res  = await fetch(`/api/market_data?symbol=${encodeURIComponent(symbol)}&interval=1d&period=3mo`);
        const data = await res.json();
        if (!data || !data.length) {
            container.innerHTML = '<div class="text-xs text-base-content/40 text-center py-6">No chart data available</div>';
            return;
        }

        const chart = LightweightCharts.createChart(container, {
            width:           container.clientWidth,
            height:          200,
            layout:          { backgroundColor: 'transparent', textColor: '#4A6355' },
            grid:            { vertLines: { color: '#E4EBE6' }, horzLines: { color: '#E4EBE6' } },
            crosshair:       { mode: LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: { borderColor: '#C8D5CB' },
            timeScale:       { borderColor: '#C8D5CB', timeVisible: true },
            handleScroll:    true,
            handleScale:     true,
        });

        const candleSeries = chart.addCandlestickSeries({
            upColor: '#0D9B55', downColor: '#D63B3B',
            borderVisible: false,
            wickUpColor: '#0D9B55', wickDownColor: '#D63B3B',
        });

        const toSec = d => typeof d.timestamp === 'number'
            ? d.timestamp / 1000
            : new Date(d.timestamp).getTime() / 1000;

        const candles = data
            .map(d => ({ time: toSec(d), open: d.open, high: d.high, low: d.low, close: d.close }))
            .sort((a, b) => a.time - b.time);

        candleSeries.setData(candles);

        // Entry price line (Signal Green dashed)
        if (levels.entry) {
            candleSeries.createPriceLine({
                price:     levels.entry,
                color:     '#0D9B55',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'Entry',
            });
        }

        // Stop loss price line (Loss Red dashed)
        if (levels.stop) {
            candleSeries.createPriceLine({
                price:     levels.stop,
                color:     '#D63B3B',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'Stop',
            });
        }

        // Take profit price line (Teal Signal dashed)
        if (levels.target) {
            candleSeries.createPriceLine({
                price:     levels.target,
                color:     '#0E9E8A',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'Target',
            });
        }

        // Fit the visible range to include all price lines (entry, stop, target)
        const prices = [levels.entry, levels.stop, levels.target].filter(Boolean);
        if (prices.length > 0) {
            const minPrice = Math.min(...prices, ...candles.map(c => c.low));
            const maxPrice = Math.max(...prices, ...candles.map(c => c.high));
            const padding  = (maxPrice - minPrice) * 0.05;
            chart.priceScale('right').applyOptions({
                autoScale: false,
            });
            candleSeries.applyOptions({
                autoscaleInfoProvider: () => ({
                    priceRange: {
                        minValue: minPrice - padding,
                        maxValue: maxPrice + padding,
                    },
                }),
            });
        }

        chart.timeScale().fitContent();
        _posCharts[symbol] = chart;

        // Resize when container width changes (e.g. window resize)
        const ro = new ResizeObserver(() => {
            chart.applyOptions({ width: container.clientWidth });
        });
        ro.observe(container);

    } catch (err) {
        container.innerHTML = `<div class="text-xs text-error text-center py-6">Chart error: ${err.message}</div>`;
    }
}

// ── Card builder ──────────────────────────────────────────────────────────────

function buildPositionCard(p, orders, expanded) {
    const plClass  = p.unrealized_pl >= 0 ? 'text-success' : 'text-error';
    const plPct    = (p.unrealized_plpc * 100).toFixed(2);
    const plSign   = p.unrealized_pl >= 0 ? '+' : '';
    const chartId  = `pos-chart-${p.symbol}`;

    return `<div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-xl" id="pos-card-${p.symbol}">
        <input type="checkbox" ${expanded ? 'checked' : ''} onchange="onPositionCardToggle('${p.symbol}', this.checked)" />
        <div class="collapse-title flex items-center justify-between pr-10">
            <div class="flex items-center gap-3">
                <span class="font-bold text-base">${p.symbol}</span>
                <span class="text-xs text-base-content/60">${p.qty} shares</span>
                <button id="blacklist-btn-${p.symbol}"
                        class="btn btn-xs btn-ghost"
                        title="Bot active — click to blacklist"
                        onmousedown="event.preventDefault(); event.stopPropagation(); toggleBlacklist('${p.symbol}')">
                    <i class="bi bi-robot"></i>
                </button>
            </div>
            <div class="flex items-center gap-4">
                <div class="text-right hidden sm:block">
                    <div class="text-xs text-base-content/60">Current</div>
                    <div class="text-sm font-mono font-semibold">$${p.current_price.toFixed(2)}</div>
                </div>
                <div class="text-right">
                    <div class="${plClass} font-semibold text-sm">${plSign}$${p.unrealized_pl.toFixed(2)}</div>
                    <div class="${plClass} text-xs">${plSign}${plPct}%</div>
                </div>
            </div>
        </div>
        <div class="collapse-content">
            <!-- Two-column layout: left = chart, right = review -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">

                <!-- LEFT: stats + chart + legend -->
                <div class="flex flex-col gap-3">
                    <!-- Section header — mirrors "Position Review" on the right -->
                    <div class="flex items-center justify-between mb-0">
                        <span class="text-sm font-semibold flex items-center gap-2">
                            <i class="bi bi-bar-chart-line text-primary"></i>
                            Position Summary
                        </span>
                    </div>
                    <!-- Stats grid -->
                    <div class="grid grid-cols-2 gap-2">
                        <div class="bg-base-200 rounded-lg p-2">
                            <div class="text-xs text-base-content/60 mb-0.5">Avg Entry</div>
                            <div class="font-semibold font-mono text-sm">$${p.avg_entry_price.toFixed(2)}</div>
                        </div>
                        <div class="bg-base-200 rounded-lg p-2">
                            <div class="text-xs text-base-content/60 mb-0.5">Current Price</div>
                            <div class="font-semibold font-mono text-sm">$${p.current_price.toFixed(2)}</div>
                        </div>
                        <div class="bg-base-200 rounded-lg p-2">
                            <div class="text-xs text-base-content/60 mb-0.5">Market Value</div>
                            <div class="font-semibold font-mono text-sm">$${p.market_value.toFixed(2)}</div>
                        </div>
                        <div class="bg-base-200 rounded-lg p-2">
                            <div class="text-xs text-base-content/60 mb-0.5">Unrealized P&L</div>
                            <div class="${plClass} font-semibold font-mono text-sm">${plSign}$${p.unrealized_pl.toFixed(2)}</div>
                        </div>
                    </div>

                    <!-- Mini chart -->
                    <div class="bg-base-200 rounded-xl p-2">
                        <div id="${chartId}" style="height: 200px; width: 100%; position: relative;" class="rounded-lg overflow-hidden">
                        </div>
                    </div>

                    <!-- Price level legend -->
                    <div class="flex flex-wrap gap-3 text-xs">
                        <span class="flex items-center gap-1.5">
                            <span class="inline-block w-5 border-t-2 border-dashed border-success"></span>
                            Entry $${p.avg_entry_price.toFixed(2)}
                        </span>
                        ${(() => {
                            const stopOrder = orders.find(o => (o.type === 'stop' || o.type === 'stop_limit') && o.stop_price);
                            return stopOrder ? `<span class="flex items-center gap-1.5">
                                <span class="inline-block w-5 border-t-2 border-dashed border-error"></span>
                                Stop $${stopOrder.stop_price.toFixed(2)}
                               </span>` : '';
                        })()}
                        ${(() => {
                            const targetOrder = orders.find(o => o.type === 'limit' && o.side === 'sell' && o.limit_price);
                            return targetOrder ? `<span class="flex items-center gap-1.5">
                                <span class="inline-block w-5 border-t-2 border-dashed" style="border-color: #0E9E8A;"></span>
                                Target $${targetOrder.limit_price.toFixed(2)}
                               </span>` : '';
                        })()}
                    </div>
                </div>

                <!-- RIGHT: position review (auto-runs on expand) -->
                <div class="flex flex-col">
                    <div class="flex items-center justify-between mb-2">
                        <div class="flex items-center gap-3">
                            <span class="text-sm font-semibold flex items-center gap-2">
                                <i class="bi bi-clipboard2-pulse text-primary"></i>
                                Position Review
                            </span>
                            <label class="flex items-center gap-1.5 cursor-pointer select-none">
                                <input type="checkbox" id="blacklist-toggle-${p.symbol}"
                                       class="toggle toggle-xs toggle-primary"
                                       checked
                                       onchange="toggleBlacklist('${p.symbol}')" />
                                <span id="blacklist-label-${p.symbol}" class="text-xs text-base-content/60">Bot Managed</span>
                            </label>
                        </div>
                        <span id="review-tf-label-${p.symbol}" class="text-xs text-base-content/50 italic"></span>
                    </div>
                    <div id="review-panel-${p.symbol}" class="flex-1">
                        <div class="flex items-center gap-2 text-sm text-base-content/60 py-3">
                            <span class="loading loading-spinner loading-sm"></span>
                            Loading review…
                        </div>
                    </div>
                </div>

            </div>
        </div>
    </div>
</div>`;
}

// ── Trade log timeframe lookup ────────────────────────────────────────────────

// Populated once on page load: symbol → timeframe from data/trades.json
const _tradeTimeframes = {};

async function loadTradeTimeframes() {
    try {
        const log = await (await fetch('/api/trades/log')).json();
        if (!Array.isArray(log)) return;
        // Most-recent entry per symbol wins (trades are appended chronologically)
        for (const t of log) {
            if (t.symbol && t.timeframe) {
                _tradeTimeframes[t.symbol.toUpperCase()] = t.timeframe;
            }
        }
    } catch (_) {
        // Non-fatal — falls back to 'long' default
    }
}

// ── Card toggle handler ───────────────────────────────────────────────────────

// Keyed by symbol: { position, orders } so we can init the chart on first expand
const _positionData = {};

function onPositionCardToggle(symbol, isOpen) {
    if (!isOpen) return;
    const d = _positionData[symbol];
    if (!d) return;
    const levels = extractPriceLevels(d.position.avg_entry_price, d.orders);
    initPositionChart(symbol, `pos-chart-${symbol}`, levels);
}

// ── Main loader ───────────────────────────────────────────────────────────────

async function loadPositions() {
    const container = document.getElementById('positions-container');
    const summaryEl = document.getElementById('positions-summary');
    const urlSymbol = new URLSearchParams(window.location.search).get('symbol');

    container.innerHTML = `<div class="flex justify-center py-12">
        <span class="loading loading-spinner loading-lg text-primary"></span>
    </div>`;

    try {
        const positions = await (await fetch('/api/positions')).json();

        if (!positions || positions.error || positions.length === 0) {
            summaryEl.innerHTML = '';
            container.innerHTML = `<div class="text-center text-base-content/60 py-16">
                <i class="bi bi-bar-chart text-4xl mb-3 block"></i>
                <p class="text-lg font-semibold">No open positions</p>
                <p class="text-sm mt-1">Run the scanner or analyze a symbol to find setups.</p>
            </div>`;
            return;
        }

        // Summary bar
        const totalValue = positions.reduce((s, p) => s + p.market_value, 0);
        const totalPL    = positions.reduce((s, p) => s + p.unrealized_pl, 0);
        const plClass    = totalPL >= 0 ? 'text-success' : 'text-error';
        const plSign     = totalPL >= 0 ? '+' : '';
        summaryEl.innerHTML = `
            <div class="stats stats-horizontal shadow w-full mb-6">
                <div class="stat">
                    <div class="stat-title">Open Positions</div>
                    <div class="stat-value text-2xl">${positions.length}</div>
                </div>
                <div class="stat">
                    <div class="stat-title">Total Value</div>
                    <div class="stat-value text-2xl">$${totalValue.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                </div>
                <div class="stat">
                    <div class="stat-title">Total P&L</div>
                    <div class="stat-value text-2xl ${plClass}">${plSign}$${totalPL.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                </div>
            </div>`;

        // Fetch orders for all positions in parallel
        const ordersBySymbol = {};
        await Promise.all(positions.map(async p => {
            try {
                const orders = await (await fetch(`/api/positions/${encodeURIComponent(p.symbol)}/orders`)).json();
                ordersBySymbol[p.symbol] = orders;
            } catch (_) {
                ordersBySymbol[p.symbol] = [];
            }
        }));

        // Sort: pre-selected symbol first, then alphabetical
        const sorted = [...positions].sort((a, b) => {
            if (urlSymbol) {
                if (a.symbol === urlSymbol.toUpperCase()) return -1;
                if (b.symbol === urlSymbol.toUpperCase()) return 1;
            }
            return a.symbol.localeCompare(b.symbol);
        });

        // Store data for deferred chart init
        sorted.forEach(p => {
            _positionData[p.symbol] = { position: p, orders: ordersBySymbol[p.symbol] || [] };
        });

        container.innerHTML = sorted.map(p =>
            buildPositionCard(p, ordersBySymbol[p.symbol] || [], p.symbol === (urlSymbol || '').toUpperCase())
        ).join('');

        // Render blacklist state for all cards now that they're in the DOM
        sorted.forEach(p => {
            const isBlacklisted = _blacklist.has(p.symbol.toUpperCase());
            _updateBlacklistBtn(p.symbol, isBlacklisted);
            _updateBlacklistToggle(p.symbol, isBlacklisted);
        });

        // Set the entry timeframe label for each card
        const TF_LABELS = { long: 'Daily', swing: 'Hourly', short: '15-min' };
        sorted.forEach(p => {
            const loggedTf = _tradeTimeframes[p.symbol.toUpperCase()] || 'long';
            const labelEl  = document.getElementById(`review-tf-label-${p.symbol}`);
            if (labelEl) {
                labelEl.textContent = `${TF_LABELS[loggedTf] || loggedTf} timeframe`;
            }
            // Store the timeframe on the element so runPositionReview can read it
            const btn = labelEl ? labelEl.closest('.flex') : null;
            if (btn) btn.dataset.timeframe = loggedTf;
        });

        // Init charts and auto-run reviews for any cards that start expanded
        sorted.forEach(p => {
            const isExpanded = p.symbol === (urlSymbol || '').toUpperCase();
            if (isExpanded) {
                const levels = extractPriceLevels(p.avg_entry_price, ordersBySymbol[p.symbol] || []);
                initPositionChart(p.symbol, `pos-chart-${p.symbol}`, levels);
                runPositionReview(p.symbol);
            }
        });

        // Auto-run review for all cards (they all start collapsed except the URL one,
        // but we still want the review ready when the user opens them)
        sorted.forEach(p => {
            const isExpanded = p.symbol === (urlSymbol || '').toUpperCase();
            if (!isExpanded) {
                runPositionReview(p.symbol);
            }
        });

    } catch (err) {
        container.innerHTML = `<div class="alert alert-error"><span>Failed to load positions: ${err.message}</span></div>`;
    }
}

// ── Position Review ───────────────────────────────────────────────────────────

const VERDICT_CONFIG = {
    HOLD:           { badge: 'badge-success',  icon: 'bi-check-circle-fill',   label: 'HOLD',           color: '#0D9B55' },
    TRAIL_STOP:     { badge: 'badge-warning',  icon: 'bi-arrow-up-circle-fill', label: 'TRAIL STOP',     color: '#C47D0A' },
    RAISE_TARGET:   { badge: 'badge-info',     icon: 'bi-graph-up-arrow',       label: 'RAISE TARGET',   color: '#0E9E8A' },
    PARTIAL_PROFIT: { badge: 'badge-warning',  icon: 'bi-pie-chart-fill',       label: 'PARTIAL PROFIT', color: '#C47D0A' },
    EXIT:           { badge: 'badge-error',    icon: 'bi-x-circle-fill',        label: 'EXIT',           color: '#D63B3B' },
};

async function runPositionReview(symbol) {
    const panel   = document.getElementById(`review-panel-${symbol}`);
    const labelEl = document.getElementById(`review-tf-label-${symbol}`);
    if (!panel) return;

    // Timeframe is stored on the parent flex container by loadPositions()
    const tfContainer = labelEl ? labelEl.closest('.flex') : null;
    const timeframe   = (tfContainer && tfContainer.dataset.timeframe) || _tradeTimeframes[symbol.toUpperCase()] || 'long';

    panel.innerHTML = `<div class="flex items-center gap-2 text-sm text-base-content/60 py-3">
        <span class="loading loading-spinner loading-sm"></span>
        Running position review (${timeframe})…
    </div>`;

    try {
        const res  = await fetch(`/api/positions/${encodeURIComponent(symbol)}/review?timeframe=${timeframe}`);
        const data = await res.json();

        if (data.error) {
            panel.innerHTML = `<div class="alert alert-error text-sm"><span>${data.error}</span></div>`;
            return;
        }

        panel.innerHTML = buildReviewPanel(symbol, data);

    } catch (err) {
        panel.innerHTML = `<div class="alert alert-error text-sm"><span>Review failed: ${err.message}</span></div>`;
    }
}

function buildReviewPanel(symbol, r) {
    const cfg     = VERDICT_CONFIG[r.verdict] || VERDICT_CONFIG.HOLD;
    const m       = r.momentum || {};
    const hasSugg = r.suggested_stop !== null || r.suggested_target !== null;

    // Momentum health grid
    const rsiClass   = m.rsi_trend === 'RISING' ? 'text-success' : m.rsi_trend === 'FALLING' ? 'text-error' : 'text-base-content/60';
    const slopeClass = m.ema9_slope_trend === 'RISING' ? 'text-success' : m.ema9_slope_trend === 'FALLING' ? 'text-error' : 'text-base-content/60';
    const rvolClass  = m.rvol_status === 'ELEVATED' ? 'text-success' : m.rvol_status === 'LOW' ? 'text-error' : 'text-base-content/60';
    const extClass   = m.extension_status === 'EXTENDED' ? 'text-error' : 'text-success';
    const ema9Class  = m.price_vs_ema9 === 'ABOVE' ? 'text-success' : 'text-error';
    const regClass   = m.regime === 'BULLISH' ? 'text-success' : m.regime === 'BEARISH' ? 'text-error' : 'text-warning';

    // Suggested adjustment inputs (pre-filled with suggested values)
    const stopVal   = r.suggested_stop   !== null ? r.suggested_stop   : (r.current_stop   || '');
    const targetVal = r.suggested_target !== null ? r.suggested_target : (r.current_target || '');

    const adjustSection = `
        <div class="bg-base-200 rounded-lg p-3 mt-3">
            <div class="text-xs font-semibold text-base-content/70 mb-2">Adjust Orders</div>
            <div class="grid grid-cols-2 gap-2 mb-2">
                <div>
                    <label class="text-xs text-base-content/60 block mb-1">Stop Price</label>
                    <input type="number" id="adj-stop-${symbol}" step="0.01"
                           value="${stopVal}"
                           class="input input-xs input-bordered w-full font-mono"
                           placeholder="Stop price" />
                </div>
                <div>
                    <label class="text-xs text-base-content/60 block mb-1">Target Price</label>
                    <input type="number" id="adj-target-${symbol}" step="0.01"
                           value="${targetVal}"
                           class="input input-xs input-bordered w-full font-mono"
                           placeholder="Target price" />
                </div>
            </div>
            <div class="flex gap-2">
                <button class="btn btn-xs btn-primary flex-1 gap-1" onclick="applyOrderAdjustment('${symbol}')">
                    <i class="bi bi-pencil-fill"></i> Apply Adjustment
                </button>
                <button class="btn btn-xs btn-error gap-1" onclick="confirmClosePosition('${symbol}')">
                    <i class="bi bi-x-lg"></i> Close Position
                </button>
            </div>
            <div id="adj-result-${symbol}" class="mt-2"></div>
        </div>`;

    return `
        <div class="space-y-3">
            <!-- Verdict banner -->
            <div class="flex items-start gap-3 p-3 rounded-lg border"
                 style="border-color: ${cfg.color}20; background: ${cfg.color}10;">
                <i class="bi ${cfg.icon} text-xl mt-0.5" style="color: ${cfg.color};"></i>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 mb-1">
                        <span class="badge ${cfg.badge} badge-sm font-bold">${cfg.label}</span>
                        <span class="text-xs text-base-content/50">${symbol}</span>
                    </div>
                    <p class="text-sm text-base-content/80 leading-snug">${r.reason}</p>
                </div>
            </div>

            <!-- Momentum health grid -->
            <div class="grid grid-cols-3 sm:grid-cols-6 gap-2">
                <div class="bg-base-200 rounded-lg p-2 text-center">
                    <div class="text-xs text-base-content/50 mb-0.5">Regime</div>
                    <div class="text-xs font-bold ${regClass}">${m.regime || '—'}</div>
                </div>
                <div class="bg-base-200 rounded-lg p-2 text-center">
                    <div class="text-xs text-base-content/50 mb-0.5">RSI</div>
                    <div class="text-xs font-bold ${rsiClass}">${m.rsi !== null ? m.rsi : '—'} <span class="font-normal">(${m.rsi_trend || '—'})</span></div>
                </div>
                <div class="bg-base-200 rounded-lg p-2 text-center">
                    <div class="text-xs text-base-content/50 mb-0.5">EMA9 Slope</div>
                    <div class="text-xs font-bold ${slopeClass}">${m.ema9_slope !== null ? m.ema9_slope + '%' : '—'}</div>
                </div>
                <div class="bg-base-200 rounded-lg p-2 text-center">
                    <div class="text-xs text-base-content/50 mb-0.5">RVOL</div>
                    <div class="text-xs font-bold ${rvolClass}">${m.rvol !== null ? m.rvol + 'x' : '—'}</div>
                </div>
                <div class="bg-base-200 rounded-lg p-2 text-center">
                    <div class="text-xs text-base-content/50 mb-0.5">Extension</div>
                    <div class="text-xs font-bold ${extClass}">${m.extension !== null ? m.extension + 'x ATR' : '—'}</div>
                </div>
                <div class="bg-base-200 rounded-lg p-2 text-center">
                    <div class="text-xs text-base-content/50 mb-0.5">vs EMA9</div>
                    <div class="text-xs font-bold ${ema9Class}">${m.price_vs_ema9 || '—'}</div>
                </div>
            </div>

            ${r.suggested_stop !== null || r.suggested_target !== null ? `
            <!-- Suggested levels -->
            <div class="flex flex-wrap gap-3 text-xs">
                ${r.suggested_stop !== null ? `<span class="flex items-center gap-1.5 bg-warning/10 text-warning px-2 py-1 rounded-lg">
                    <i class="bi bi-arrow-up-circle"></i>
                    Suggested Stop: <strong>$${r.suggested_stop.toFixed(2)}</strong>
                    ${r.current_stop ? `<span class="opacity-60">(was $${r.current_stop.toFixed(2)})</span>` : ''}
                </span>` : ''}
                ${r.suggested_target !== null ? `<span class="flex items-center gap-1.5 bg-info/10 text-info px-2 py-1 rounded-lg">
                    <i class="bi bi-graph-up-arrow"></i>
                    Suggested Target: <strong>$${r.suggested_target.toFixed(2)}</strong>
                    ${r.current_target ? `<span class="opacity-60">(was $${r.current_target.toFixed(2)})</span>` : ''}
                </span>` : ''}
            </div>` : ''}

            ${adjustSection}
        </div>`;
}

async function applyOrderAdjustment(symbol) {
    const stopInput   = document.getElementById(`adj-stop-${symbol}`);
    const targetInput = document.getElementById(`adj-target-${symbol}`);
    const resultEl    = document.getElementById(`adj-result-${symbol}`);

    const body = {};
    if (stopInput   && stopInput.value.trim())   body.stop_price   = parseFloat(stopInput.value);
    if (targetInput && targetInput.value.trim()) body.target_price = parseFloat(targetInput.value);

    if (!body.stop_price && !body.target_price) {
        resultEl.innerHTML = `<div class="alert alert-warning text-xs py-1"><span>Enter at least one price to adjust.</span></div>`;
        return;
    }

    resultEl.innerHTML = `<div class="flex items-center gap-2 text-xs text-base-content/60">
        <span class="loading loading-spinner loading-xs"></span> Updating orders…
    </div>`;

    try {
        const res  = await fetch(`/api/positions/${encodeURIComponent(symbol)}/adjust`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(body),
        });
        const data = await res.json();

        if (data.error || data.status === 'error') {
            const msg = data.error || (data.errors || []).join('; ') || 'Unknown error';
            resultEl.innerHTML = `<div class="alert alert-error text-xs py-1"><span>${msg}</span></div>`;
            return;
        }

        const changed = (data.changed || []).join('<br>');
        resultEl.innerHTML = `<div class="alert alert-success text-xs py-1">
            <i class="bi bi-check-circle-fill"></i>
            <span>${changed || 'Orders updated'}</span>
        </div>`;

        // Refresh the position card after a short delay so the new levels show
        setTimeout(() => loadPositions(), 1500);

    } catch (err) {
        resultEl.innerHTML = `<div class="alert alert-error text-xs py-1"><span>${err.message}</span></div>`;
    }
}

async function confirmClosePosition(symbol) {
    const resultEl = document.getElementById(`adj-result-${symbol}`);

    // Simple inline confirmation — avoids a modal dependency
    resultEl.innerHTML = `
        <div class="alert alert-warning text-xs py-2">
            <i class="bi bi-exclamation-triangle-fill"></i>
            <span>Close <strong>${symbol}</strong> at market? This cannot be undone.</span>
            <div class="flex gap-2 mt-1">
                <button class="btn btn-xs btn-error" onclick="executeClosePosition('${symbol}')">Yes, Close</button>
                <button class="btn btn-xs btn-ghost" onclick="document.getElementById('adj-result-${symbol}').innerHTML=''">Cancel</button>
            </div>
        </div>`;
}

async function executeClosePosition(symbol) {
    const resultEl = document.getElementById(`adj-result-${symbol}`);

    resultEl.innerHTML = `<div class="flex items-center gap-2 text-xs text-base-content/60">
        <span class="loading loading-spinner loading-xs"></span> Closing position…
    </div>`;

    try {
        const res  = await fetch(`/api/positions/${encodeURIComponent(symbol)}/close`, { method: 'POST' });
        const data = await res.json();

        if (data.error || data.status === 'error') {
            resultEl.innerHTML = `<div class="alert alert-error text-xs py-1"><span>${data.error || data.message}</span></div>`;
            return;
        }

        resultEl.innerHTML = `<div class="alert alert-success text-xs py-1">
            <i class="bi bi-check-circle-fill"></i>
            <span>${data.message || 'Position closed'}</span>
        </div>`;

        // Reload positions after close
        setTimeout(() => loadPositions(), 2000);

    } catch (err) {
        resultEl.innerHTML = `<div class="alert alert-error text-xs py-1"><span>${err.message}</span></div>`;
    }
}

// ── Trade History ─────────────────────────────────────────────────────────────

const TF_LABELS_HIST = { long: 'Daily', swing: 'Hourly', short: '15-min' };

async function loadTradeHistory() {
    const container = document.getElementById('trade-history-container');
    if (!container) return;

    try {
        const log = await (await fetch('/api/trades/log')).json();
        if (!Array.isArray(log) || log.length === 0) {
            container.innerHTML = `<div class="text-center text-base-content/60 py-8">
                <i class="bi bi-journal-x text-3xl mb-2 block"></i>
                <p class="text-sm">No trade history yet. Trades will appear here after you execute your first order.</p>
            </div>`;
            return;
        }

        // Pair entry and exit events by symbol (most-recent first)
        const entries = log.filter(t => !t.event || t.event === 'entry');
        const exits   = log.filter(t => t.event === 'exit');

        // Build a lookup: symbol → list of exit events (chronological)
        const exitsBySymbol = {};
        for (const ex of exits) {
            const sym = (ex.symbol || '').toUpperCase();
            if (!exitsBySymbol[sym]) exitsBySymbol[sym] = [];
            exitsBySymbol[sym].push(ex);
        }

        // Render entries newest-first, pairing with the closest exit if available
        const rows = [...entries].reverse().map(entry => {
            const sym     = (entry.symbol || '').toUpperCase();
            const exits   = exitsBySymbol[sym] || [];
            // Match the first exit that happened after this entry
            const exit    = exits.find(ex => ex.timestamp > entry.timestamp) || null;

            const tf      = entry.timeframe || 'long';
            const tfLabel = TF_LABELS_HIST[tf] || tf;
            const date    = entry.timestamp ? new Date(entry.timestamp).toLocaleDateString() : '—';
            const side    = (entry.side || 'buy').toUpperCase();
            const sideClass = side === 'BUY' ? 'badge-success' : 'badge-error';

            const entryPx  = entry.entry_price != null ? `$${Number(entry.entry_price).toFixed(2)}` : '—';
            const stopPx   = entry.stop_price   != null ? `$${Number(entry.stop_price).toFixed(2)}`  : '—';
            const targetPx = entry.target_price != null ? `$${Number(entry.target_price).toFixed(2)}` : '—';
            const qty      = entry.quantity != null ? entry.quantity : '—';

            let statusBadge, exitPx, plCell;
            if (exit) {
                const exitPrice = exit.exit_price != null ? Number(exit.exit_price) : null;
                const entryPrice = entry.entry_price != null ? Number(entry.entry_price) : null;
                exitPx = exitPrice != null ? `$${exitPrice.toFixed(2)}` : '—';

                const pl = exit.unrealized_pl != null
                    ? Number(exit.unrealized_pl)
                    : (exitPrice != null && entryPrice != null && qty !== '—'
                        ? (exitPrice - entryPrice) * Number(qty) : null);
                const plClass = pl != null ? (pl >= 0 ? 'text-success' : 'text-error') : '';
                const plSign  = pl != null && pl >= 0 ? '+' : '';
                plCell = pl != null
                    ? `<span class="${plClass} font-semibold">${plSign}$${Math.abs(pl).toFixed(2)}</span>`
                    : '—';

                const reasonLabel = { manual: 'Manual', stop: 'Stop Hit', target: 'Target Hit' };
                const reasonText  = reasonLabel[exit.exit_reason] || exit.exit_reason || 'Closed';
                statusBadge = `<span class="badge badge-ghost badge-sm">${reasonText}</span>`;
            } else {
                exitPx      = '—';
                plCell      = '—';
                statusBadge = `<span class="badge badge-primary badge-sm">Open</span>`;
            }

            return `<tr class="hover">
                <td class="font-bold">${sym}</td>
                <td><span class="badge ${sideClass} badge-sm">${side}</span></td>
                <td class="text-xs text-base-content/60">${tfLabel}</td>
                <td class="font-mono text-xs">${date}</td>
                <td class="font-mono text-xs">${qty}</td>
                <td class="font-mono text-xs">${entryPx}</td>
                <td class="font-mono text-xs">${stopPx}</td>
                <td class="font-mono text-xs">${targetPx}</td>
                <td class="font-mono text-xs">${exitPx}</td>
                <td class="font-mono text-xs">${plCell}</td>
                <td>${statusBadge}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `
            <div class="overflow-x-auto rounded-xl border border-base-300">
                <table class="table table-sm w-full">
                    <thead>
                        <tr class="text-xs text-base-content/60 uppercase">
                            <th>Symbol</th>
                            <th>Side</th>
                            <th>Timeframe</th>
                            <th>Date</th>
                            <th>Qty</th>
                            <th>Entry</th>
                            <th>Stop</th>
                            <th>Target</th>
                            <th>Exit</th>
                            <th>P&L</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;

    } catch (err) {
        container.innerHTML = `<div class="alert alert-error text-sm"><span>Failed to load trade history: ${err.message}</span></div>`;
    }
}

window.addEventListener('load', async () => {
    await Promise.all([loadTradeTimeframes(), loadBlacklist()]);
    loadPositions();
    loadTradeHistory();
});
