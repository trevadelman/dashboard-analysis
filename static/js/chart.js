/**
 * chart.js — LightweightCharts initialization and data loading
 */

let chart, candleSeries, smaShortSeries, smaMedSeries, smaLongSeries;

function initChart() {
    const chartContainer = document.getElementById('chart-container');

    // Read live CSS variable values so the chart always matches the current theme
    const style      = getComputedStyle(document.documentElement);
    const bgColor    = '#EEF2EE';   // --b1 page background
    const textColor  = '#0F1F14';   // --bc primary text
    const gridColor  = '#DDE6DF';   // Border Subtle
    const borderColor = '#C8D8CB';  // Border Strong

    chart = LightweightCharts.createChart(chartContainer, {
        layout: { backgroundColor: bgColor, textColor: textColor },
        grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: borderColor },
        timeScale: { borderColor: borderColor },
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#0D9B55', downColor: '#D63B3B', borderVisible: false,
        wickUpColor: '#0D9B55', wickDownColor: '#D63B3B',
    });

    smaShortSeries = chart.addLineSeries({ color: '#149960', lineWidth: 2, title: 'EMA 9',   lastValueVisible: false, priceLineVisible: false });
    smaMedSeries   = chart.addLineSeries({ color: '#4DCE99', lineWidth: 2, title: 'EMA 21',  lastValueVisible: false, priceLineVisible: false });
    smaLongSeries  = chart.addLineSeries({ color: '#8AA394', lineWidth: 2, title: 'EMA 50',  lastValueVisible: false, priceLineVisible: false });

    window._chartInstance = chart;

    window.addEventListener('resize', () => {
        chart.applyOptions({ width: chartContainer.clientWidth });
    });
}

function filterOutliers(candles) {
    if (candles.length < 10) return candles;
    const closes = candles.map(c => c.close).sort((a, b) => a - b);
    const median = closes[Math.floor(closes.length / 2)];
    const mean = closes.reduce((s, v) => s + v, 0) / closes.length;
    const stdDev = Math.sqrt(closes.reduce((s, v) => s + Math.pow(v - mean, 2), 0) / closes.length);
    return candles.filter(c =>
        [c.open, c.high, c.low, c.close].every(v => Math.abs(v - median) <= 3 * stdDev)
    );
}

function toUnixSeconds(d) {
    return typeof d.timestamp === 'number'
        ? d.timestamp / 1000
        : new Date(d.timestamp).getTime() / 1000;
}

// Map interval → period so the chart always shows a meaningful lookback
const INTERVAL_PERIOD = {
    '1d':  '2y',
    '1h':  '3mo',
    '15m': '1mo',
    '5m':  '2w',
    '1m':  '5d',
};

async function loadChart() {
    const symbol   = document.getElementById('symbol-input').value.toUpperCase();
    const interval = document.getElementById('interval-input').value;
    const period   = INTERVAL_PERIOD[interval] || '2y';
    window.currentSymbol = symbol;

    try {
        const response = await fetch(`/api/market_data?symbol=${symbol}&interval=${interval}&period=${period}`);
        const data = await response.json();
        if (!data || data.length === 0) { console.warn('loadChart: no data returned'); return; }

        let candles = data.map(d => ({
            time: toUnixSeconds(d), open: d.open, high: d.high, low: d.low, close: d.close
        })).sort((a, b) => a.time - b.time);

        candles = filterOutliers(candles);
        candleSeries.setData(candles);

        smaShortSeries.setData(data.filter(d => d.sma_20).map(d => ({ time: toUnixSeconds(d), value: d.sma_20 })).sort((a, b) => a.time - b.time));
        smaMedSeries.setData(data.filter(d => d.sma_50).map(d => ({ time: toUnixSeconds(d), value: d.sma_50 })).sort((a, b) => a.time - b.time));
        smaLongSeries.setData(data.filter(d => d.sma_200).map(d => ({ time: toUnixSeconds(d), value: d.sma_200 })).sort((a, b) => a.time - b.time));

        chart.timeScale().fitContent();
    } catch (err) { console.error('loadChart error:', err); }
}
