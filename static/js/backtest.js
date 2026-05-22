/**
 * backtest.js — Backtest page: run, render summary, equity curve, trades table
 */

let _btChart = null;
let _btResult = null;

// ── Run ───────────────────────────────────────────────────────────────────────

async function runBacktest() {
    const symbol             = document.getElementById('bt-symbol').value.trim().toUpperCase();
    const timeframe          = document.getElementById('bt-timeframe').value;
    const period             = document.getElementById('bt-period').value;
    const usePositionReview  = document.getElementById('bt-use-review').checked;

    if (!symbol) return;

    setLoading(true);
    hideError();
    hideResults();

    try {
        const res  = await fetch('/api/backtest', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ symbol, timeframe, period, use_position_review: usePositionReview }),
        });
        const data = await res.json();

        if (data.error) {
            showError(data.error);
            return;
        }

        _btResult = data;
        renderResults(data);

    } catch (err) {
        showError(`Request failed: ${err.message}`);
    } finally {
        setLoading(false);
    }
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderResults(data) {
    renderSummary(data);
    renderEquityCurve(data);
    renderTradesTable(data.trades || []);
    showResults();
}

function renderSummary(data) {
    const s = data.summary;
    const winPct  = (s.win_rate  * 100).toFixed(1);
    const lossPct = (s.loss_rate * 100).toFixed(1);
    const expSign = s.expectancy >= 0 ? '+' : '';
    const totalRSign = s.total_r >= 0 ? '+' : '';
    const expClass   = s.expectancy >= 0 ? 'text-success' : 'text-error';
    const totalRClass = s.total_r >= 0 ? 'text-success' : 'text-error';

    document.getElementById('bt-summary-bar').innerHTML = `
        <div class="stats stats-horizontal shadow w-full flex-wrap mb-2">
            <div class="stat">
                <div class="stat-title">Symbol</div>
                <div class="stat-value text-xl">${data.symbol}</div>
                <div class="stat-desc">${data.period} · ${data.timeframe} · ${data.total_bars} bars</div>
            </div>
            <div class="stat">
                <div class="stat-title">Trades</div>
                <div class="stat-value text-xl">${s.total}</div>
                <div class="stat-desc">${s.wins}W / ${s.losses}L / ${s.timeouts} open${data.use_position_review ? ' · <span class="text-info">+reviewer</span>' : ''}</div>
            </div>
            <div class="stat">
                <div class="stat-title">Win Rate</div>
                <div class="stat-value text-xl">${winPct}%</div>
                <div class="stat-desc">Avg win ${s.avg_win_r}R · Avg loss ${s.avg_loss_r}R</div>
            </div>
            <div class="stat">
                <div class="stat-title">Expectancy</div>
                <div class="stat-value text-xl ${expClass}">${expSign}${s.expectancy}R</div>
                <div class="stat-desc">per trade</div>
            </div>
            <div class="stat">
                <div class="stat-title">Total R</div>
                <div class="stat-value text-xl ${totalRClass}">${totalRSign}${s.total_r}R</div>
                <div class="stat-desc">Max DD ${s.max_drawdown_r}R</div>
            </div>
        </div>`;
}

