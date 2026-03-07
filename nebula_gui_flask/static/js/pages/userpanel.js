// nebula_gui_flask/static/js/pages/userpanel.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

let panelData = null;
let twoFAEnabled = false;

function sanitizeOtpInputValue(value) {
    return String(value || '').replace(/\D/g, '').slice(0, 6);
}

function sanitizeOtpPart(value) {
    return String(value || '').replace(/\D/g, '').slice(0, 1);
}

function readSplitCode(prefix) {
    const digits = [];
    for (let i = 1; i <= 6; i += 1) {
        const field = document.getElementById(`${prefix}-${i}`);
        const val = sanitizeOtpPart(field ? field.value : '');
        if (field) field.value = val;
        digits.push(val);
    }
    return digits.join('');
}

function resetSplitCode(prefix) {
    for (let i = 1; i <= 6; i += 1) {
        const field = document.getElementById(`${prefix}-${i}`);
        if (field) field.value = '';
    }
}

function bindSplitOtpInputs(prefix) {
    const fields = [];
    for (let i = 1; i <= 6; i += 1) {
        const field = document.getElementById(`${prefix}-${i}`);
        if (field) fields.push(field);
    }
    if (fields.length !== 6) return;
    fields.forEach((field, idx) => {
        field.addEventListener('input', () => {
            field.value = sanitizeOtpPart(field.value);
            if (field.value && idx < fields.length - 1) {
                fields[idx + 1].focus();
            }
        });
        field.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && !field.value && idx > 0) {
                fields[idx - 1].focus();
            }
            if (e.key === 'ArrowLeft' && idx > 0) {
                fields[idx - 1].focus();
            }
            if (e.key === 'ArrowRight' && idx < fields.length - 1) {
                fields[idx + 1].focus();
            }
        });
        field.addEventListener('paste', (e) => {
            const text = (e.clipboardData || window.clipboardData).getData('text');
            const digits = sanitizeOtpInputValue(text);
            if (digits.length !== 6) return;
            e.preventDefault();
            for (let i = 0; i < 6; i += 1) {
                fields[i].value = digits[i];
            }
            fields[5].focus();
        });
    });
}

function render2FAStatusChip(enabled, loading = false) {
    const status = document.getElementById('twofa-status');
    if (!status) return;
    status.classList.remove('is-enabled', 'is-disabled', 'is-loading');
    if (loading) {
        status.classList.add('is-loading');
        status.textContent = 'Status: loading...';
        return;
    }
    if (enabled) {
        status.classList.add('is-enabled');
        status.textContent = 'Status: enabled';
        return;
    }
    status.classList.add('is-disabled');
    status.textContent = 'Status: disabled';
}

function statusClass(status) {
    return String(status || '').toLowerCase() === 'running' ? 'status-running' : 'status-stopped';
}

function renderStats(stats) {
    document.getElementById('panel-containers').textContent = `${stats.running_containers}/${stats.total_containers}`;
    document.getElementById('stat-running').textContent = stats.running_containers;
    document.getElementById('stat-total').textContent = stats.total_containers;
    document.getElementById('stat-cpu').textContent = `${stats.cpu_percent}%`;
    document.getElementById('stat-ram').textContent = `${stats.memory_percent}%`;
    document.getElementById('stat-db').textContent = stats.databases_count;
}

function renderContainers(containers) {
    const tbody = document.getElementById('containers-tbody');
    if (!Array.isArray(containers) || containers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding: 20px; color: var(--text-muted);">No containers available</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    containers.forEach(cont => {
        const users = Array.isArray(cont.users) ? cont.users.join(', ') : '-';
        const row = document.createElement('tr');
        row.innerHTML = `
            <td style="padding: 14px 20px;"><div style="color:white;font-weight:600;">${cont.name}</div><div style="font-size:0.72rem;color:var(--text-muted);">${cont.id}</div></td>
            <td style="padding: 14px 20px; color:#cfcfcf;">${cont.image || '-'}</td>
            <td style="padding: 14px 20px; color:#cfcfcf;">${users || '-'}</td>
            <td style="padding: 14px 20px;"><span class="status-pill ${statusClass(cont.status)}">${cont.status || 'unknown'}</span></td>
            <td style="padding: 14px 20px; text-align:right; display:flex; gap:6px; justify-content:flex-end;">
                <button class="btn-mini" onclick="openLogs('${cont.id}','${cont.name}')">Logs</button>
                <button class="btn-mini" onclick="containerAction('restart','${cont.id}')">Restart</button>
                <button class="btn-mini" onclick="containerAction('${String(cont.status).toLowerCase()==='running' ? 'stop' : 'start'}','${cont.id}')">${String(cont.status).toLowerCase()==='running' ? 'Stop' : 'Start'}</button>
            </td>
        `;
        tbody.appendChild(row);
    });
}

function renderDatabases(databases) {
    const target = document.getElementById('db-list');
    if (!Array.isArray(databases) || databases.length === 0) {
        target.innerHTML = '<div class="db-node">No databases found</div>';
        return;
    }

    target.innerHTML = databases.map(db => {
        const dbClean = String(db).replace(/\.db$/,'');
        const openHref = panelData && panelData.is_staff
            ? '/users'
            : `/users/view/${encodeURIComponent(panelData.username)}?db_name=${encodeURIComponent(panelData.db_name)}`;
        return `<div class="db-node"><div><div style="font-weight:600;">${db}</div><div style="font-size:0.73rem;color:var(--text-muted);">Client sector: ${dbClean}</div></div><a href="${openHref}">Open</a></div>`;
    }).join('');
}

