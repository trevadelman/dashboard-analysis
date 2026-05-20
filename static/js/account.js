/**
 * account.js — Account summary, positions, and trade history
 */

async function updateAccount() {
    try {
        const data = await (await fetch('/api/account')).json();
        if (data && !data.error && data.equity != null) {
            document.getElementById('equity').textContent       = `$${data.equity.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
            document.getElementById('cash').textContent         = `$${data.cash.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
            document.getElementById('buying_power').textContent = `$${data.buying_power.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
        }
        // If no credentials, values stay at $0.00 — no crash
    } catch (_) {}
}

async function updatePositions() {
    const positions = await (await fetch('/api/positions')).json();
    const container = document.getElementById('positions-list');
    document.getElementById('positions-count').textContent = positions.length || 0;

    if (positions && !positions.error && positions.length > 0) {
        container.innerHTML = positions.map(p => {
            const plClass = p.unrealized_pl >= 0 ? 'text-success' : 'text-error';
            const plPct   = (p.unrealized_plpc * 100).toFixed(2);
            const sym     = p.symbol;
            return `<a href="/positions?symbol=${sym}" class="block card bg-base-200 mb-2 hover:bg-base-300 transition-colors">
                <div class="card-body p-3">
                    <div class="flex justify-between items-center">
                        <strong class="text-sm">${sym}</strong>
                        <span class="${plClass} text-sm font-semibold">${p.unrealized_pl >= 0 ? '+' : ''}$${p.unrealized_pl.toFixed(2)} (${plPct}%)</span>
                    </div>
                    <div class="text-xs text-base-content/60 mt-1">${p.qty} shares · Entry $${p.avg_entry_price.toFixed(2)} · Now $${p.current_price.toFixed(2)}</div>
                    <div class="text-xs text-base-content/60">Value: $${p.market_value.toFixed(2)}</div>
                </div>
            </a>`;
        }).join('');
    } else {
        container.innerHTML = '<div class="text-center text-base-content/60 text-sm py-3">No open positions</div>';
    }
}

const STATUS_COLORS = {
    filled: 'text-success', accepted: 'text-info', new: 'text-info',
    canceled: 'text-base-content/60', cancelled: 'text-base-content/60', expired: 'text-base-content/60',
    pending_new: 'text-warning', held: 'text-warning', partially_filled: 'text-warning'
};

async function updateTrades() {
    const trades    = await (await fetch('/api/trades?limit=20')).json();
    const container = document.getElementById('trades-list');
    if (!container) return;
    if (trades && !trades.error && trades.length > 0) {
        container.innerHTML = trades.map(t => {
            const time        = t.timestamp ? new Date(t.timestamp).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '—';
            const sideClass   = t.side === 'buy' ? 'text-success' : 'text-error';
            const statusClass = STATUS_COLORS[t.status] || 'text-base-content/60';
            return `<div class="flex justify-between items-center py-2 border-b border-base-300 last:border-0">
                <div>
                    <span class="font-semibold text-sm">${t.symbol}</span>
                    <span class="${sideClass} text-sm ml-1">${(t.side || '').toUpperCase()}</span>
                    <span class="text-base-content/60 text-sm ml-1">×${t.quantity}</span>
                </div>
                <div class="text-right">
                    <div class="text-xs text-base-content/60">${time}</div>
                    <div class="text-xs font-semibold ${statusClass}">${t.status}</div>
                </div>
            </div>`;
        }).join('');
    } else {
        container.innerHTML = '<div class="text-center text-base-content/60 text-sm py-3">No order history</div>';
    }
}

async function executeTrade(sig, tif) {
    const side     = sig.side === 'buy' ? 'buy' : 'sell';
    const tifLabel = tif === 'gtc' ? 'Good Till Cancelled' : 'Day (expires today)';
    const confirmed = confirm(
        `Execute ${side.toUpperCase()} order for ${sig.symbol}?\n\n` +
        `Entry: $${sig.entry_price}\nStop Loss: $${sig.stop_price}\nTake Profit: $${sig.target_price}\n` +
        `Duration: ${tifLabel}\n\nThis will submit a bracket order to Alpaca.`
    );
    if (!confirmed) return;

    try {
        const response = await fetch('/api/execute_trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: sig.symbol, side: sig.side,
                entry_price: sig.entry_price, stop_price: sig.stop_price,
                target_price: sig.target_price, time_in_force: tif || 'gtc'
            })
        });
        const result = await response.json();
        if (result.status === 'executed') {
            alert(`✅ Order submitted!\n\nSymbol: ${result.trade.symbol}\nQty: ${result.trade.quantity}\nOrder ID: ${result.trade.order_id}`);
            updatePositions();
            updateTrades();
        } else if (result.status === 'skipped') {
            alert(`⚠️ Trade skipped: ${result.reason}`);
        } else {
            alert(`❌ Error: ${result.error || 'Unknown error'}`);
        }
    } catch (err) {
        alert(`❌ Request failed: ${err.message}`);
    }
}
