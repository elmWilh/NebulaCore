const containersContext = window.NebulaContainers || {};
const isStaff = !!containersContext.isStaff;
let assignableUsers = [];
let selectedUsers = new Map();
let rolePermissionMatrix = {};
let roleCatalog = [];
const rolePermissionColumns = [
    { key: 'allow_explorer', label: 'Explorer' },
    { key: 'allow_root_explorer', label: 'Root Explorer' },
    { key: 'allow_console', label: 'Console' },
    { key: 'allow_shell', label: 'Shell' },
    { key: 'allow_settings', label: 'Settings' },
    { key: 'allow_edit_files', label: 'Edit Files' },
    { key: 'allow_edit_startup', label: 'Edit Startup' },
    { key: 'allow_edit_ports', label: 'Edit Ports' }
];
const defaultRolePermissions = {
    allow_explorer: true,
    allow_root_explorer: false,
    allow_console: true,
    allow_shell: false,
    allow_settings: false,
    allow_edit_files: false,
    allow_edit_startup: false,
    allow_edit_ports: false
};

async function loadAssignableUsers() {
    if (!isStaff) return;
    const usersRes = await fetch('/api/users/databases');
    if (!usersRes.ok) {
        assignableUsers = [];
        renderUserPicker('');
        return;
    }

    const dbPayload = await usersRes.json();
    const dbs = Array.isArray(dbPayload?.databases) ? dbPayload.databases : [];
    if (dbs.length === 0) {
        assignableUsers = [];
        renderUserPicker('');
        return;
    }

    assignableUsers = [];

    for (const db of dbs) {
        const uRes = await fetch(`/api/users/list?db_name=${encodeURIComponent(db)}`);
        if (!uRes.ok) continue;

        const users = await uRes.json();
        if (!Array.isArray(users)) continue;

        users
            .filter(u => !u.is_staff)
            .forEach(u => {
                assignableUsers.push({
                    username: u.username,
                    db,
                    role_tag: u.role_tag || 'user'
                });
            });
    }

    renderUserPicker('');
}

let metricsInterval = null;
let containersInterval = null;
let metricsAbortController = null;
let containersAbortController = null;
let metricsFailures = 0;
let previousNetworkTotal = null;
let hasContainerTableRendered = false;
let lastContainersSignature = '';
let currentContainers = [];
let consoleContainerId = null;
let consolePollInterval = null;
let deployJobId = null;
let deployPollInterval = null;
let activePreset = '';
let presetPermissionTemplates = {};
let activePresetConfig = {};

let containerPresets = {};

function getMetricsPollIntervalMs() {
    return document.hidden ? 15000 : 4000;
}

function getContainersPollIntervalMs() {
    return document.hidden ? 30000 : 15000;
}

function scheduleLiveTelemetry() {
    if (metricsInterval) clearInterval(metricsInterval);
    if (containersInterval) clearInterval(containersInterval);
    metricsInterval = setInterval(updateStats, getMetricsPollIntervalMs());
    containersInterval = setInterval(fetchContainers, getContainersPollIntervalMs());
}

function selectedUserKey(user) {
    return `${String(user?.db || 'system.db')}::${String(user?.username || '')}`;
}

function cloneRoleMatrix(source) {
    const out = {};
    const src = source && typeof source === 'object' ? source : {};
    const activeRoles = roleCatalog.length ? roleCatalog : ['user'];
    activeRoles.forEach(role => {
        out[role] = {};
        rolePermissionColumns.forEach(col => {
            const fallback = !!defaultRolePermissions[col.key];
            out[role][col.key] = !!(src[role] && src[role][col.key] !== undefined ? src[role][col.key] : fallback);
        });
    });
    return out;
}

async function loadRoleCatalog() {
    try {
        const res = await fetch('/api/roles/list');
        const data = await res.json();
        roleCatalog = Array.isArray(data) ? data.map(r => String(r.name || '').trim()).filter(Boolean) : [];
    } catch (e) {
        roleCatalog = [];
    }
    if (!roleCatalog.includes('user')) roleCatalog.unshift('user');
}

