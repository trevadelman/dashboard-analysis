/**
 * settings.js — Settings modal: Alpaca profiles, AI provider, App settings
 * Loaded globally via layout.html so it's available on every page.
 */

// ── AI provider presets ───────────────────────────────────────────────────────

const AI_PRESETS = {
    ollama: {
        base_url: "http://localhost:11434/v1",
        api_key:  "ollama",
        model:    "gemma3:4b-it-qat",
    },
    deepseek: {
        base_url: "https://api.deepseek.com/v1",
        api_key:  "",          // user must supply
        model:    "deepseek-chat",
    },
};

// ── Tab switching ─────────────────────────────────────────────────────────────

function switchSettingsTab(tab) {
    ['alpaca', 'ai', 'app'].forEach(t => {
        const panel = document.getElementById(`settings-panel-${t}`);
        const tabEl = document.getElementById(`tab-${t}`);
        if (panel) panel.classList.toggle('hidden', t !== tab);
        if (tabEl) tabEl.classList.toggle('tab-active', t === tab);
    });
}

// ── Load all settings into the modal ─────────────────────────────────────────

async function loadSettings() {
    await Promise.all([loadProfiles(), loadAppSettings()]);
}

// ── Alpaca Profiles ───────────────────────────────────────────────────────────

async function loadProfiles() {
    try {
        const profiles = await fetch('/api/profiles').then(r => r.json());
        renderProfiles(profiles);
    } catch (e) {
        document.getElementById('profiles-list').innerHTML =
            '<div class="text-xs text-error">Failed to load profiles</div>';
    }
}

function renderProfiles(profiles) {
    const container = document.getElementById('profiles-list');
    if (!profiles.length) {
        container.innerHTML = '<div class="text-xs text-base-content/40 py-2">No profiles saved yet.</div>';
        return;
    }
    container.innerHTML = profiles.map(p => `
        <div class="flex items-center justify-between p-2.5 rounded-xl border ${p.is_active ? 'border-primary bg-primary/5' : 'border-base-300 bg-base-100'}">
            <div class="flex items-center gap-2.5">
                <span class="inline-block w-2 h-2 rounded-full flex-shrink-0 ${p.is_active ? 'bg-success' : 'bg-base-content/20'}"></span>
                <div>
                    <div class="text-sm font-medium leading-tight">${p.name}</div>
                    <div class="text-xs text-base-content/50">${p.paper_trading ? '📄 Paper' : '💰 Live'}</div>
                </div>
                ${p.is_active ? '<span class="badge badge-primary badge-xs">Active</span>' : ''}
            </div>
            <div class="flex gap-1">
                ${!p.is_active ? `<button class="btn btn-xs btn-ghost text-success" onclick="activateProfile(${p.id})" title="Activate">
                    <i class="bi bi-play-fill"></i>
                </button>` : ''}
                <button class="btn btn-xs btn-ghost text-error" onclick="deleteProfile(${p.id}, '${p.name.replace(/'/g, "\\'")}')" title="Delete">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
    `).join('');
}

async function saveProfile(activate) {
    const name      = document.getElementById('profile-name').value.trim();
    const apiKey    = document.getElementById('profile-api-key').value.trim();
    const secretKey = document.getElementById('profile-secret-key').value.trim();
    const paper     = document.getElementById('profile-paper').checked;
    const status    = document.getElementById('profile-status');

    if (!name || !apiKey || !secretKey) {
        status.innerHTML = '<span class="text-error">Name, API key, and secret key are required.</span>';
        return;
    }

    status.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Saving…';

    try {
        const res  = await fetch('/api/profiles', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ name, api_key: apiKey, secret_key: secretKey, paper_trading: paper, activate }),
        });
        const data = await res.json();

        if (data.error) {
            status.innerHTML = `<span class="text-error">${data.error}</span>`;
            return;
        }

        status.innerHTML = `<span class="text-success">✓ ${activate ? 'Saved and activated' : 'Saved'}</span>`;
        document.getElementById('profile-name').value       = '';
        document.getElementById('profile-api-key').value    = '';
        document.getElementById('profile-secret-key').value = '';
        await loadProfiles();

        if (activate && typeof updateAccount === 'function') setTimeout(updateAccount, 500);
        if (activate && typeof setAlpacaStatus === 'function') setAlpacaStatus(true, name);
        setTimeout(() => { status.innerHTML = ''; }, 3000);
    } catch (e) {
        status.innerHTML = `<span class="text-error">${e.message}</span>`;
    }
}

