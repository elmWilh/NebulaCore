// nebula_gui_flask/static/js/pages/plugins.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

const gridEl = document.getElementById('plugins-grid');
const summaryEl = document.getElementById('plugins-summary');
const alertEl = document.getElementById('plugins-alert');
const refreshBtn = document.getElementById('plugins-refresh');
const rescanBtn = document.getElementById('plugins-rescan');
const logsModal = document.getElementById('plugin-logs-modal');
const logsTitle = document.getElementById('plugin-logs-title');
const logsBody = document.getElementById('plugin-logs-body');
const logsClose = document.getElementById('plugin-logs-close');
let pluginsRenderHash = '';
let pluginsAutoRefreshTimer = null;
let pluginsLoadInFlight = false;

function esc(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function statusClass(status) {
    const s = String(status || '').toLowerCase();
    if (["healthy", "initialized", "degraded", "unresponsive", "crashed", "disabled"].includes(s)) {
        return `status-${s}`;
    }
    return 'status-degraded';
}

function showAlert(text, isError = false) {
    if (!alertEl) return;
    alertEl.style.display = 'block';
    alertEl.style.borderColor = isError ? '#ff6b6b66' : 'var(--border)';
    alertEl.style.background = isError ? 'rgba(255,107,107,0.08)' : 'rgba(255,255,255,0.04)';
    alertEl.textContent = text;
    window.clearTimeout(showAlert._hideTimer);
    showAlert._hideTimer = window.setTimeout(() => {
        alertEl.style.display = 'none';
    }, 4200);
}

async function apiJson(url, options = {}) {
    const res = await fetch(url, options);
    let data = {};
    try {
        data = await res.json();
    } catch (_) {
        data = {};
    }
    if (!res.ok) {
        throw new Error(String(data?.detail || `HTTP ${res.status}`));
    }
    return data;
}

function renderSummary(plugins) {
    if (!summaryEl) return;
    const total = plugins.length;
    const healthy = plugins.filter(p => String(p.status || '').toLowerCase() === 'healthy').length;
    const degraded = plugins.filter(p => ['degraded', 'unresponsive', 'crashed'].includes(String(p.status || '').toLowerCase())).length;
    const disabled = plugins.filter(p => String(p.status || '').toLowerCase() === 'disabled').length;
    summaryEl.innerHTML = `
        <div class="plugins-kpi"><div class="label">Total</div><div class="value">${total}</div></div>
        <div class="plugins-kpi"><div class="label">Healthy</div><div class="value">${healthy}</div></div>
        <div class="plugins-kpi"><div class="label">Issues</div><div class="value">${degraded}</div></div>
        <div class="plugins-kpi"><div class="label">Disabled</div><div class="value">${disabled}</div></div>
    `;
}

function renderPlugins(plugins) {
    if (!gridEl) return;
    if (!plugins.length) {
        gridEl.innerHTML = '<div class="plugins-empty">No plugins discovered.</div>';
        return;
    }

    const cards = plugins.map((p) => {
        const runtime = p.runtime || {};
        const scopes = Array.isArray(p.scopes) ? p.scopes : [];
        const rssMb = runtime.rss_kb ? (Number(runtime.rss_kb) / 1024).toFixed(1) : '—';
        const vmMb = runtime.vm_kb ? (Number(runtime.vm_kb) / 1024).toFixed(1) : '—';
        const source = esc(p.source || 'unknown');
        const runtimeVer = esc(p.runtime_version || '-');
        const apiVer = esc(p.api_version || '-');
        const version = esc(p.version || '-');
        const desc = esc(p.description || 'No description');
        const msg = esc(p.message || '');
        const warn = esc(p.warning || '');
        const err = esc(p.error || '');
        const status = esc(p.status || 'unknown');
        const name = esc(p.name || 'plugin');

        return `
            <div class="plugin-card" data-plugin="${name}">
                <div class="plugin-card-head">
                    <div>
                        <h3 class="plugin-title">${name}</h3>
                        <div class="plugin-meta">v${version} • ${source} • ${runtimeVer} • API ${apiVer}</div>
                    </div>
                    <span class="plugin-status ${statusClass(status)}">${status}</span>
                </div>
                <div class="plugin-body">
                    <div class="plugin-description">${desc}</div>
                    <div class="plugin-runtime">
                        <div class="plugin-runtime-item"><div class="label">PID</div><div class="value">${runtime.pid ?? '—'}</div></div>
                        <div class="plugin-runtime-item"><div class="label">Alive</div><div class="value">${runtime.alive ? 'yes' : 'no'}</div></div>
                        <div class="plugin-runtime-item"><div class="label">RSS</div><div class="value">${rssMb} MB</div></div>
                        <div class="plugin-runtime-item"><div class="label">VM</div><div class="value">${vmMb} MB</div></div>
                    </div>
                    <div class="plugin-scopes">
                        ${scopes.length ? scopes.map(s => `<span class="scope-chip">${esc(s)}</span>`).join('') : '<span class="scope-chip">no scopes</span>'}
                    </div>
                    <div class="plugin-actions">
                        <button type="button" class="btn-plugin btn-plugin-neutral" data-action="health" data-plugin="${name}"><i class="bi bi-heart-pulse"></i> Health</button>
                        <button type="button" class="btn-plugin btn-plugin-neutral" data-action="logs" data-plugin="${name}"><i class="bi bi-journal-text"></i> Logs</button>
                        <button type="button" class="btn-plugin btn-plugin-danger" data-action="stop" data-plugin="${name}"><i class="bi bi-stop-circle"></i> Stop</button>
                        <button type="button" class="btn-plugin btn-plugin-success" data-action="start" data-plugin="${name}"><i class="bi bi-play-circle"></i> Start</button>
                        <button type="button" class="btn-plugin btn-plugin-accent" data-action="restart" data-plugin="${name}"><i class="bi bi-arrow-clockwise"></i> Restart</button>
                    </div>
                    <div class="plugin-extra">
                        ${msg ? `<div><b>Message:</b> ${msg}</div>` : ''}
                        ${warn ? `<div><b>Warning:</b> ${warn}</div>` : ''}
                        ${err ? `<div><b>Error:</b> ${err}</div>` : ''}
                        <div><b>Timeouts:</b> ${Number(p.consecutive_timeouts || 0)} • <b>Health failures:</b> ${Number(p.consecutive_health_failures || 0)} • <b>Crashes:</b> ${Number(p.consecutive_crashes || 0)} • <b>Restarts:</b> ${Number(p.restart_count || 0)}</div>
                    </div>
                </div>
            </div>
        `;
    });

    gridEl.innerHTML = cards.join('');
}

async function loadPlugins(options = {}) {
    const silent = Boolean(options.silent);
    if (pluginsLoadInFlight) {
        return;
    }
    pluginsLoadInFlight = true;

    if (gridEl && !silent) {
        gridEl.innerHTML = '<div class="plugins-empty">Loading plugins...</div>';
    }
    try {
        const payload = await apiJson('/api/plugins/list');
        const plugins = Array.isArray(payload?.plugins) ? payload.plugins : [];
        const nextHash = JSON.stringify(plugins);
        renderSummary(plugins);
        if (nextHash !== pluginsRenderHash) {
            renderPlugins(plugins);
            pluginsRenderHash = nextHash;
        }
    } catch (e) {
        if (gridEl && !silent) {
            gridEl.innerHTML = `<div class="plugins-empty" style="color:#ff8b8b;">Failed to load plugins: ${esc(e.message)}</div>`;
        }
    } finally {
        pluginsLoadInFlight = false;
    }
}

async function pluginAction(plugin, action) {
    try {
        const payload = await apiJson(`/api/plugins/${encodeURIComponent(plugin)}/action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action }),
        });
        showAlert(`${plugin}: ${action} completed`);
        await loadPlugins();
        return payload;
    } catch (e) {
        showAlert(`${plugin}: ${action} failed: ${e.message}`, true);
        return null;
    }
}

async function openLogs(plugin) {
    if (!logsModal || !logsTitle || !logsBody) return;
    logsModal.style.display = 'flex';
    logsTitle.textContent = `Plugin Logs: ${plugin}`;
    logsBody.textContent = 'Loading logs...';
    try {
        const payload = await apiJson(`/api/plugins/${encodeURIComponent(plugin)}/logs?tail=300`);
        const lines = Array.isArray(payload?.lines) ? payload.lines : [];
        logsBody.textContent = lines.length ? lines.join('\n') : 'No log lines available.';
    } catch (e) {
        logsBody.textContent = `Failed to load logs: ${e.message}`;
    }
}

async function checkHealth(plugin) {
    try {
        const payload = await apiJson(`/api/plugins/${encodeURIComponent(plugin)}/health`);
        showAlert(`${plugin}: health ok (${JSON.stringify(payload?.health || {})})`);
    } catch (e) {
        showAlert(`${plugin}: health failed: ${e.message}`, true);
    }
}

async function rescanPlugins() {
    try {
        await apiJson('/api/plugins/rescan', { method: 'POST' });
        showAlert('Plugin rescan completed');
        await loadPlugins();
    } catch (e) {
        showAlert(`Rescan failed: ${e.message}`, true);
    }
}

if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadPlugins({ silent: false }));
}

if (rescanBtn) {
    rescanBtn.addEventListener('click', () => rescanPlugins());
}

if (gridEl) {
    gridEl.addEventListener('click', async (event) => {
        const btn = event.target.closest('button[data-action][data-plugin]');
        if (!btn) return;
        const action = String(btn.dataset.action || '').toLowerCase();
        const plugin = String(btn.dataset.plugin || '').trim();
        if (!action || !plugin) return;

        if (action === 'logs') {
            await openLogs(plugin);
            return;
        }
        if (action === 'health') {
            await checkHealth(plugin);
            return;
        }
        if (['start', 'stop', 'restart'].includes(action)) {
            await pluginAction(plugin, action);
        }
    });
}

if (logsClose && logsModal) {
    logsClose.addEventListener('click', () => {
        logsModal.style.display = 'none';
    });
    logsModal.addEventListener('click', (event) => {
        if (event.target === logsModal) logsModal.style.display = 'none';
    });
}

document.addEventListener('DOMContentLoaded', () => {
    loadPlugins({ silent: false });
    pluginsAutoRefreshTimer = window.setInterval(() => {
        if (document.hidden) return;
        loadPlugins({ silent: true });
    }, 30000);
});