function renderRolePermissionMatrix() {
    const host = document.getElementById('role_permissions_grid');
    if (!host) return;
    if (!rolePermissionMatrix || Object.keys(rolePermissionMatrix).length === 0) {
        rolePermissionMatrix = cloneRoleMatrix({});
    }
    const roles = Object.keys(rolePermissionMatrix);
    let html = '<table style="width:100%; border-collapse: collapse; font-size: 0.78rem;"><thead><tr>';
    html += '<th style="text-align:left; padding:8px 10px; border-bottom:1px solid var(--border);">Role</th>';
    rolePermissionColumns.forEach(col => {
        html += `<th style="text-align:center; padding:8px 10px; border-bottom:1px solid var(--border);">${escapeHtml(col.label)}</th>`;
    });
    html += '</tr></thead><tbody>';
    roles.forEach(role => {
        html += `<tr><td style="padding:8px 10px; border-bottom:1px solid var(--border); color:#d6d6df; font-weight:600;">${escapeHtml(role)}</td>`;
        rolePermissionColumns.forEach(col => {
            const checked = rolePermissionMatrix[role][col.key] ? 'checked' : '';
            html += `<td style="text-align:center; padding:8px 10px; border-bottom:1px solid var(--border);"><input class="role-perm-check" type="checkbox" data-role="${escapeAttr(role)}" data-key="${escapeAttr(col.key)}" ${checked} onchange="onRolePermissionToggle(this)"></td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table>';
    host.innerHTML = html;
}

function onRolePermissionToggle(el) {
    const role = String(el?.dataset?.role || '');
    const key = String(el?.dataset?.key || '');
    if (!role || !key) return;
    if (!rolePermissionMatrix[role]) rolePermissionMatrix[role] = {};
    rolePermissionMatrix[role][key] = !!el.checked;
}

async function loadContainerPresets() {
    const renderSelect = () => {
        const select = document.getElementById('cont_preset_select');
        if (!select) return;
        select.innerHTML = '';
        Object.keys(containerPresets).sort().forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        });
        if (select.options.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No presets found';
            select.appendChild(opt);
            const hint = document.getElementById('preset_hint');
            if (hint) hint.textContent = 'No presets in containers/presets. Add JSON files or save one from this form.';
        }
        select.value = activePreset;
    };
    renderSelect();
    try {
        const res = await fetch('/api/containers/presets');
        if (!res.ok) return;
        const presets = await res.json();
        if (!Array.isArray(presets)) return;
        const merged = {};
        Object.entries(containerPresets || {}).forEach(([k, v]) => { merged[k] = v; });
        presets.forEach(p => {
            const name = String(p?.name || '').trim();
            if (!name) return;
            if (p?.config && typeof p.config === 'object') {
                merged[name] = p.config;
            }
            if (p?.permissions && typeof p.permissions === 'object') {
                presetPermissionTemplates[name] = cloneRoleMatrix(p.permissions);
            }
        });
        containerPresets = merged;

        const select = document.getElementById('cont_preset_select');
        if (select) {
            select.innerHTML = '';
            presets.forEach(p => {
                const opt = document.createElement('option');
                const name = String(p?.name || '').trim();
                if (!name) return;
                opt.value = name;
                opt.textContent = p?.title ? `${p.title} (${name})` : name;
                select.appendChild(opt);
            });
            if (select.options.length === 0) renderSelect();
        }
    } catch (e) {
        // keep built-in presets only
    }
}

function applySelectedPreset() {
    const select = document.getElementById('cont_preset_select');
    const name = String(select?.value || '').trim() || activePreset;
    if (!name) return;
    applyContainerPreset(name);
}

async function saveCurrentAsPreset() {
    if (!isStaff) return;
    const name = String(document.getElementById('preset_save_name')?.value || '').trim();
    if (!name) {
        alert('Preset name is required');
        return;
    }
    const title = String(document.getElementById('preset_save_title')?.value || '').trim();
    const config = collectContainerForm();
    const payload = {
        name,
        title: title || name,
        description: 'Saved from Container Architect',
        config,
        permissions: rolePermissionMatrix
    };
    try {
        const res = await fetch('/api/containers/presets', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const out = await res.json();
        if (!res.ok) {
            alert(out.detail || 'Failed to save preset');
            return;
        }
        await loadContainerPresets();
        const select = document.getElementById('cont_preset_select');
        if (select) select.value = out.name || name;
        const hint = document.getElementById('preset_hint');
        if (hint) hint.textContent = `Preset '${out.name || name}' saved`;
    } catch (e) {
        alert('Failed to save preset');
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttr(value) {
    return escapeHtml(value).replaceAll('`', '&#96;');
}

function jsQuote(value) {
    return String(value ?? '')
        .replaceAll('\\', '\\\\')
        .replaceAll("'", "\\'")
        .replaceAll('\n', '\\n')
        .replaceAll('\r', '\\r');
}

function safeDomId(value) {
    return String(value ?? '').replace(/[^a-zA-Z0-9_-]/g, '_');
}

function setFieldValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = value === null || value === undefined ? '' : String(value);
}

function applyContainerPreset(presetName) {
    const preset = containerPresets[presetName];
    if (!preset) return;

    activePreset = presetName;
    activePresetConfig = preset;
    const select = document.getElementById('cont_preset_select');
    if (select) select.value = presetName;
    setFieldValue('cont_ram_input', preset.ram);
    setFieldValue('cont_swap', preset.swap);
    setFieldValue('cont_disk', preset.disk);
    setFieldValue('cont_cpu', preset.cpu);
    setFieldValue('cont_cpu_limit', preset.cpu_limit);
    setFieldValue('cont_cpuset', preset.cpuset);
    setFieldValue('cont_cpu_quota', preset.cpu_quota);
    setFieldValue('cont_cpu_period', preset.cpu_period);
    setFieldValue('cont_pids_limit', preset.pids_limit);
    setFieldValue('cont_shm', preset.shm);
    const restartEl = document.getElementById('cont_restart');
    if (restartEl) restartEl.checked = !!preset.restart;
    if (typeof preset.image === 'string') setFieldValue('cont_image', preset.image);
    if (typeof preset.ports === 'string') setFieldValue('cont_ports', preset.ports);
    if (typeof preset.command === 'string') setFieldValue('cont_command', preset.command);
    if (typeof preset.env === 'string') setFieldValue('cont_env', preset.env);
    if (typeof preset.volumes === 'string') setFieldValue('cont_volumes', preset.volumes);

    const hint = document.getElementById('preset_hint');
    if (hint) hint.textContent = `${presetName.replace('-', ' ')} profile applied`;
    if (presetPermissionTemplates[presetName]) {
        rolePermissionMatrix = cloneRoleMatrix(presetPermissionTemplates[presetName]);
        renderRolePermissionMatrix();
    }
}

function toggleUserSelection(user) {
    const key = selectedUserKey(user);
    if (selectedUsers.has(key)) selectedUsers.delete(key);
    else selectedUsers.set(key, { username: user.username, db: user.db, role_tag: user.role_tag || 'user' });
    renderUserPicker(document.getElementById('cont_user_search')?.value || '');
}

function renderSelectedUsers() {
    const target = document.getElementById('cont_users_selected');
    if (!target) return;
    target.innerHTML = '';
    if (selectedUsers.size === 0) {
        target.innerHTML = '<span style="font-size:0.75rem; color: var(--text-muted);">No users selected</span>';
        return;
    }
    Array.from(selectedUsers.values())
        .sort((a, b) => `${a.username}:${a.db}`.localeCompare(`${b.username}:${b.db}`))
        .forEach(u => {
        const chip = document.createElement('span');
        chip.className = 'user-chip';
        chip.textContent = `${u.username} [${u.db}] (${u.role_tag || 'user'})`;
        target.appendChild(chip);
    });
}

function renderUserPicker(searchText) {
    const listEl = document.getElementById('cont_users_list');
    if (!listEl) return;
    listEl.innerHTML = '';
    const query = (searchText || '').toLowerCase().trim();
    const filtered = assignableUsers.filter(u => {
        const label = `${u.username} ${u.db}`.toLowerCase();
        return !query || label.includes(query);
    });

    if (filtered.length === 0) {
        listEl.innerHTML = '<div style="padding:10px; color: var(--text-muted); font-size:0.8rem;">No users found</div>';
        renderSelectedUsers();
        return;
    }

    filtered.forEach(user => {
        const row = document.createElement('div');
        row.className = `user-option${selectedUsers.has(selectedUserKey(user)) ? ' is-selected' : ''}`;
        row.onclick = () => toggleUserSelection(user);
        const usernameHtml = escapeHtml(user.username);
        const dbHtml = escapeHtml(user.db);
        row.innerHTML = `
            <div>${usernameHtml}</div>
            <div style="font-size:0.72rem; color: var(--text-muted);">${dbHtml}</div>
        `;
        listEl.appendChild(row);
    });
    renderSelectedUsers();
}

function asNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function applyHealthState(status, pressure) {
    const el = document.getElementById('stat_health');
    el.classList.remove('health-optimal', 'health-stable', 'health-elevated', 'health-critical');

    const normalized = (status || 'optimal').toLowerCase();
    if (normalized === 'critical') {
        el.classList.add('health-critical');
    } else if (normalized === 'elevated') {
        el.classList.add('health-elevated');
    } else if (normalized === 'stable') {
        el.classList.add('health-stable');
    } else {
        el.classList.add('health-optimal');
    }

    const pressureText = Number.isFinite(pressure) ? `pressure ${pressure.toFixed(1)}%` : 'telemetry unavailable';
    el.innerHTML = `${normalized[0].toUpperCase()}${normalized.slice(1)} <span id="stat_health_sub" class="stat-sub">${pressureText}</span>`;
}

function startLiveTelemetry() {
    fetchContainers(true);
    updateStats();
    scheduleLiveTelemetry();
}

function onVisibilityChanged() {
    scheduleLiveTelemetry();
    if (!document.hidden) {
        updateStats();
        fetchContainers(false);
    }
}

function normalizeNode(raw) {
    if (typeof raw === 'string') {
        return { id: raw, label: raw.toUpperCase(), status: 'active' };
    }
    return {
        id: raw?.id || raw?.name || 'node',
        label: raw?.label || raw?.name || raw?.id || 'Unknown node',
        status: (raw?.status || 'active').toLowerCase()
    };
}

async function initPage() {
    try {
        startLiveTelemetry();

        if (isStaff) {
            const nodeRes = await fetch('/api/containers/nodes');
            const nodeData = await nodeRes.json();
            const selector = document.getElementById('node_selector');
            if (selector) {
                selector.innerHTML = '';

                const nodes = Array.isArray(nodeData?.nodes) ? nodeData.nodes : [];
                const activeNodeId = nodeData?.active_node || (nodes[0]?.id || nodes[0]);

                nodes.map(normalizeNode).forEach(node => {
                    const opt = document.createElement('option');
                    opt.value = node.id;
                    opt.disabled = node.status !== 'active';
                    opt.textContent = `${node.label} (${node.status === 'active' ? 'Active' : 'Offline'})`;
                    if (node.id === activeNodeId) opt.selected = true;
                    selector.appendChild(opt);
                });
                fetchContainers(false);
            }
        }

        Promise.allSettled([
            loadRoleCatalog(),
            loadContainerPresets(),
            loadAssignableUsers()
        ]).then(() => {
            rolePermissionMatrix = cloneRoleMatrix({});
            renderRolePermissionMatrix();
        });
    } catch (e) { console.error("Nebula API Failure", e); }
}

async function updateStats() {
    try {
        if (metricsAbortController) metricsAbortController.abort();
        metricsAbortController = new AbortController();
        const res = await fetch('/api/metrics', { signal: metricsAbortController.signal, cache: 'no-store' });
        if (!res.ok) throw new Error('metrics unavailable');
        const data = await res.json();

        const ramUsed = asNumber(data.ram_used_gb, 0);
        const ramTotal = asNumber(data.ram_total_gb, 0);
        const ramPercent = asNumber(data.ram_percent, 0);
        const cpuPercent = asNumber(data.cpu_percent, 0);
        const cpuCoresTotal = asNumber(data.cpu_cores_total, 1);
        const cpuCoresActive = asNumber(data.cpu_cores_active, 0);
        const netUp = asNumber(data.network_sent_mb, 0);
        const netDown = asNumber(data.network_recv_mb, 0);
        const netTotal = netUp + netDown;
        const trendIcon = previousNetworkTotal === null ? '•' : (netTotal >= previousNetworkTotal ? '↑' : '↓');
        previousNetworkTotal = netTotal;
        const activeContainers = asNumber(data.active_containers, 0);
        const totalContainers = asNumber(data.containers, activeContainers);

        document.getElementById('stat_active_containers').innerHTML = `${activeContainers} <span class="stat-sub">running / ${totalContainers} total</span>`;
        document.getElementById('stat_ram').innerHTML = `${ramUsed.toFixed(1)} GB <span class="stat-sub">/ ${ramTotal.toFixed(1)} GB (${ramPercent.toFixed(1)}%)</span>`;
        document.getElementById('stat_cpu').innerHTML = `${cpuCoresActive.toFixed(1)} / ${cpuCoresTotal} <span class="stat-sub">${cpuPercent.toFixed(1)}% busy</span>`;
        document.getElementById('stat_net').innerHTML = `↑ ${netUp.toFixed(2)} ↓ ${netDown.toFixed(2)} <span class="stat-sub">${trendIcon} total ${netTotal.toFixed(2)} MB/s</span>`;

        const healthPressure = Math.max(cpuPercent, ramPercent, asNumber(data.disk_percent, 0));
        applyHealthState(data.health_status, healthPressure);
        metricsFailures = 0;
    } catch (e) {
        if (e && e.name === 'AbortError') return;
        metricsFailures += 1;
        if (metricsFailures < 3) {
            document.getElementById('stat_active_containers').innerHTML = `— <span class="stat-sub">sync delayed</span>`;
            return;
        }
        document.getElementById('stat_active_containers').innerHTML = `— <span class="stat-sub">telemetry offline</span>`;
        document.getElementById('stat_ram').innerHTML = `— <span class="stat-sub">telemetry offline</span>`;
        document.getElementById('stat_cpu').innerHTML = `— <span class="stat-sub">telemetry offline</span>`;
        document.getElementById('stat_net').innerHTML = `— <span class="stat-sub">telemetry offline</span>`;
        applyHealthState('critical', NaN);
    }
}

function updateActiveContainersStat(containers) {
    const total = containers.length;
    const running = containers.filter(c => (c.status || '').toLowerCase() === 'running').length;
    document.getElementById('stat_active_containers').innerHTML = `${running} <span class="stat-sub">running / ${total} total</span>`;
}

function onNodeChanged() {
    hasContainerTableRendered = false;
    lastContainersSignature = '';
    fetchContainers(true);
}

async function fetchContainers(showLoader = false) {
    const tbody = document.getElementById('container_table_body');
    const selectedNode = document.getElementById('node_selector')?.value || '';
    const listUrl = selectedNode ? `/api/containers/list?node=${encodeURIComponent(selectedNode)}` : '/api/containers/list';
    if (!hasContainerTableRendered || showLoader) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:60px;"><i class="bi bi-arrow-repeat spin" style="font-size:2rem; color:var(--accent);"></i></td></tr>';
    }

    try {
        if (containersAbortController) containersAbortController.abort();
        containersAbortController = new AbortController();
        const response = await fetch(listUrl, { signal: containersAbortController.signal, cache: 'no-store' });
        const containers = await response.json();

        if (!response.ok) {
            const detail = (containers && containers.detail) ? containers.detail : 'Failed to sync with Nebula Core';
            tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:40px; color:#ff4f4f;">${escapeHtml(detail)}</td></tr>`;
            hasContainerTableRendered = true;
            return;
        }

        if (!Array.isArray(containers) || containers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:40px; color:var(--text-muted);">No active containers on this node</td></tr>';
            hasContainerTableRendered = true;
            currentContainers = [];
            lastContainersSignature = '[]';
            updateActiveContainersStat([]);
            return;
        }

        updateActiveContainersStat(containers);
        const signature = JSON.stringify(containers.map(c => [c.id, c.name, c.status, c.image, (c.users || []).join(',')]));
        if (signature === lastContainersSignature && hasContainerTableRendered) {
            currentContainers = containers;
            return;
        }

        currentContainers = containers;
        lastContainersSignature = signature;
        tbody.innerHTML = '';

        containers.forEach(cont => {
            const row = document.createElement('tr');
            row.className = "table-row-hover";
            row.style.borderBottom = '1px solid var(--border)';
            row.style.cursor = 'pointer';
            const containerId = String(cont.id ?? '');
            const containerName = String(cont.name || containerId);
            const containerStatus = String(cont.status || 'unknown').toLowerCase();
            const containerImage = String(cont.image || 'unknown');
            const containerIdPath = encodeURIComponent(containerId);
            const encodedName = encodeURIComponent(containerName);
            const menuId = `drop-${safeDomId(containerId)}`;
            const containerIdJs = jsQuote(containerId);
            const color = containerStatus === 'running' ? '#4ade80' : '#ff4f4f';
            const usersHtml = (cont.users || []).map((u, i) => {
                const username = String(u || '');
                const displayLetter = escapeHtml((username[0] || '?').toUpperCase());
                return `<div class="user-avatar-mini" style="margin-left: ${i === 0 ? '0' : '-8px'}; background: ${stringToColor(username)}" title="${escapeAttr(username)}">${displayLetter}</div>`;
            }).join('');
            row.onclick = (event) => {
                if (event.target.closest('.dropdown') || event.target.closest('a') || event.target.closest('button')) return;
                window.location.href = `/containers/view/${containerIdPath}`;
            };

            row.innerHTML = `
                <td style="padding: 18px 24px;">
                    <div style="display:flex; align-items:center; gap:16px;">
                        <div class="container-icon-bg"><i class="bi bi-box-seam" style="color:var(--accent);"></i></div>
                        <div>
                            <div style="font-weight:700; color:white; font-size:0.9rem;">
                                <a href="/containers/view/${containerIdPath}" style="color:inherit; text-decoration:none;">${escapeHtml(containerName)}</a>
                            </div>
                            <div style="font-size:0.75rem; color:var(--text-muted);">ID: ${escapeHtml(containerId)}</div>
                        </div>
                    </div>
                </td>
                <td style="padding: 18px 24px;">
                    <span class="badge badge-user">${escapeHtml(containerImage)}</span>
                </td>
                <td style="padding: 18px 24px;">
                    <div style="display:flex;">${usersHtml}</div>
                </td>
                <td style="padding: 18px 24px;">
                    <div style="display:flex; align-items:center; gap:10px; color:${color}; font-size:0.8rem; font-weight:700;">
                        <div class="pulse-dot" style="background:${color}"></div> 
                        ${escapeHtml(containerStatus.toUpperCase())}
                    </div>
                </td>
                <td style="padding: 18px 24px; text-align: right;">
                    <div class="dropdown">
                        <button class="action-btn" onclick="toggleMenu(event, '${menuId}')">
                            <i class="bi bi-three-dots"></i>
                        </button>
                        <div id="${menuId}" class="dropdown-content">
                            <a href="/containers/view/${containerIdPath}"><i class="bi bi-window-sidebar"></i> Container Mode</a>
                            <a href="javascript:void(0)" onclick="openConsoleModal('${containerIdJs}', decodeURIComponent('${encodedName}')); return false;"><i class="bi bi-terminal"></i> Console</a>
                            <a href="javascript:void(0)" onclick="startContainer('${containerIdJs}'); return false;"><i class="bi bi-play-fill"></i> Start</a>
                            <a href="javascript:void(0)" onclick="stopContainer('${containerIdJs}'); return false;"><i class="bi bi-stop-fill"></i> Stop</a>
                            <a href="javascript:void(0)" onclick="restartContainer('${containerIdJs}'); return false;"><i class="bi bi-arrow-clockwise"></i> Restart</a>
                            ${isStaff ? `
                            <hr style="border:0; border-top:1px solid var(--border); margin:6px 0;">
                            <a href="javascript:void(0)" style="color:#ff6b6b;" onclick="deleteContainer('${containerIdJs}'); return false;"><i class="bi bi-trash3"></i> Delete</a>
                            ` : ''}
                        </div>
                    </div>
                </td>
            `;
            tbody.appendChild(row);
        });
        hasContainerTableRendered = true;
    } catch (e) {
        if (e && e.name === 'AbortError') return;
        if (!hasContainerTableRendered) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:40px; color:#ff4f4f;">Failed to sync with Nebula Core</td></tr>';
            hasContainerTableRendered = true;
        }
    }
}

async function restartContainer(containerId) {
    if (!confirm(`Restart container ${containerId}?`)) return;
    try {
        const res = await fetch(`/api/containers/restart/${containerId}`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            alert(`Restart failed: ${err.detail || 'Unknown error'}`);
            return;
        }
        fetchContainers(true);
    } catch (e) {
        alert('Failed to restart container');
    }
}

async function startContainer(containerId) {
    try {
        const res = await fetch(`/api/containers/start/${containerId}`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            alert(`Start failed: ${err.detail || 'Unknown error'}`);
            return;
        }
        fetchContainers(true);
    } catch (e) {
        alert('Failed to start container');
    }
}

async function stopContainer(containerId) {
    try {
        const res = await fetch(`/api/containers/stop/${containerId}`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            alert(`Stop failed: ${err.detail || 'Unknown error'}`);
            return;
        }
        fetchContainers(true);
    } catch (e) {
        alert('Failed to stop container');
    }
}

async function deleteContainer(containerId) {
    const confirmed = confirm(
        `Delete container ${containerId}?\n\nThis action is destructive and can stop services permanently.`
    );
    if (!confirmed) return;

    try {
        const res = await fetch(`/api/containers/delete/${containerId}`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            alert(`Delete failed: ${err.detail || 'Unknown error'}`);
            return;
        }
        fetchContainers(true);
    } catch (e) {
        alert('Failed to delete container');
    }
}

async function refreshConsoleLogs() {
    if (!consoleContainerId) return;
    const output = document.getElementById('consoleOutput');
    const status = document.getElementById('consoleStatus');
    try {
        status.textContent = 'Fetching latest logs...';
        const res = await fetch(`/api/containers/logs/${consoleContainerId}?tail=300`);
        const data = await res.json();
        if (!res.ok) {
            output.textContent = '';
            status.textContent = `Log stream error: ${data.detail || 'Unknown error'}`;
            return;
        }
        output.textContent = data.logs || '';
        output.scrollTop = output.scrollHeight;
        status.textContent = `Updated: ${new Date().toLocaleTimeString()}`;
    } catch (e) {
        status.textContent = 'Failed to fetch logs';
    }
}

function openConsoleModal(containerId, containerName) {
    consoleContainerId = containerId;
    document.getElementById('consoleTitle').textContent = `Container Console: ${containerName}`;
    document.getElementById('consoleSub').textContent = `Container ID: ${containerId}`;
    document.getElementById('consoleModal').style.display = 'flex';
    if (consolePollInterval) clearInterval(consolePollInterval);
    refreshConsoleLogs();
    consolePollInterval = setInterval(refreshConsoleLogs, 4000);
}

function closeConsoleModal() {
    document.getElementById('consoleModal').style.display = 'none';
    if (consolePollInterval) clearInterval(consolePollInterval);
    consolePollInterval = null;
    consoleContainerId = null;
}

function openDeployProgressModal() {
    document.getElementById('deployProgressModal').style.display = 'flex';
    document.getElementById('deployProgressFill').style.width = '0%';
    document.getElementById('deployProgressText').textContent = '0%';
    document.getElementById('deployStage').textContent = 'Preparing deployment...';
    document.getElementById('deployLogOutput').textContent = '';
}

function closeDeployProgressModal() {
    document.getElementById('deployProgressModal').style.display = 'none';
    if (deployPollInterval) clearInterval(deployPollInterval);
    deployPollInterval = null;
    deployJobId = null;
}

function openDeployErrorModal(errorPayload, logs) {
    const payload = (errorPayload && typeof errorPayload === 'object') ? errorPayload : {
        title: 'Deployment Error',
        summary: String(errorPayload || 'Unknown deployment error'),
        hint: '',
        code: 'deploy_failed',
        raw_error: String(errorPayload || ''),
    };
    document.getElementById('deployErrorSummary').textContent = payload.summary || payload.title || 'Deployment failed';
    document.getElementById('deployErrorHint').textContent = payload.hint || 'Open raw log for details.';
    const raw = payload.raw_error || '';
    const logText = Array.isArray(logs) ? logs.join('\n') : '';
    document.getElementById('deployErrorRaw').textContent = [raw, '', logText].filter(Boolean).join('\n');
    document.getElementById('deployErrorModal').style.display = 'flex';
}

function closeDeployErrorModal() {
    document.getElementById('deployErrorModal').style.display = 'none';
}

async function pollDeployStatus() {
    if (!deployJobId) return;
    try {
        const res = await fetch(`/api/containers/deploy/status/${deployJobId}`);
        const data = await res.json();
        if (!res.ok) {
            document.getElementById('deployStage').textContent = data.detail || 'Failed to fetch deployment status';
            return;
        }

        document.getElementById('deployProgressFill').style.width = `${Math.min(100, Math.max(0, data.progress || 0))}%`;
        document.getElementById('deployProgressText').textContent = `${data.progress || 0}%`;
        document.getElementById('deployStage').textContent = data.stage || 'Deploying...';
        document.getElementById('deployLogOutput').textContent = (data.logs || []).join('\n');

        if (data.status === 'success') {
            if (deployPollInterval) clearInterval(deployPollInterval);
            deployPollInterval = null;
            closeModal();
            fetchContainers(true);
        } else if (data.status === 'failed') {
            if (deployPollInterval) clearInterval(deployPollInterval);
            deployPollInterval = null;
            const errorPayload = data.error || { summary: 'Deployment failed' };
            const summary = (typeof errorPayload === 'object')
                ? (errorPayload.summary || errorPayload.title || 'Deployment failed')
                : String(errorPayload || 'Deployment failed');
            document.getElementById('deployStage').textContent = summary;
            openDeployErrorModal(errorPayload, data.logs || []);
        }
    } catch (e) {
        document.getElementById('deployStage').textContent = 'Connection error during deployment monitoring';
    }
}

async function saveContainer() {
    if (!isStaff) return;
    const btn = document.getElementById('saveBtn');
    const data = collectContainerForm();
    data.users = Array.from(selectedUsers.values()).map(u => u.username);
    data.user_assignments = Array.from(selectedUsers.values()).map(u => ({
        username: u.username,
        db_name: u.db || 'system.db',
        role_tag: u.role_tag || 'user'
    }));
    data.role_permissions = rolePermissionMatrix;

    if(!data.name || !data.image) { alert("Instance name and Image are required"); return; }

    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-arrow-repeat spin"></i> Deploying...';

    try {
        openDeployProgressModal();
        const res = await fetch('/api/containers/deploy/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });

        if (res.ok) {
            const out = await res.json();
            deployJobId = out.job_id;
            if (deployPollInterval) clearInterval(deployPollInterval);
            deployPollInterval = setInterval(pollDeployStatus, 1000);
            await pollDeployStatus();
        } else {
            const err = await res.json();
            closeDeployProgressModal();
            const errorPayload = (err && err.detail) ? err.detail : { summary: 'Deployment failed', raw_error: JSON.stringify(err || {}) };
            openDeployErrorModal(errorPayload, []);
        }
    } catch (e) {
        closeDeployProgressModal();
        openDeployErrorModal({
            title: 'Connection Error',
            summary: 'Fatal connection error during deployment request.',
            hint: 'Check GUI/Core connectivity.',
            raw_error: String(e && e.message ? e.message : e),
            code: 'deploy_transport_error'
        }, []);
    } finally {
        btn.disabled = false;
        btn.innerHTML = 'Deploy Instance';
    }
}

function collectContainerForm() {
    return {
        name: document.getElementById('cont_name').value,
        image: document.getElementById('cont_image').value,
        ram: parseInt(document.getElementById('cont_ram_input').value),
        swap: parseInt(document.getElementById('cont_swap').value),
        disk: parseInt(document.getElementById('cont_disk').value),
        cpu: parseInt(document.getElementById('cont_cpu').value),
        cpu_limit: parseFloat(document.getElementById('cont_cpu_limit').value),
        cpuset: document.getElementById('cont_cpuset').value,
        cpu_quota: parseInt(document.getElementById('cont_cpu_quota').value),
        cpu_period: parseInt(document.getElementById('cont_cpu_period').value),
        pids_limit: parseInt(document.getElementById('cont_pids_limit').value),
        shm: parseInt(document.getElementById('cont_shm').value),
        ports: document.getElementById('cont_ports').value,
        command: document.getElementById('cont_command').value,
        env: document.getElementById('cont_env').value,
        volumes: document.getElementById('cont_volumes').value,
        workspace_mount: activePresetConfig.workspace_mount || '',
        explorer_root: activePresetConfig.explorer_root || '',
        console_cwd: activePresetConfig.console_cwd || '',
        profile_name: activePresetConfig.profile_name || activePreset || '',
        restart: document.getElementById('cont_restart').checked,
        preset: activePreset
    };
}

function stringToColor(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) { hash = str.charCodeAt(i) + ((hash << 5) - hash); }
    return `hsl(${hash % 360}, 60%, 60%)`;
}

function toggleMenu(e, id) {
    e.stopPropagation();
    const menu = document.getElementById(id);
    document.querySelectorAll('.dropdown-content').forEach(d => { if(d !== menu) d.classList.remove('show'); });
    menu.classList.toggle('show');
}

function openCreateModal() {
    if (!isStaff) return;
    selectedUsers = new Map();
    const search = document.getElementById('cont_user_search');
    if (search) search.value = '';
    const presetName = document.getElementById('preset_save_name');
    const presetTitle = document.getElementById('preset_save_title');
    if (presetName) presetName.value = '';
    if (presetTitle) presetTitle.value = '';
    document.getElementById('cont_name').value = '';
    document.getElementById('cont_image').value = 'ubuntu:latest';
    document.getElementById('cont_ports').value = '';
    document.getElementById('cont_command').value = '';
    document.getElementById('cont_env').value = '';
    document.getElementById('cont_volumes').value = '';
    activePresetConfig = {};
    rolePermissionMatrix = cloneRoleMatrix({});
    renderRolePermissionMatrix();
    const select = document.getElementById('cont_preset_select');
    const firstPreset = select && select.options.length > 0 ? select.options[0].value : '';
    if (firstPreset) {
        applyContainerPreset(firstPreset);
    }
    renderUserPicker('');
    document.getElementById('containerModal').style.display = 'flex';
}

function closeModal() { document.getElementById('containerModal').style.display = 'none'; }

window.onclick = (e) => {
    document.querySelectorAll('.dropdown-content').forEach(d => d.classList.remove('show'));
    const containerModal = document.getElementById('containerModal');
    const consoleModal = document.getElementById('consoleModal');
    const deployModal = document.getElementById('deployProgressModal');
    const deployErrorModal = document.getElementById('deployErrorModal');
    if (containerModal && e.target === containerModal) closeModal();
    if (consoleModal && e.target === consoleModal) closeConsoleModal();
    if (deployModal && e.target === deployModal) closeDeployProgressModal();
    if (deployErrorModal && e.target === deployErrorModal) closeDeployErrorModal();
};
document.addEventListener('DOMContentLoaded', initPage);
document.addEventListener('visibilitychange', onVisibilityChanged);
window.addEventListener('beforeunload', () => {
    if (metricsInterval) clearInterval(metricsInterval);
    if (containersInterval) clearInterval(containersInterval);
    if (consolePollInterval) clearInterval(consolePollInterval);
    if (deployPollInterval) clearInterval(deployPollInterval);
    if (metricsAbortController) metricsAbortController.abort();
    if (containersAbortController) containersAbortController.abort();
    document.removeEventListener('visibilitychange', onVisibilityChanged);
});