async function activateProfile(id) {
    const res  = await fetch(`/api/profiles/${id}/activate`, { method: 'POST' });
    const data = await res.json();
    if (data.error) { alert(`Activation failed: ${data.error}`); return; }
    await loadProfiles();
    if (typeof setAlpacaStatus === 'function') setAlpacaStatus(true, data.profile_name || '');
    if (typeof updateAccount === 'function') setTimeout(updateAccount, 500);
}

async function deleteProfile(id, name) {
    if (!confirm(`Delete profile "${name}"?`)) return;
    const res = await fetch(`/api/profiles/${id}`, { method: 'DELETE' });
    if (res.ok) await loadProfiles();
}

// ── AI Settings ───────────────────────────────────────────────────────────────

function applyAiPreset(preset) {
    const p = AI_PRESETS[preset];
    if (!p) return;

    document.getElementById('setting-ai-base-url').value = p.base_url;
    document.getElementById('setting-ai-model').value    = p.model;

    // For DeepSeek, clear the key field so the user knows they need to enter one.
    // For Ollama, pre-fill "ollama" (the conventional no-auth key).
    document.getElementById('setting-ai-api-key').value = p.api_key;

    // Highlight the active preset card
    ['ollama', 'deepseek'].forEach(name => {
        const card = document.getElementById(`preset-${name}`);
        if (card) card.classList.toggle('border-primary', name === preset);
    });

    // Clear test result
    document.getElementById('ai-test-result').innerHTML = '';
}

async function loadAppSettings() {
    try {
        const s = await fetch('/api/settings').then(r => r.json());

        // AI tab
        document.getElementById('setting-ai-base-url').value = s.ai_base_url    || '';
        document.getElementById('setting-ai-api-key').value  = s.ai_api_key     || '';
        document.getElementById('setting-ai-model').value    = s.ai_model       || '';

        // Highlight matching preset
        const url = (s.ai_base_url || '').toLowerCase();
        if (url.includes('localhost') || url.includes('11434')) {
            document.getElementById('preset-ollama')?.classList.add('border-primary');
            document.getElementById('preset-deepseek')?.classList.remove('border-primary');
        } else if (url.includes('deepseek')) {
            document.getElementById('preset-deepseek')?.classList.add('border-primary');
            document.getElementById('preset-ollama')?.classList.remove('border-primary');
        }

        // App tab — risk
        document.getElementById('setting-max-positions').value = s.max_positions   || '5';
        document.getElementById('setting-risk-pct').value      = s.risk_percentage || '2.0';

        // App tab — autonomous bot
        const autonomous = (s.bot_autonomous || 'false').toLowerCase() === 'true';
        document.getElementById('setting-bot-autonomous').checked = autonomous;

        const watchlistEl = document.getElementById('setting-bot-watchlist');
        if (watchlistEl) watchlistEl.value = s.bot_scan_watchlist || 'sp500_top100';

        document.getElementById('setting-bot-max-loss').value  = s.bot_max_daily_loss_pct   || '2.0';
        document.getElementById('setting-bot-cooldown').value  = s.bot_entry_cooldown_hours || '24';

        const reviewTfs = (s.bot_review_timeframes || 'swing,long').split(',').map(t => t.trim());
        document.getElementById('setting-bot-tf-long').checked  = reviewTfs.includes('long');
        document.getElementById('setting-bot-tf-swing').checked = reviewTfs.includes('swing');
        document.getElementById('setting-bot-tf-short').checked = reviewTfs.includes('short');

        // Password always blank on load
        document.getElementById('setting-password').value         = '';
        document.getElementById('setting-password-confirm').value = '';
    } catch (e) {
        console.error('Failed to load app settings:', e);
    }
}

