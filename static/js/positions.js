/**
 * positions.js — Positions page: summary bar + accordion cards with mini charts
 */

// Track LightweightCharts instances so we can destroy them when cards collapse
const _posCharts = {};

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
            height:          280,
            layout:          { backgroundColor: 'transparent', textColor: '#6b7280' },
            grid:            { vertLines: { color: '#e5e7eb' }, horzLines: { color: '#e5e7eb' } },
            crosshair:       { mode: LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: { borderColor: '#d1d5db' },
            timeScale:       { borderColor: '#d1d5db', timeVisible: true },
            handleScroll:    true,
            handleScale:     true,
        });

        const candleSeries = chart.addCandlestickSeries({
            upColor: '#26a69a', downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        });

        const toSec = d => typeof d.timestamp === 'number'
            ? d.timestamp / 1000
            : new Date(d.timestamp).getTime() / 1000;

        const candles = data
            .map(d => ({ time: toSec(d), open: d.open, high: d.high, low: d.low, close: d.close }))
            .sort((a, b) => a.time - b.time);

        candleSeries.setData(candles);

        // Entry price line (green dashed)
        if (levels.entry) {
            candleSeries.createPriceLine({
                price:     levels.entry,
                color:     '#22c55e',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'Entry',
            });
        }

        // Stop loss price line (red dashed)
        if (levels.stop) {
            candleSeries.createPriceLine({
                price:     levels.stop,
                color:     '#ef4444',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: 'Stop',
            });
        }

        // Take profit price line (teal dashed)
        if (levels.target) {
            candleSeries.createPriceLine({
                price:     levels.target,
                color:     '#14b8a6',
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
            <!-- Stats grid -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                <div class="bg-base-200 rounded-lg p-3">
                    <div class="text-xs text-base-content/60 mb-1">Avg Entry</div>
                    <div class="font-semibold font-mono">$${p.avg_entry_price.toFixed(2)}</div>
                </div>
                <div class="bg-base-200 rounded-lg p-3">
                    <div class="text-xs text-base-content/60 mb-1">Current Price</div>
                    <div class="font-semibold font-mono">$${p.current_price.toFixed(2)}</div>
                </div>
                <div class="bg-base-200 rounded-lg p-3">
                    <div class="text-xs text-base-content/60 mb-1">Market Value</div>
                    <div class="font-semibold font-mono">$${p.market_value.toFixed(2)}</div>
                </div>
                <div class="bg-base-200 rounded-lg p-3">
                    <div class="text-xs text-base-content/60 mb-1">Unrealized P&L</div>
                    <div class="${plClass} font-semibold font-mono">${plSign}$${p.unrealized_pl.toFixed(2)}</div>
                </div>
            </div>

            <!-- Mini chart -->
            <div class="bg-base-200 rounded-xl p-3 mb-4">
                <div id="${chartId}" style="height: 280px; width: 100%; position: relative;" class="rounded-lg overflow-hidden">
                </div>
            </div>

            <!-- Price level legend -->
            <div class="flex flex-wrap gap-4 text-xs mb-4">
                <span class="flex items-center gap-1.5">
                    <span class="inline-block w-6 border-t-2 border-dashed border-success"></span>
                    Entry $${p.avg_entry_price.toFixed(2)}
                </span>
                ${(() => {
                    const stopOrder = orders.find(o => (o.type === 'stop' || o.type === 'stop_limit') && o.stop_price);
                    return stopOrder ? `<span class="flex items-center gap-1.5">
                        <span class="inline-block w-6 border-t-2 border-dashed border-error"></span>
                        Stop $${stopOrder.stop_price.toFixed(2)}
                       </span>` : '';
                })()}
                ${(() => {
                    const targetOrder = orders.find(o => o.type === 'limit' && o.side === 'sell' && o.limit_price);
                    return targetOrder ? `<span class="flex items-center gap-1.5">
                        <span class="inline-block w-6 border-t-2 border-dashed" style="border-color: #14b8a6;"></span>
                        Target $${targetOrder.limit_price.toFixed(2)}
                       </span>` : '';
                })()}
            </div>

        </div>
    </div>`;
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

        // Init charts for any cards that start expanded
        sorted.forEach(p => {
            const isExpanded = p.symbol === (urlSymbol || '').toUpperCase();
            if (isExpanded) {
                const levels = extractPriceLevels(p.avg_entry_price, ordersBySymbol[p.symbol] || []);
                initPositionChart(p.symbol, `pos-chart-${p.symbol}`, levels);
            }
        });

    } catch (err) {
        container.innerHTML = `<div class="alert alert-error"><span>Failed to load positions: ${err.message}</span></div>`;
    }
}

window.addEventListener('load', loadPositions);
