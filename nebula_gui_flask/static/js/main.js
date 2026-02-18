// nebula_gui_flask/static/js/main.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

// static/js/main.js — WORK WIDTH api_metrics
function updateMetrics() {
    const cpuEl = document.getElementById('cpu');
    const ramEl = document.getElementById('ram');
    const diskEl = document.getElementById('disk');
    const networkEl = document.getElementById('network');
    const containersEl = document.getElementById('containers');
    const serversEl = document.getElementById('servers');
    const alertsEl = document.getElementById('alerts');
    const tasksEl = document.getElementById('tasks');

    if (!cpuEl || !ramEl || !diskEl || !networkEl || !containersEl || !serversEl || !alertsEl || !tasksEl) {
        return;
    }

    fetch('/api/metrics')
        .then(r => {
            if (!r.ok) throw new Error();
            return r.json();
        })
        .then(data => {
            if (data.error) {
                setUIOffline();
                return;
            }

            document.body.style.opacity = "1";

            cpuEl.textContent = data.cpu || "—";
            ramEl.textContent = data.ram || "—";
            diskEl.textContent = data.disk || "—";
            networkEl.textContent = data.network || "—";
            
            containersEl.textContent = data.containers || "0";
            serversEl.textContent = data.servers || "0";
            alertsEl.textContent = data.alerts || "0";
            tasksEl.textContent = data.tasks || "0";
        })
        .catch(() => {
            setUIOffline();
        });
}

function setUIOffline() {
   // document.body.style.opacity = "0.6";
    document.querySelectorAll('.card-value').forEach(el => el.textContent = "—");
    const cpuEl = document.getElementById('cpu');
    if (cpuEl) cpuEl.textContent = "Core offline";
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
    updateMetrics();
    scheduleMetricsLoop();
});
document.addEventListener('visibilitychange', () => {
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
