/**
 * bot.js — Autonomous Bot page: status card + action log
 */

const ACTION_CONFIG = {
    AUTO_ENTRY:         { badge: 'badge-success',  icon: 'bi-cart-plus-fill',         label: 'Entry' },
    AUTO_ENTRY_FAILED:  { badge: 'badge-error',    icon: 'bi-exclamation-triangle',    label: 'Entry Failed' },
    AUTO_EXIT:          { badge: 'badge-error',    icon: 'bi-x-circle-fill',           label: 'Auto Exit' },
    EXIT_DETECTED:      { badge: 'badge-warning',  icon: 'bi-door-open-fill',          label: 'Exit Detected' },
    TRAIL_STOP_APPLIED: { badge: 'badge-info',     icon: 'bi-arrow-up-circle-fill',    label: 'Trail Stop' },
    REVIEW_HOLD:        { badge: 'badge-ghost',    icon: 'bi-check-circle',            label: 'Hold' },
    REVIEW_EXIT:        { badge: 'badge-error',    icon: 'bi-x-circle',                label: 'Review: Exit' },
    REVIEW_TRAIL_STOP:  { badge: 'badge-warning',  icon: 'bi-arrow-up-circle',         label: 'Review: Trail' },
    CIRCUIT_BREAKER:    { badge: 'badge-error',    icon: 'bi-shield-fill-exclamation', label: 'Circuit Breaker' },
    MANUAL_HALT:        { badge: 'badge-warning',  icon: 'bi-pause-circle-fill',       label: 'Halted' },
    MANUAL_RESUME:      { badge: 'badge-success',  icon: 'bi-play-circle-fill',        label: 'Resumed' },
    DAILY_OPEN:         { badge: 'badge-ghost',    icon: 'bi-sunrise',                 label: 'Daily Open' },
    SCAN_ERROR:         { badge: 'badge-error',    icon: 'bi-bug-fill',                label: 'Scan Error' },
};

let _botHalted = false;

// ── Bot Status ────────────────────────────────────────────────────────────────

async function loadBotStatus() {
    const container = document.getElementById('bot-status-container');
    if (!container) return;

    try {
        const status = await (await fetch('/api/bot/status')).json();
        _botHalted = status.halted;

        const pauseLabel = document.getElementById('bot-pause-label');
        if (pauseLabel) pauseLabel.textContent = _botHalted ? 'Resume' : 'Pause';

        const runBadge  = status.running
            ? `<span class="badge badge-success badge-sm">Running</span>`
            : `<span class="badge badge-error badge-sm">Stopped</span>`;
        const haltBadge = status.halted
            ? `<span class="badge badge-warning badge-sm">Halted</span>`
            : `<span class="badge badge-ghost badge-sm">Active</span>`;
        const autoBadge = status.autonomous
            ? `<span class="badge badge-primary badge-sm">Auto-Entry ON</span>`
            : `<span class="badge badge-ghost badge-sm">Auto-Entry OFF</span>`;

        const equity = status.daily_open_equity != null
            ? `$${Number(status.daily_open_equity).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`
            : '—';

        const jobRows = (status.jobs || []).map(j => {
            const next = j.next_run ? new Date(j.next_run).toLocaleTimeString() : '—';
            return `<tr class="hover text-xs">
                <td class="font-mono">${j.id}</td>
                <td>${j.name}</td>
                <td class="font-mono">${next}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `
            <div class="bg-base-100 border border-base-300 rounded-xl p-4">
                <div class="flex flex-wrap gap-2 mb-4">
                    ${runBadge} ${haltBadge} ${autoBadge}
                    <span class="text-xs text-base-content/60 ml-auto">Today's actions: <strong>${status.today_actions || 0}</strong></span>
                    <span class="text-xs text-base-content/60">Daily open equity: <strong>${equity}</strong></span>
                </div>
                ${jobRows ? `
                <div class="overflow-x-auto">
                    <table class="table table-xs w-full">
                        <thead>
                            <tr class="text-xs text-base-content/50 uppercase">
                                <th>Job</th><th>Description</th><th>Next Run (local)</th>
                            </tr>
                        </thead>
                        <tbody>${jobRows}</tbody>
                    </table>
                </div>` : '<p class="text-xs text-base-content/50">No jobs registered</p>'}
            </div>`;

    } catch (err) {
        container.innerHTML = `<div class="alert alert-error text-sm"><span>Failed to load bot status: ${err.message}</span></div>`;
    }
}

async function toggleBotHalt() {
    try {
        const res  = await fetch('/api/bot/pause', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ halted: !_botHalted }),
        });
        const data = await res.json();
        if (data.status === 'ok') {
            await loadBotStatus();
            await loadBotActions();
        }
    } catch (err) {
        console.error('toggleBotHalt error:', err);
    }
}

// ── Action Log ────────────────────────────────────────────────────────────────

async function loadBotActions() {
    const container = document.getElementById('bot-actions-container');
    if (!container) return;

    try {
        const actions = await (await fetch('/api/bot/actions?limit=50')).json();
        if (!Array.isArray(actions) || actions.length === 0) {
            container.innerHTML = `<div class="text-center text-base-content/50 py-8 text-sm">
                <i class="bi bi-robot text-3xl mb-2 block"></i>
                No bot actions yet. The scheduler will log actions here as it runs.
            </div>`;
            return;
        }

        const rows = actions.map(a => {
            const cfg    = ACTION_CONFIG[a.action_type] || { badge: 'badge-ghost', icon: 'bi-info-circle', label: a.action_type };
            const time   = a.timestamp ? new Date(a.timestamp).toLocaleString() : '—';
            const sym    = a.symbol || '—';
            const result = (a.result || '').substring(0, 120);
            return `<tr class="hover text-xs">
                <td class="font-mono text-xs text-base-content/50 whitespace-nowrap">${time}</td>
                <td><span class="badge ${cfg.badge} badge-sm gap-1"><i class="bi ${cfg.icon}"></i>${cfg.label}</span></td>
                <td class="font-bold">${sym}</td>
                <td class="text-base-content/70 max-w-sm truncate">${result}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `
            <div class="overflow-x-auto rounded-xl border border-base-300">
                <table class="table table-xs w-full">
                    <thead>
                        <tr class="text-xs text-base-content/50 uppercase">
                            <th>Time</th><th>Action</th><th>Symbol</th><th>Result</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;

    } catch (err) {
        container.innerHTML = `<div class="alert alert-error text-sm"><span>Failed to load bot actions: ${err.message}</span></div>`;
    }
}

// ── Init ──────────────────────────────────────────────────────────────────────

window.addEventListener('load', () => {
    loadBotStatus();
    loadBotActions();
});