function renderActivity(activity) {
    const target = document.getElementById('audit-list');
    if (!Array.isArray(activity) || activity.length === 0) {
        target.innerHTML = '<div class="log-item"><div style="color:var(--text-muted);">No activity yet</div></div>';
        return;
    }

    target.innerHTML = activity.slice(0, 8).map(item => `
        <div class="log-item">
            <div class="log-level">${item.level || 'INFO'}</div>
            <div style="flex:1;">
                <div style="color:white; font-size:0.82rem;">${item.message || '-'}</div>
                <div style="color:var(--text-muted); font-size:0.72rem;">${item.iso || '-'}</div>
            </div>
        </div>
    `).join('');
}

async function loadUserPanel() {
    const res = await fetch('/api/userpanel/overview');
    const data = await res.json();
    if (!res.ok) {
        throw new Error(data.detail || 'Unable to load profile overview');
    }
    panelData = data;
    const roleBadge = document.getElementById('panel-role-badge');
    if (roleBadge) {
        const tag = String(data.role_tag || (data.is_staff ? 'admin' : 'user')).toUpperCase();
        roleBadge.textContent = tag;
        roleBadge.classList.toggle('badge-admin', !!data.is_staff);
        roleBadge.classList.toggle('badge-user', !data.is_staff);
    }
    document.getElementById('panel-db').textContent = data.db_name || '-';
    renderStats(data.stats || {});
    renderContainers(data.containers || []);
    renderDatabases(data.databases || []);
    renderActivity(data.activity || []);
    await refresh2FAStatus();
}

async function containerAction(action, containerId) {
    const res = await fetch(`/api/containers/${action}/${containerId}`, { method: 'POST' });
    const payload = await res.json();
    if (!res.ok) {
        alert(payload.detail || `Action ${action} failed`);
        return;
    }
    await loadUserPanel();
}

async function openLogs(containerId, name) {
    document.getElementById('logs-title').textContent = name;
    document.getElementById('logs-content').textContent = 'Loading logs...';
    document.getElementById('logs-modal').style.display = 'flex';
    const res = await fetch(`/api/containers/logs/${containerId}?tail=250`);
    const payload = await res.json();
    if (!res.ok) {
        document.getElementById('logs-content').textContent = payload.detail || 'Logs unavailable';
        return;
    }
    document.getElementById('logs-content').textContent = payload.logs || 'No logs';
}

function closeLogsModal() {
    document.getElementById('logs-modal').style.display = 'none';
}

async function refresh2FAStatus() {
    const res = await fetch('/api/user/2fa/status');
    const payload = await res.json();
    twoFAEnabled = !!(res.ok && payload.enabled);
    document.getElementById('btn-2fa').innerHTML = twoFAEnabled
        ? '<i class="bi bi-shield-check"></i> 2FA Enabled'
        : '<i class="bi bi-shield-lock"></i> Enable 2FA';
}

function show2FAError(message) {
    const el = document.getElementById('twofa-error');
    el.textContent = message || 'Operation failed';
    el.style.display = 'block';
}

function clear2FAError() {
    const el = document.getElementById('twofa-error');
    el.textContent = '';
    el.style.display = 'none';
}

function close2FAModal() {
    document.getElementById('twofa-modal').style.display = 'none';
}

async function open2FAModal() {
    clear2FAError();
    document.getElementById('twofa-modal').style.display = 'flex';
    render2FAStatusChip(false, true);
    await refresh2FAStatus();
    render2FAStatusChip(twoFAEnabled, false);
    document.getElementById('twofa-disable-box').style.display = twoFAEnabled ? 'block' : 'none';
    document.getElementById('twofa-setup-box').style.display = twoFAEnabled ? 'none' : 'block';
    if (!twoFAEnabled) {
        await setup2FA();
    }
}

async function setup2FA() {
    clear2FAError();
    const res = await fetch('/api/user/2fa/setup', { method: 'POST' });
    const payload = await res.json();
    if (!res.ok) {
        show2FAError(payload.detail || 'Unable to start setup');
        return;
    }
    document.getElementById('twofa-secret').textContent = `Secret: ${payload.secret}`;
    const qrTarget = document.getElementById('twofa-qr');
    qrTarget.innerHTML = '';
    new QRCode(qrTarget, {
        text: payload.otpauth_uri,
        width: 204,
        height: 204
    });
}

async function confirm2FA() {
    clear2FAError();
    const code = sanitizeOtpInputValue(readSplitCode('twofa-code'));
    const res = await fetch('/api/user/2fa/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
    });
    const payload = await res.json();
    if (!res.ok) {
        show2FAError(payload.detail || 'Invalid code');
        return;
    }
    await refresh2FAStatus();
    render2FAStatusChip(true, false);
    document.getElementById('twofa-disable-box').style.display = 'block';
    document.getElementById('twofa-setup-box').style.display = 'none';
}

async function disable2FA() {
    clear2FAError();
    const code = sanitizeOtpInputValue(readSplitCode('twofa-disable-code'));
    const res = await fetch('/api/user/2fa/disable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
    });
    const payload = await res.json();
    if (!res.ok) {
        show2FAError(payload.detail || 'Invalid code');
        return;
    }
    await refresh2FAStatus();
    render2FAStatusChip(false, false);
    document.getElementById('twofa-disable-box').style.display = 'none';
    document.getElementById('twofa-setup-box').style.display = 'block';
    resetSplitCode('twofa-disable-code');
    await setup2FA();
}

document.getElementById('btn-2fa').addEventListener('click', open2FAModal);

document.addEventListener('DOMContentLoaded', async () => {
    bindSplitOtpInputs('twofa-code');
    bindSplitOtpInputs('twofa-disable-code');
    try {
        await loadUserPanel();
    } catch (e) {
        document.getElementById('containers-tbody').innerHTML = `<tr><td colspan="5" style="padding:20px;color:#ff6b6b;">${e.message}</td></tr>`;
    }
});
