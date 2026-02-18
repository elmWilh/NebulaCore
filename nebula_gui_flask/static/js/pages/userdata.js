// nebula_gui_flask/static/js/pages/userdata.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

const userDataContext = window.NebulaUserdata || {};
const profileUsername = userDataContext.profileUsername || '';
const profileDbName = userDataContext.profileDbName || '';

function statusPill(status) {
    return String(status || '').toLowerCase() === 'running'
        ? '<span class="status-pill online">Online</span>'
        : '<span class="status-pill offline">Stopped</span>';
}

async function callContainerAction(action, id) {
    const res = await fetch(`/api/containers/${action}/${id}`, { method: 'POST' });
    const payload = await res.json();
    if (!res.ok) {
        alert(payload.detail || `Action ${action} failed`);
        return;
    }
    await loadProfileData();
}

async function loadProfileData() {
    const detailRes = await fetch(`/api/users/detail/${encodeURIComponent(profileUsername)}?db_name=${encodeURIComponent(profileDbName)}`);
    const detail = await detailRes.json();
    if (!detailRes.ok) {
        alert(detail.detail || 'Failed to load user details');
        return;
    }

    document.getElementById('profile-uuid').textContent = detail.id;
    document.getElementById('profile-db').textContent = detail.db_name || profileDbName;
    document.getElementById('profile-email').textContent = detail.email || '-';
    document.getElementById('profile-root').textContent = `/home/cloud_sys/users/${detail.username}`;
    document.getElementById('profile-role').textContent = detail.role_tag || (detail.is_staff ? 'admin' : 'user');

    const metricsRes = await fetch('/api/metrics');
    const metrics = await metricsRes.json();
    if (metricsRes.ok) {
        document.getElementById('stat-cpu').textContent = metrics.cpu || '0%';
        document.getElementById('stat-ram').textContent = metrics.ram || '0%';
    }

    const containersRes = await fetch('/api/containers/list');
    const containers = await containersRes.json();
    const tbody = document.getElementById('instances-body');
    if (!containersRes.ok || !Array.isArray(containers)) {
        tbody.innerHTML = '<tr><td colspan="4" style="padding:14px;color:#ff6b6b;">Failed to load containers</td></tr>';
        return;
    }

    const running = containers.filter(c => String(c.status || '').toLowerCase() === 'running').length;
    document.getElementById('stat-containers').textContent = containers.length;
    document.getElementById('stat-running').textContent = running;
    document.getElementById('instance-count').textContent = `${containers.length} visible`;

    if (containers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="padding:14px;color:var(--text-muted);">No instances available</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    containers.forEach(cont => {
        const isRunning = String(cont.status || '').toLowerCase() === 'running';
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><strong>${cont.name}</strong><div style="font-size:0.72rem; color: var(--text-muted);">${cont.id}</div></td>
            <td>${cont.image || '-'}</td>
            <td>${statusPill(cont.status)}</td>
            <td style="text-align:right;"><button class="mini-btn" onclick="callContainerAction('${isRunning ? 'stop' : 'start'}','${cont.id}')">${isRunning ? 'Stop' : 'Start'}</button></td>
        `;
        tbody.appendChild(row);
    });
}

document.addEventListener('DOMContentLoaded', loadProfileData);