async function saveAiSettings() {
    const result = document.getElementById('ai-test-result');
    result.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Saving…';

    const payload = {
        ai_base_url: document.getElementById('setting-ai-base-url').value.trim(),
        ai_api_key:  document.getElementById('setting-ai-api-key').value.trim(),
        ai_model:    document.getElementById('setting-ai-model').value.trim(),
    };

    try {
        const res  = await fetch('/api/settings', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        const data = await res.json();
        result.innerHTML = data.error
            ? `<span class="text-error">${data.error}</span>`
            : '<span class="text-success">✓ Saved</span>';
        setTimeout(() => { result.innerHTML = ''; }, 3000);
    } catch (e) {
        result.innerHTML = `<span class="text-error">${e.message}</span>`;
    }
}

async function saveAppSettings() {
    const status = document.getElementById('app-settings-status');
    const pw     = document.getElementById('setting-password').value;
    const pwConf = document.getElementById('setting-password-confirm').value;

    if (pw && pw !== pwConf) {
        status.innerHTML = '<span class="text-error">Passwords do not match.</span>';
        return;
    }

    status.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Saving…';

    // Build review timeframes string from checkboxes
    const reviewTfs = ['long', 'swing', 'short']
        .filter(tf => document.getElementById(`setting-bot-tf-${tf}`)?.checked)
        .join(',') || 'swing,long';

    const payload = {
        max_positions:      parseInt(document.getElementById('setting-max-positions').value, 10),
        risk_percentage:    parseFloat(document.getElementById('setting-risk-pct').value),
        dashboard_password: pw,
        // Autonomous bot
        bot_autonomous:           document.getElementById('setting-bot-autonomous').checked ? 'true' : 'false',
        bot_scan_watchlist:       document.getElementById('setting-bot-watchlist').value,
        bot_max_daily_loss_pct:   parseFloat(document.getElementById('setting-bot-max-loss').value),
        bot_entry_cooldown_hours: parseInt(document.getElementById('setting-bot-cooldown').value, 10),
        bot_review_timeframes:    reviewTfs,
    };

    try {
        const res  = await fetch('/api/settings', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        const data = await res.json();

        if (data.error) {
            status.innerHTML = `<span class="text-error">${data.error}</span>`;
            return;
        }

        status.innerHTML = '<span class="text-success">✓ Saved</span>';
        document.getElementById('setting-password').value         = '';
        document.getElementById('setting-password-confirm').value = '';
        setTimeout(() => { status.innerHTML = ''; }, 3000);
    } catch (e) {
        status.innerHTML = `<span class="text-error">${e.message}</span>`;
    }
}

async function testAiConnection() {
    const result  = document.getElementById('ai-test-result');
    const baseUrl = document.getElementById('setting-ai-base-url').value.trim();
    const apiKey  = document.getElementById('setting-ai-api-key').value.trim();

    result.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Testing…';

    try {
        const res  = await fetch('/api/settings/test-ai', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ ai_base_url: baseUrl, ai_api_key: apiKey }),
        });
        const data = await res.json();

        if (data.status === 'ok') {
            const modelList = (data.models || []).slice(0, 4).join(', ');
            result.innerHTML = `<span class="text-success">✓ Connected${modelList ? ' — ' + modelList : ''}</span>`;
        } else {
            result.innerHTML = `<span class="text-error">✗ ${data.message}</span>`;
        }
    } catch (e) {
        result.innerHTML = `<span class="text-error">✗ ${e.message}</span>`;
    }
}
