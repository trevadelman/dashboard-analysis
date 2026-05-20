/**
 * positions.js — Positions page: summary bar + accordion cards
 */

const STATUS_COLORS_POS = {
    filled: 'text-success', accepted: 'text-info', new: 'text-info',
    canceled: 'text-base-content/60', cancelled: 'text-base-content/60', expired: 'text-base-content/60',
    pending_new: 'text-warning', held: 'text-warning', partially_filled: 'text-warning'
};

function buildLegRow(leg) {
    if (leg.type === 'limit') {
        return `<div class="flex justify-between text-xs py-1 border-b border-base-300 last:border-0">
            <span class="text-success font-medium">Take Profit</span>
            <span class="font-mono">$${leg.limit_price?.toFixed(2) ?? '—'}</span>
            <span class="${STATUS_COLORS_POS[leg.status] || 'text-base-content/60'}">${leg.status}</span>
        </div>`;
    }
    if (leg.type === 'stop' || leg.type === 'stop_limit') {
        return `<div class="flex justify-between text-xs py-1 border-b border-base-300 last:border-0">
            <span class="text-error font-medium">Stop Loss</span>
            <span class="font-mono">$${leg.stop_price?.toFixed(2) ?? '—'}</span>
            <span class="${STATUS_COLORS_POS[leg.status] || 'text-base-content/60'}">${leg.status}</span>
        </div>`;
    }
    return `<div class="flex justify-between text-xs py-1 border-b border-base-300 last:border-0">
        <span class="text-base-content/60">${leg.type}</span>
        <span>—</span>
        <span class="${STATUS_COLORS_POS[leg.status] || 'text-base-content/60'}">${leg.status}</span>
    </div>`;
}

function buildOrderBlock(order) {
    const typeLabel   = order.order_class === 'bracket' ? 'Bracket' : order.type;
    const priceLabel  = order.limit_price ? `Limit $${order.limit_price}` : order.stop_price ? `Stop $${order.stop_price}` : 'Market';
    const statusClass = STATUS_COLORS_POS[order.status] || 'text-base-content/60';
    const created     = order.created_at ? new Date(order.created_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '—';
    const legs        = (order.legs || []).map(buildLegRow).join('');

    return `<div class="bg-base-200 rounded-lg p-3 mb-2">
        <div class="flex justify-between items-start mb-1">
            <div>
                <span class="text-xs font-semibold">${typeLabel}</span>
                <span class="text-xs text-base-content/60 ml-2">${priceLabel}</span>
                <span class="text-xs text-base-content/60 ml-2">×${order.qty}</span>
            </div>
            <span class="text-xs font-semibold ${statusClass}">${order.status}</span>
        </div>
        <div class="text-xs text-base-content/40 mb-2">${created}</div>
        ${legs ? `<div class="mt-1">${legs}</div>` : ''}
    </div>`;
}

function buildPositionCard(p, orders, expanded) {
    const plClass  = p.unrealized_pl >= 0 ? 'text-success' : 'text-error';
    const plPct    = (p.unrealized_plpc * 100).toFixed(2);
    const plSign   = p.unrealized_pl >= 0 ? '+' : '';
    const ordersHtml = orders.length > 0
        ? orders.map(buildOrderBlock).join('')
        : '<div class="text-xs text-base-content/60 py-2">No open orders for this position.</div>';

    return `<div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-xl" id="pos-card-${p.symbol}">
        <input type="checkbox" ${expanded ? 'checked' : ''} />
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

            <!-- Open orders / bracket legs -->
            <div class="mb-4">
                <h4 class="text-xs font-semibold text-base-content/60 uppercase mb-2">Open Orders</h4>
                ${ordersHtml}
            </div>

            <!-- Actions -->
            <div class="flex gap-2">
                <a href="/?symbol=${encodeURIComponent(p.symbol)}" class="btn btn-primary btn-sm gap-2">
                    <i class="bi bi-graph-up"></i> Analyze
                </a>
            </div>
        </div>
    </div>`;
}

async function loadPositions() {
    const container  = document.getElementById('positions-container');
    const summaryEl  = document.getElementById('positions-summary');
    const urlSymbol  = new URLSearchParams(window.location.search).get('symbol');

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

        container.innerHTML = sorted.map(p =>
            buildPositionCard(p, ordersBySymbol[p.symbol] || [], p.symbol === (urlSymbol || '').toUpperCase())
        ).join('');

    } catch (err) {
        container.innerHTML = `<div class="alert alert-error"><span>Failed to load positions: ${err.message}</span></div>`;
    }
}

window.addEventListener('load', loadPositions);
