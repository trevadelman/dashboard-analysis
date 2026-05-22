/**
 * config.js — Strategy config loading, saving, and momentum presets
 */

const PARAM_GROUPS = {
    momentum: {
        keys: ['rsi_buy', 'rsi_sell', 'rsi_neutral_min', 'rsi_neutral_max', 'ai_confidence_min'],
        labels: { rsi_buy: 'RSI Buy Threshold', rsi_sell: 'RSI Sell Threshold', rsi_neutral_min: 'Neutral Zone Min', rsi_neutral_max: 'Neutral Zone Max', ai_confidence_min: 'AI Min Confidence' }
    },
    risk: {
        keys: ['atr_multiplier', 'min_rr_ratio', 'atr_pct_min', 'atr_pct_max', 'price_range_min'],
        labels: { atr_multiplier: 'ATR Multiplier', min_rr_ratio: 'Min R:R Ratio', atr_pct_min: 'Min ATR %', atr_pct_max: 'Max ATR %', price_range_min: 'Min Price Range' }
    }
};

function renderParamInput(key, val, label) {
    const step = val < 1 ? '0.01' : '1';
    return `<div class="form-control">
        <label class="label py-1">
            <span class="label-text text-xs">${label}</span>
            <input type="number" step="${step}" class="input input-bordered input-sm w-20 text-right config-input" data-key="${key}" value="${val}">
        </label>
    </div>`;
}

async function loadConfig() {
    const config = await (await fetch('/api/config')).json();
    for (const [groupKey, group] of Object.entries(PARAM_GROUPS)) {
        const container = document.getElementById(`settings-${groupKey}`);
        if (!container) continue;
        container.innerHTML = group.keys
            .filter(k => config[k] !== undefined)
            .map(k => renderParamInput(k, config[k], group.labels[k] || k))
            .join('');
    }
}

async function updateConfig() {
    const inputs = document.querySelectorAll('.config-input');
    const config = {};
    inputs.forEach(i => config[i.dataset.key] = parseFloat(i.value));

    const statusEl = document.getElementById('config-save-status');
    const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    });
    if (response.ok) {
        if (statusEl) {
            statusEl.textContent = '✓ Saved';
            statusEl.className = 'text-xs text-success';
            setTimeout(() => { statusEl.textContent = ''; }, 2000);
        }
        loadChart();
    }
}

const MOMENTUM_PRESETS = {
    breakout: {
        label: '🚀 Breakout',
        description: 'Catches momentum breakouts. High RSI buy threshold, tight stops, strong trend required.',
        params: { rsi_buy: 60, rsi_sell: 75, rsi_neutral_min: 45, rsi_neutral_max: 60, ai_confidence_min: 70, atr_multiplier: 1.5, min_rr_ratio: 2.0, atr_pct_min: 1.5, atr_pct_max: 8.0, price_range_min: 10 }
    },
    mean_revert: {
        label: '↩️ Mean Revert',
        description: 'Buys oversold dips, sells overbought peaks. Low RSI buy, high RSI sell.',
        params: { rsi_buy: 30, rsi_sell: 70, rsi_neutral_min: 40, rsi_neutral_max: 60, ai_confidence_min: 60, atr_multiplier: 2.0, min_rr_ratio: 1.5, atr_pct_min: 1.0, atr_pct_max: 6.0, price_range_min: 5 }
    },
    trend: {
        label: '📈 Trend Follow',
        description: 'Follows established trends. Requires SMA alignment + moderate RSI. Wider stops.',
        params: { rsi_buy: 45, rsi_sell: 70, rsi_neutral_min: 40, rsi_neutral_max: 55, ai_confidence_min: 65, atr_multiplier: 2.5, min_rr_ratio: 1.5, atr_pct_min: 0.8, atr_pct_max: 5.0, price_range_min: 5 }
    },
    scalp: {
        label: '⚡ Scalp',
        description: 'Quick in-and-out trades. Low AI confidence bar, tight R:R, high volatility required.',
        params: { rsi_buy: 35, rsi_sell: 65, rsi_neutral_min: 45, rsi_neutral_max: 55, ai_confidence_min: 50, atr_multiplier: 1.0, min_rr_ratio: 1.2, atr_pct_min: 2.0, atr_pct_max: 10.0, price_range_min: 2 }
    }
};

async function applyMomentumPreset(name) {
    const preset = MOMENTUM_PRESETS[name];
    if (!preset) return;

    const inputs = document.querySelectorAll('.config-input');
    inputs.forEach(input => {
        const key = input.dataset.key;
        if (preset.params[key] !== undefined) input.value = preset.params[key];
    });

    document.querySelectorAll('.momentum-preset-btn').forEach(btn => {
        btn.classList.remove('btn-primary');
        btn.classList.add('btn-outline');
    });
    const activeBtn = document.querySelector(`[data-preset="${name}"]`);
    if (activeBtn) {
        activeBtn.classList.remove('btn-outline');
        activeBtn.classList.add('btn-primary');
    }

    document.getElementById('momentum_preset_desc').textContent = preset.description;

    const config = {};
    inputs.forEach(i => config[i.dataset.key] = parseFloat(i.value));
    Object.entries(preset.params).forEach(([k, v]) => {
        if (config[k] === undefined || isNaN(config[k])) config[k] = v;
    });

    const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    });
    if (response.ok) {
        document.getElementById('momentum_preset_desc').textContent = preset.description + ' ✅ Applied';
    }
}