function renderEquityCurve(data) {
    const container = document.getElementById('bt-equity-chart');
    if (!container) return;

    // Destroy previous chart instance and clear any placeholder text
    if (_btChart) {
        _btChart.remove();
        _btChart = null;
    }
    container.innerHTML = '';

    const curve  = data.summary.r_curve || [];
    const trades = data.trades || [];

    if (curve.length === 0) {
        container.innerHTML = '<div class="text-xs text-base-content/40 text-center py-8">No trades to chart</div>';
        return;
    }

    // Build x-axis time values from trade entry dates (Unix seconds).
    // LightweightCharts requires strictly increasing time values.
    // If two trades share the same second (unlikely on daily, possible on hourly),
    // we nudge each subsequent duplicate by +1s to satisfy the constraint.
    const lineData = [];
    let lastTs = 0;
    curve.forEach((r, idx) => {
        const trade = trades[idx];
        let ts = trade && trade.date
            ? Math.floor(new Date(trade.date).getTime() / 1000)
            : lastTs + 86400;
        if (ts <= lastTs) ts = lastTs + 1;
        lastTs = ts;
        lineData.push({ time: ts, value: r });
    });

    _btChart = LightweightCharts.createChart(container, {
        width:           container.clientWidth,
        height:          240,
        layout:          { background: { type: 'solid', color: 'transparent' }, textColor: '#6b7280' },
        grid:            { vertLines: { color: '#e5e7eb' }, horzLines: { color: '#e5e7eb' } },
        crosshair:       { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#d1d5db' },
        timeScale:       { borderColor: '#d1d5db', timeVisible: true },
        handleScroll:    true,
        handleScale:     true,
    });

    const lineSeries = _btChart.addLineSeries({
        color:     '#2962ff',
        lineWidth: 2,
        title:     'Cumulative R',
        lastValueVisible: true,
        priceLineVisible: false,
    });

    lineSeries.setData(lineData);

    // Zero line
    lineSeries.createPriceLine({
        price:     0,
        color:     '#9ca3af',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: false,
    });

    // fitContent must run after the chart has been painted — defer one frame
    requestAnimationFrame(() => {
        if (_btChart) _btChart.timeScale().fitContent();
    });

    const ro = new ResizeObserver(() => {
        if (_btChart) _btChart.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);
}

function renderTradesTable(trades) {
    const tbody = document.getElementById('bt-trades-body');
    if (!trades.length) {
        tbody.innerHTML = `<tr><td colspan="12" class="text-center text-base-content/40 py-6">No trades generated</td></tr>`;
        return;
    }

    tbody.innerHTML = trades.map((t, i) => {
        const outcomeClass = t.outcome === 'win'
            ? 'badge-success'
            : t.outcome === 'loss'
                ? 'badge-error'
                : t.outcome === 'early_exit'
                    ? 'badge-info'
                    : 'badge-warning';
        const rClass = t.r_multiple > 0 ? 'text-success' : t.r_multiple < 0 ? 'text-error' : '';
        const rSign  = t.r_multiple > 0 ? '+' : '';
        const date   = t.date ? t.date.slice(0, 10) : '—';
        const exitDate = t.exit_date ? t.exit_date.slice(0, 10) : '—';
        const actionBadge = t.action === 'buy'
            ? '<span class="badge badge-success badge-sm">BUY</span>'
            : '<span class="badge badge-error badge-sm">SELL</span>';

        return `<tr>
            <td class="font-mono text-xs">${i + 1}</td>
            <td class="font-mono text-xs">${date}</td>
            <td>${actionBadge}</td>
            <td class="font-mono text-xs">$${t.entry}</td>
            <td class="font-mono text-xs text-error">$${t.stop}</td>
            <td class="font-mono text-xs text-success">$${t.target}</td>
            <td class="font-mono text-xs">${t.r_risk}</td>
            <td><span class="badge ${outcomeClass} badge-sm">${t.outcome}</span></td>
            <td class="font-mono text-xs font-semibold ${rClass}">${rSign}${t.r_multiple}R</td>
            <td class="font-mono text-xs">${exitDate}</td>
            <td class="font-mono text-xs">${t.bars_held}</td>
            <td class="font-mono text-xs">${t.tier}</td>
        </tr>`;
    }).join('');
}

// ── CSV export ────────────────────────────────────────────────────────────────

function exportCsv() {
    if (!_btResult || !_btResult.trades.length) return;

    const headers = ['#', 'Date', 'Action', 'Entry', 'Stop', 'Target', 'R Risk',
                     'Outcome', 'R Multiple', 'Exit Date', 'Exit Price', 'Bars Held', 'Tier', 'Confidence'];
    const rows = _btResult.trades.map((t, i) => [
        i + 1,
        t.date ? t.date.slice(0, 10) : '',
        t.action,
        t.entry,
        t.stop,
        t.target,
        t.r_risk,
        t.outcome,
        t.r_multiple,
        t.exit_date ? t.exit_date.slice(0, 10) : '',
        t.exit_price ?? '',
        t.bars_held,
        t.tier,
        t.confidence,
    ]);

    const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `backtest_${_btResult.symbol}_${_btResult.period}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function setLoading(on) {
    const btn     = document.getElementById('bt-run-btn');
    const loading = document.getElementById('bt-loading');
    btn.disabled  = on;
    btn.innerHTML = on
        ? '<span class="loading loading-spinner loading-xs"></span> Running…'
        : '<i class="bi bi-play-fill"></i> Run Backtest';
    loading.classList.toggle('hidden', !on);
}

function showError(msg) {
    const el = document.getElementById('bt-error');
    el.innerHTML = `<i class="bi bi-exclamation-triangle-fill"></i> ${msg}`;
    el.classList.remove('hidden');
}

function hideError() {
    document.getElementById('bt-error').classList.add('hidden');
}

function showResults() {
    document.getElementById('bt-results').classList.remove('hidden');
}

function hideResults() {
    document.getElementById('bt-results').classList.add('hidden');
}

// Allow Enter key in symbol input to trigger run
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('bt-symbol').addEventListener('keydown', e => {
        if (e.key === 'Enter') runBacktest();
    });
});
