// nebula_gui_flask/static/js/main.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

// static/js/main.js — WORK WIDTH api_metrics
function t(key, params = {}, fallback = '') {
    return window.NebulaI18n?.t(key, params, fallback) || fallback || key;
}

function getPanelSettingsStorageKey(userId) {
    return `nebula-panel-settings-draft:${String(userId || 'anonymous')}`;
}

function getPanelThemeStorageKey(userId) {
    return `nebula-panel-theme:${String(userId || 'anonymous')}`;
}

function applyPanelThemeSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return;
    const vars = snapshot.vars && typeof snapshot.vars === 'object' ? snapshot.vars : {};
    Object.entries(vars).forEach(([key, value]) => {
        document.documentElement.style.setProperty(key, String(value));
    });
    const themeId = String(snapshot.id || 'custom');
    document.documentElement.dataset.panelTheme = themeId;
    if (document.body) {
        document.body.dataset.panelTheme = themeId;
    }
    const themeColor = String(snapshot.theme_color || '').trim();
    const themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeColor && themeMeta) themeMeta.setAttribute('content', themeColor);
    window.dispatchEvent(new CustomEvent('nebula:theme-applied', {
        detail: { snapshot },
    }));
}

function loadUserPanelDraft(userId) {
    try {
        const rawDraft = localStorage.getItem(getPanelSettingsStorageKey(userId));
        if (!rawDraft) return {};
        const draft = JSON.parse(rawDraft);
        return draft && typeof draft === 'object' ? draft : {};
    } catch (_) {
        return {};
    }
}

function resolveAllowedLandingPages(runtime) {
    const allowed = {
        dashboard: '/',
        containers: '/containers',
        projects: '/projects',
        databases: '/databases',
        settings: '/settings'
    };
    if (runtime.isStaff) {
        allowed.logs = '/logs';
    }
    return allowed;
}

function redirectToPreferredLandingPage(runtime, draft) {
    if (window.location.pathname !== '/') return;
    if (window.location.search.includes('skip_default_page=1')) return;
    const landingKey = String(draft['personal.default_page'] || 'dashboard');
    const allowed = resolveAllowedLandingPages(runtime);
    const targetPath = allowed[landingKey];
    if (!targetPath || targetPath === '/') return;
    window.location.replace(`${targetPath}?from=dashboard_default`);
}

function applyUserPanelPreferences() {
    const runtime = window.NebulaRuntime || {};
    const userId = String(runtime.userId || 'anonymous');

    try {
        const rawTheme = localStorage.getItem(getPanelThemeStorageKey(userId));
        if (rawTheme) {
            const themeSnapshot = JSON.parse(rawTheme);
            applyPanelThemeSnapshot(themeSnapshot);
        }
    } catch (_) {}

    const draft = loadUserPanelDraft(userId);
    document.body.classList.toggle('reduce-motion', !!draft['personal.reduce_motion']);
    document.body.classList.toggle('compact-tables', !!draft['personal.compact_tables']);
    document.body.classList.remove('content-width-default', 'content-width-narrow', 'content-width-wide');
    const widthMode = String(draft['personal.content_width'] || 'default');
    document.body.classList.add(`content-width-${widthMode}`);
    redirectToPreferredLandingPage(runtime, draft);
}

applyUserPanelPreferences();

function updateMetrics() {
    if (window.__nebulaDashboardManaged === true) {
        return;
    }
    const cpuEl = document.getElementById('cpu');
    const ramEl = document.getElementById('ram');
    const diskEl = document.getElementById('disk');
    const networkEl = document.getElementById('network');
    const containersEl = document.getElementById('containers');
    const serversEl = document.getElementById('servers');
    const alertsEl = document.getElementById('alerts');
    const tasksEl = document.getElementById('tasks');
    if (!cpuEl && !ramEl && !diskEl && !networkEl && !containersEl && !serversEl && !alertsEl && !tasksEl) {
        return;
    }

    fetch('/api/metrics')
        .then(r => {
            if (r.status === 401) {
                window.location.href = '/login';
                throw new Error('SESSION_EXPIRED');
            }
            if (!r.ok) throw new Error();
            return r.json();
        })
        .then(data => {
            if (data.error) {
                setUIOffline();
                return;
            }

            document.body.style.opacity = "1";

            if (cpuEl) cpuEl.textContent = data.cpu || "—";
            if (ramEl) ramEl.textContent = data.ram || "—";
            if (diskEl) diskEl.textContent = data.disk || "—";
            if (networkEl) networkEl.textContent = data.network || "—";
            
            if (containersEl) containersEl.textContent = data.containers || "0";
            if (serversEl) serversEl.textContent = data.servers || "0";
            if (alertsEl) alertsEl.textContent = data.alerts || "0";
            if (tasksEl) tasksEl.textContent = data.tasks || "0";
        })
        .catch(() => {
            setUIOffline();
        });
}

function setUIOffline() {
   // document.body.style.opacity = "0.6";
    document.querySelectorAll('.card-value').forEach(el => el.textContent = "—");
    const cpuEl = document.getElementById('cpu');
    if (cpuEl) cpuEl.textContent = t('common.core_offline');
}

function metricsIntervalMs() {
    return document.hidden ? 15000 : 3000;
}

function scheduleMetricsLoop() {
    if (window.__nebulaMainMetricsTimer) {
        clearInterval(window.__nebulaMainMetricsTimer);
    }
    window.__nebulaMainMetricsTimer = setInterval(updateMetrics, metricsIntervalMs());
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.__nebulaDashboardManaged === true) return;
    updateMetrics();
    scheduleMetricsLoop();
});
document.addEventListener('visibilitychange', () => {
    if (window.__nebulaDashboardManaged === true) return;
    scheduleMetricsLoop();
    if (!document.hidden) updateMetrics();
});

// User menu interactions
function toggleUserMenu(e) {
    e.stopPropagation();
    const menu = document.getElementById('userMenu');
    if (!menu) return;
    document.querySelectorAll('.user-menu').forEach(m => { if (m !== menu) m.classList.remove('show'); });
    menu.classList.toggle('show');
}

function openProfile() {
    window.location.href = '/userpanel';
}

function openSettings() {
    const userNameEl = document.querySelector('.user-nick');
    const username = userNameEl ? userNameEl.textContent.trim() : '';
    if (username) {
        window.location.href = `/users/view/${encodeURIComponent(username)}`;
        return;
    }
    window.location.href = '/userpanel';
}

// Close user menu on outside click
window.addEventListener('click', () => {
    document.querySelectorAll('.user-menu').forEach(m => m.classList.remove('show'));
});
