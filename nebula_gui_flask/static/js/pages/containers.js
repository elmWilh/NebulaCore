// nebula_gui_flask/static/js/pages/containers.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

const containersContext = window.NebulaContainers || {};
const isStaff = !!containersContext.isStaff;
const containersSocket = (typeof window.io === 'function') ? window.io() : null;
const CONTAINERS_SORT_STORAGE_KEY = 'nebula-containers-sort-v1';
const PINNED_CONTAINERS_STORAGE_KEY = 'nebula-pinned-containers-v1';
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
const MANAGED_WORKSPACE_HINT = 'storage/container_workspaces';
const PANEL_GROUP_HINT = 'nebulapanel';
let architectProtocolDefinitions = {};
let websiteWizardDefinitions = {};

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

function initContainerSocket() {
    if (!containersSocket) return;
    containersSocket.on('container_stream_status', (data) => {
        if (consoleMode !== 'logs') return;
        if (!consoleContainerId || String(data?.container_id || '') !== String(consoleContainerId)) return;
        const status = document.getElementById('consoleStatus');
        if (!status) return;
        status.textContent = data?.state === 'connecting'
            ? 'Connecting live stream...'
            : String(data?.detail || data?.state || 'stream update');
    });
    containersSocket.on('container_stream_update', (data) => {
        if (consoleMode !== 'logs') return;
        if (!consoleContainerId || String(data?.container_id || '') !== String(consoleContainerId)) return;
        const output = document.getElementById('consoleOutput');
        const status = document.getElementById('consoleStatus');
        if (output) {
            output.textContent = String(data?.logs || '');
            output.scrollTop = output.scrollHeight;
        }
        if (status) status.textContent = `Live stream: ${new Date().toLocaleTimeString()}`;
    });
    containersSocket.on('container_attach_ack', (data) => {
        if (!consoleContainerId || String(data?.container_id || '') !== String(consoleContainerId)) return;
        const status = document.getElementById('consoleStatus');
        if (status) {
            status.textContent = data?.ok
                ? `Attach input sent via ${data?.transport || 'console'}`
                : `Attach failed: ${data?.detail || 'unknown error'}`;
        }
    });
    containersSocket.on('container_pty_status', (data) => {
        if (!consoleContainerId) return;
        if (data?.container_id && String(data.container_id) !== String(consoleContainerId)) return;
        if (data?.session_id && consolePtySessionId && String(data.session_id) !== String(consolePtySessionId)) return;
        const status = document.getElementById('consoleStatus');
        if (!status) return;
        if (data?.state === 'ready') {
            consolePtySessionId = String(data?.session_id || '');
            status.textContent = `Interactive shell ready: ${data?.shell || '/bin/sh'}`;
            return;
        }
        if (data?.state === 'closed') {
            consolePtySessionId = null;
            status.textContent = `Shell session closed${Number.isFinite(data?.exit_code) ? ` (exit ${data.exit_code})` : ''}`;
            return;
        }
        if (data?.state === 'error') {
            consolePtySessionId = null;
        }
        status.textContent = String(data?.detail || data?.state || 'shell update');
    });
    containersSocket.on('container_pty_update', (data) => {
        if (consoleMode !== 'shell') return;
        if (!consolePtySessionId || String(data?.session_id || '') !== String(consolePtySessionId)) return;
        const output = document.getElementById('consoleOutput');
        const status = document.getElementById('consoleStatus');
        if (!output) return;
        if (data?.clipped) {
            output.textContent = String(data?.output || '');
        } else {
            output.textContent = `${output.textContent || ''}${String(data?.output || '')}`;
        }
        output.scrollTop = output.scrollHeight;
        if (status) status.textContent = `Interactive shell live: ${new Date().toLocaleTimeString()}`;
    });
    containersSocket.on('container_pty_ack', (data) => {
        if (!consolePtySessionId || String(data?.session_id || '') !== String(consolePtySessionId)) return;
        const status = document.getElementById('consoleStatus');
        if (status && data?.ok === false) {
            status.textContent = `Shell input failed: ${data?.detail || 'unknown error'}`;
        }
    });
}

let metricsInterval = null;
let containersInterval = null;
let metricsAbortController = null;
let containersAbortController = null;
let metricsFailures = 0;
let previousNetworkTotal = null;
let lastContainerStatsSnapshot = null;
let hasContainerTableRendered = false;
let lastContainersSignature = '';
let currentContainers = [];
let consoleContainerId = null;
let consoleContainerName = '';
let consoleMode = 'logs';
let consolePollInterval = null;
let consolePtySessionId = null;
let deployJobId = null;
let deployPollInterval = null;
let activePreset = '';
let presetPermissionTemplates = {};
let activePresetConfig = {};
let availableProjects = [];
let projectsByContainerId = {};
let projectsMapCacheTs = 0;
let editContainerId = null;
let editSelectedUsers = new Map();
let editRolePermissionMatrix = {};
let editAvailablePortRules = [];
let editInspectBundle = null;
let advancedOpsPollInterval = null;
let inspectViewMode = 'tree';
let pendingRecreatePayload = null;

let containerPresets = {};

function fallbackArchitectProtocolDefinitions() {
    return {
        generic: {
            label: 'Generic Project',
            install: '',
            startup: '',
            hint: 'Manual flow for custom applications or ready-to-run images.'
        }
    };
}

function parsePortRule(rawRule = '') {
    const token = String(rawRule || '').trim();
    if (!token) return { hostPort: 0, containerPort: 0, protocol: 'tcp' };
    const normalized = token.replace(/\/(tcp|udp)$/i, '');
    const parts = normalized.split(':').map((item) => item.trim()).filter(Boolean);
    const hostPort = parseInt(parts.length >= 2 ? parts[parts.length - 2] : (parts[0] || ''), 10);
    const containerPort = parseInt(parts.length >= 2 ? parts[parts.length - 1] : (parts[0] || ''), 10);
    return {
        hostPort: Number.isFinite(hostPort) ? hostPort : 0,
        containerPort: Number.isFinite(containerPort) ? containerPort : 0,
        protocol: /\/udp$/i.test(token) ? 'udp' : 'tcp',
    };
}

function buildArchitectProtocolDefinitions(presets = []) {
    const defs = fallbackArchitectProtocolDefinitions();
    (Array.isArray(presets) ? presets : []).forEach((preset) => {
        const config = preset?.config && typeof preset.config === 'object' ? preset.config : {};
        const ui = preset?.ui && typeof preset.ui === 'object' ? preset.ui : {};
        const protocol = String(config.project_protocol || '').trim() || inferArchitectProtocol(config.profile_name || preset?.name || '', config.image || '');
        if (!protocol) return;
        const current = defs[protocol];
        const preferred = !!ui.protocol_preferred;
        if (!current || preferred) {
            defs[protocol] = {
                label: String(ui.protocol_label || preset?.title || protocol).trim() || protocol,
                install: String(config.install_command || '').trim(),
                startup: String(config.command || '').trim(),
                hint: String(ui.protocol_hint || preset?.description || `Preset-backed bootstrap for ${protocol}.`).trim(),
            };
        }
    });
    return defs;
}

function buildWebsiteWizardDefinitions(presets = []) {
    const defs = {};
    (Array.isArray(presets) ? presets : []).forEach((preset) => {
        const config = preset?.config && typeof preset.config === 'object' ? preset.config : {};
        const ui = preset?.ui && typeof preset.ui === 'object' ? preset.ui : {};
        const website = ui.website_wizard && typeof ui.website_wizard === 'object' ? ui.website_wizard : null;
        if (!website || website.enabled === false) return;
        const protocol = String(website.type || config.project_protocol || inferArchitectProtocol(config.profile_name || preset?.name || '', config.image || '') || '').trim();
        if (!protocol) return;
        const firstPort = parsePortRule(String(config.ports || '').split(',')[0] || '');
        const preferred = !!website.preferred;
        if (!defs[protocol]) {
            defs[protocol] = {
                label: String(website.label || preset?.title || protocol).trim() || protocol,
                presetCandidates: [],
                protocol,
                image: config.image || '',
                install: String(config.install_command || '').trim(),
                startup: String(config.command || '').trim(),
                internalPort: Number(website.internal_port || firstPort.containerPort || 80),
                defaultPublicPort: Number(website.default_public_port || firstPort.hostPort || firstPort.containerPort || 80),
                ram: Number(config.ram || 1024),
                disk: Number(config.disk || 20),
            };
        }
        const entry = defs[protocol];
        if (preferred) entry.presetCandidates.unshift(preset.name);
        else entry.presetCandidates.push(preset.name);
        if (preferred) {
            entry.label = String(website.label || entry.label || preset?.title || protocol).trim();
            entry.image = config.image || entry.image;
            entry.install = String(config.install_command || entry.install || '').trim();
            entry.startup = String(config.command || entry.startup || '').trim();
            entry.internalPort = Number(website.internal_port || firstPort.containerPort || entry.internalPort || 80);
            entry.defaultPublicPort = Number(website.default_public_port || firstPort.hostPort || entry.defaultPublicPort || entry.internalPort || 80);
            entry.ram = Number(config.ram || entry.ram || 1024);
            entry.disk = Number(config.disk || entry.disk || 20);
        }
    });
    return defs;
}

function renderWebsiteTypeOptions() {
    const select = document.getElementById('site_type');
    if (!select) return;
    const current = String(select.value || '').trim();
    const entries = Object.entries(websiteWizardDefinitions);
    select.innerHTML = '';
    if (!entries.length) {
        const option = document.createElement('option');
        option.value = 'generic';
        option.textContent = 'No website presets found';
        select.appendChild(option);
        return;
    }
    entries.sort((a, b) => String(a[1]?.label || a[0]).localeCompare(String(b[1]?.label || b[0])));
    entries.forEach(([key, item]) => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = item.label || key;
        select.appendChild(option);
    });
    select.value = websiteWizardDefinitions[current] ? current : entries[0][0];
}

function getContainerSortMode() {
    return localStorage.getItem(CONTAINERS_SORT_STORAGE_KEY) || 'pinned';
}

function setContainerSortMode(value) {
    localStorage.setItem(CONTAINERS_SORT_STORAGE_KEY, value || 'pinned');
}

function normalizePinnedContainer(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const id = String(raw.id || '').trim();
    const name = String(raw.name || '').trim();
    if (!id && !name) return null;
    return {
        id,
        name,
        image: String(raw.image || '').trim(),
    };
}

function loadPinnedContainers() {
    try {
        const parsed = JSON.parse(localStorage.getItem(PINNED_CONTAINERS_STORAGE_KEY) || '[]');
        return (Array.isArray(parsed) ? parsed : []).map(normalizePinnedContainer).filter(Boolean);
    } catch (_) {
        return [];
    }
}

function savePinnedContainers(items) {
    localStorage.setItem(PINNED_CONTAINERS_STORAGE_KEY, JSON.stringify(items));
}

function pinnedContainerKey(container) {
    return `${String(container?.id || '').trim()}::${String(container?.name || '').trim()}`;
}

function isPinnedContainer(container) {
    const key = pinnedContainerKey(container);
    return loadPinnedContainers().some((item) => pinnedContainerKey(item) === key);
}

function togglePinnedContainer(container) {
    const normalized = normalizePinnedContainer(container);
    if (!normalized) return;
    const key = pinnedContainerKey(normalized);
    const current = loadPinnedContainers();
    const next = current.some((item) => pinnedContainerKey(item) === key)
        ? current.filter((item) => pinnedContainerKey(item) !== key)
        : [normalized, ...current.filter((item) => pinnedContainerKey(item) !== key)];
    savePinnedContainers(next);
    renderContainersTable(currentContainers);
}

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

function containerIdentityKeys(container) {
    const keys = [];
    const id = String(container?.id || '').trim();
    const fullId = String(container?.full_id || '').trim();
    const name = String(container?.name || '').trim();
    if (id) keys.push(id);
    if (fullId && fullId !== id) keys.push(fullId);
    if (name) keys.push(name);
    return keys;
}

function buildProjectsByContainerMap(projects) {
    const map = {};
    (Array.isArray(projects) ? projects : []).forEach(project => {
        const projectId = String(project?.id || '').trim();
        const projectName = String(project?.name || '').trim();
        if (!projectId || !projectName) return;
        const entry = { id: projectId, name: projectName };
        (Array.isArray(project?.containers) ? project.containers : []).forEach(cont => {
            containerIdentityKeys(cont).forEach(key => {
                if (!map[key]) map[key] = [];
                if (!map[key].some(p => p.id === projectId)) map[key].push(entry);
            });
        });
    });
    Object.keys(map).forEach(key => {
        map[key].sort((a, b) => a.name.localeCompare(b.name));
    });
    return map;
}

function containerProjects(container) {
    return [
        ...(projectsByContainerId[String(container?.id || '')] || []),
        ...(projectsByContainerId[String(container?.full_id || '')] || []),
        ...(projectsByContainerId[String(container?.name || '')] || []),
    ];
}

function compareContainers(a, b, mode) {
    const pinnedDelta = Number(isPinnedContainer(b)) - Number(isPinnedContainer(a));
    if (pinnedDelta !== 0) return pinnedDelta;

    const nameCompare = String(a?.name || a?.id || '').localeCompare(String(b?.name || b?.id || ''));
    if (mode === 'name_desc') return -nameCompare;
    if (mode === 'status') {
        const rank = { running: 0, paused: 1, restarting: 2, created: 3, exited: 4, dead: 5, unknown: 6 };
        const delta = (rank[String(a?.status || 'unknown').toLowerCase()] ?? 99) - (rank[String(b?.status || 'unknown').toLowerCase()] ?? 99);
        return delta !== 0 ? delta : nameCompare;
    }
    if (mode === 'projects') {
        const delta = containerProjects(b).length - containerProjects(a).length;
        return delta !== 0 ? delta : nameCompare;
    }
    if (mode === 'users') {
        const delta = (Array.isArray(b?.users) ? b.users.length : 0) - (Array.isArray(a?.users) ? a.users.length : 0);
        return delta !== 0 ? delta : nameCompare;
    }
    return nameCompare;
}

function getSortedContainers(containers) {
    const mode = getContainerSortMode();
    return [...(Array.isArray(containers) ? containers : [])].sort((a, b) => compareContainers(a, b, mode));
}

function updateContainersSummary(containers) {
    const summaryEl = document.getElementById('containers_summary_text');
    if (!summaryEl) return;
    const running = containers.filter((c) => String(c?.status || '').toLowerCase() === 'running').length;
    const pinned = containers.filter((c) => isPinnedContainer(c)).length;
    const unassigned = containers.filter((c) => containerProjects(c).length === 0).length;
    summaryEl.textContent = `${containers.length} total, ${running} running, ${pinned} pinned, ${unassigned} without project`;
}

async function refreshContainerProjectsMap(force = false) {
    const now = Date.now();
    if (!force && projectsMapCacheTs && (now - projectsMapCacheTs) < 15000) {
        return;
    }
    try {
        const res = await fetch('/api/projects?tab=active', { cache: 'no-store' });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(payload?.detail || 'Failed to load projects');
        projectsByContainerId = buildProjectsByContainerMap(payload?.projects || []);
        projectsMapCacheTs = now;
    } catch (_) {
        projectsByContainerId = {};
        projectsMapCacheTs = now;
    }
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

function roleOptionsMarkup(selectedRole) {
    const roles = (roleCatalog.length ? roleCatalog : ['user']).slice();
    if (!roles.includes(selectedRole)) roles.push(selectedRole || 'user');
    return roles
        .sort((a, b) => String(a).localeCompare(String(b)))
        .map((role) => `<option value="${escapeAttr(role)}"${role === selectedRole ? ' selected' : ''}>${escapeHtml(role)}</option>`)
        .join('');
}

function describePortRule(rule) {
    const raw = String(rule || '').trim();
    if (!raw) return 'Unknown route';
    const parts = raw.split(':');
    const target = parts[parts.length - 1] || raw;
    const source = parts.length > 1 ? parts[parts.length - 2] : target;
    const protocol = raw.includes('/udp') ? 'UDP' : 'TCP';
    return `${protocol} ${source} -> ${target}`;
}

function renderSettingsModalAccessChips(matrix = editRolePermissionMatrix) {
    const host = document.getElementById('settings_modal_access_chips');
    if (!host) return;
    const rows = Object.entries(matrix || {});
    if (!rows.length) {
        host.innerHTML = '<span class="access-chip"><i class="bi bi-shield"></i> Default access model</span>';
        return;
    }
    const chipDefs = [
        { key: 'allow_explorer', label: 'Explorer' },
        { key: 'allow_shell', label: 'Shell' },
        { key: 'allow_settings', label: 'Settings' },
        { key: 'allow_edit_files', label: 'Edit Files' },
        { key: 'allow_edit_ports', label: 'Edit Ports' }
    ];
    host.innerHTML = chipDefs.map((item) => {
        const roles = rows.filter(([, policy]) => policy && policy[item.key]).map(([role]) => role);
        const label = roles.length ? `${item.label}: ${roles.join(', ')}` : `${item.label}: none`;
        return `<span class="access-chip"><i class="bi bi-shield-check"></i>${escapeHtml(label)}</span>`;
    }).join('');
}

function syncEditPortsInputFromSelection() {
    const selected = Array.from(document.querySelectorAll('#edit_ports_selection input[type="checkbox"]:checked'))
        .map((node) => node.value);
    const input = document.getElementById('edit_cont_ports');
    if (input) input.value = selected.join(', ');
    document.querySelectorAll('#edit_ports_selection .port-selection-card').forEach((card) => {
        const node = card.querySelector('input[type="checkbox"]');
        card.classList.toggle('is-selected', !!node?.checked);
    });
    const meta = document.getElementById('edit_ports_selection_meta');
    const summary = document.getElementById('edit_ports_summary');
    const total = document.querySelectorAll('#edit_ports_selection .port-selection-card').length;
    if (meta) meta.textContent = total ? `Selected ${selected.length} of ${total} published routes.` : 'No published routes found for this container.';
    if (summary) {
        summary.textContent = selected.length
            ? `${selected.length} route(s) will stay active after save.`
            : 'No route selected. You can still type manual mappings above if needed.';
    }
}

function filterEditPortsSelection() {
    const filter = String(document.getElementById('edit_ports_search')?.value || '').trim().toLowerCase();
    document.querySelectorAll('#edit_ports_selection .port-selection-card').forEach((card) => {
        const text = String(card.dataset.rule || '').toLowerCase();
        card.style.display = !filter || text.includes(filter) ? '' : 'none';
    });
}

function setAllEditPortsSelection(enabled) {
    document.querySelectorAll('#edit_ports_selection .port-selection-card input[type="checkbox"]').forEach((node) => {
        if (node.closest('.port-selection-card')?.style.display === 'none') return;
        node.checked = !!enabled;
    });
    syncEditPortsInputFromSelection();
}

function renderEditPortsSelection(rules = [], selectedRules = []) {
    const host = document.getElementById('edit_ports_selection');
    const meta = document.getElementById('edit_ports_selection_meta');
    if (!host || !meta) return;
    host.innerHTML = '';
    const selected = new Set(Array.isArray(selectedRules) ? selectedRules : []);
    if (!rules.length) {
        meta.textContent = 'No published routes detected for this container.';
        host.innerHTML = '<div class="input-hint">Run or recreate the container with exposed ports to manage them here.</div>';
        syncEditPortsInputFromSelection();
        return;
    }
    rules.forEach((rule) => {
        const id = `edit-port-${safeDomId(rule)}`;
        const card = document.createElement('label');
        card.className = 'port-selection-card';
        card.dataset.rule = rule;
        card.setAttribute('for', id);

        const input = document.createElement('input');
        input.type = 'checkbox';
        input.id = id;
        input.value = rule;
        input.checked = selected.size ? selected.has(rule) : true;
        input.addEventListener('change', syncEditPortsInputFromSelection);

        card.innerHTML = `
            <span class="port-selection-icon"><i class="bi bi-diagram-3"></i></span>
            <span class="port-selection-text">
                <strong>${escapeHtml(rule)}</strong>
                <small>${escapeHtml(describePortRule(rule))}</small>
            </span>
        `;
        card.prepend(input);
        host.appendChild(card);
    });
    syncEditPortsInputFromSelection();
    filterEditPortsSelection();
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

async function loadAvailableProjects() {
    if (!isStaff) return;
    try {
        const res = await fetch('/api/projects/active');
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || 'Failed to load projects');
        availableProjects = Array.isArray(data?.projects) ? data.projects : [];
    } catch (_) {
        availableProjects = [];
    }
    renderProjectSelector();
}

function renderProjectSelector() {
    const select = document.getElementById('cont_project_ids');
    if (!select) return;
    const selected = new Set(Array.from(select.selectedOptions || []).map(o => String(o.value)));
    select.innerHTML = '';
    availableProjects
        .slice()
        .sort((a, b) => String(a?.name || '').localeCompare(String(b?.name || '')))
        .forEach(project => {
            const option = document.createElement('option');
            option.value = String(project?.id || '');
            const tags = Array.isArray(project?.tags) && project.tags.length ? ` [${project.tags.join(', ')}]` : '';
            option.textContent = `${String(project?.name || option.value)}${tags}`;
            option.selected = selected.has(option.value);
            select.appendChild(option);
        });
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

function renderEditRolePermissionMatrix() {
    const host = document.getElementById('edit_role_permissions_grid');
    if (!host) return;
    if (!editRolePermissionMatrix || Object.keys(editRolePermissionMatrix).length === 0) {
        editRolePermissionMatrix = cloneRoleMatrix({});
    }
    const roles = Object.keys(editRolePermissionMatrix);
    let html = '<table style="width:100%; border-collapse: collapse; font-size: 0.78rem;"><thead><tr>';
    html += '<th style="text-align:left; padding:8px 10px; border-bottom:1px solid var(--border);">Role</th>';
    rolePermissionColumns.forEach(col => {
        html += `<th style="text-align:center; padding:8px 10px; border-bottom:1px solid var(--border);">${escapeHtml(col.label)}</th>`;
    });
    html += '</tr></thead><tbody>';
    roles.forEach(role => {
        html += `<tr><td style="padding:8px 10px; border-bottom:1px solid var(--border); color:#d6d6df; font-weight:600;">${escapeHtml(role)}</td>`;
        rolePermissionColumns.forEach(col => {
            const checked = editRolePermissionMatrix[role][col.key] ? 'checked' : '';
            html += `<td style="text-align:center; padding:8px 10px; border-bottom:1px solid var(--border);"><input class="role-perm-check" type="checkbox" data-edit-role="${escapeAttr(role)}" data-edit-key="${escapeAttr(col.key)}" ${checked} onchange="onEditRolePermissionToggle(this)"></td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table>';
    host.innerHTML = html;
    renderSettingsModalAccessChips(editRolePermissionMatrix);
}

function onEditRolePermissionToggle(el) {
    const role = String(el?.dataset?.editRole || '');
    const key = String(el?.dataset?.editKey || '');
    if (!role || !key) return;
    if (!editRolePermissionMatrix[role]) editRolePermissionMatrix[role] = {};
    editRolePermissionMatrix[role][key] = !!el.checked;
    renderSettingsModalAccessChips(editRolePermissionMatrix);
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
        architectProtocolDefinitions = buildArchitectProtocolDefinitions(presets);
        websiteWizardDefinitions = buildWebsiteWizardDefinitions(presets);

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
        renderWebsiteTypeOptions();
        updateWebsiteWizardPreview();
    } catch (e) {
        architectProtocolDefinitions = fallbackArchitectProtocolDefinitions();
        websiteWizardDefinitions = {};
        renderWebsiteTypeOptions();
        updateWebsiteWizardPreview();
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

function websiteWizardConfig() {
    const entries = Object.entries(websiteWizardDefinitions);
    const fallbackKey = entries[0]?.[0] || '';
    const key = String(document.getElementById('site_type')?.value || fallbackKey);
    return websiteWizardDefinitions[key] || websiteWizardDefinitions[fallbackKey] || {
        label: 'Website',
        presetCandidates: [],
        protocol: 'generic',
        image: '',
        install: '',
        startup: '',
        internalPort: 80,
        defaultPublicPort: 80,
        ram: 1024,
        disk: 20,
    };
}

function websiteWizardResolvedPreset(config = websiteWizardConfig()) {
    const candidates = Array.isArray(config.presetCandidates) ? config.presetCandidates : [];
    return candidates.find((name) => !!containerPresets[name]) || candidates[0] || '';
}

function websiteWizardPublicPort() {
    const raw = parseInt(document.getElementById('site_public_port')?.value || '', 10);
    return Number.isFinite(raw) ? raw : websiteWizardConfig().defaultPublicPort;
}

function websiteWizardPreviewUrl() {
    const domain = String(document.getElementById('site_domain')?.value || '').trim();
    const port = websiteWizardPublicPort();
    const host = domain || window.location.hostname || 'panel-host';
    const secure = port === 443;
    const needsPort = port && ![80, 443].includes(port);
    return `${secure ? 'https' : 'http'}://${host}${needsPort ? `:${port}` : ''}`;
}

function syncWebsiteWizardFields(forcePort = false) {
    const config = websiteWizardConfig();
    const currentPort = document.getElementById('site_public_port');
    const installEl = document.getElementById('site_install_command');
    const startEl = document.getElementById('site_start_command');
    const ramEl = document.getElementById('site_ram');
    const diskEl = document.getElementById('site_disk');
    if (currentPort && (forcePort || !String(currentPort.value || '').trim())) currentPort.value = String(config.defaultPublicPort || '');
    if (installEl && !String(installEl.value || '').trim()) installEl.value = config.install || '';
    if (startEl && !String(startEl.value || '').trim()) startEl.value = config.startup || '';
    if (ramEl && !String(ramEl.value || '').trim()) ramEl.value = String(config.ram || 1024);
    if (diskEl && !String(diskEl.value || '').trim()) diskEl.value = String(config.disk || 20);
}

function updateWebsiteWizardPreview() {
    const config = websiteWizardConfig();
    const presetName = websiteWizardResolvedPreset(config);
    const publicPort = websiteWizardPublicPort();
    const route = publicPort ? `${publicPort}:${config.internalPort}` : 'No published route';
    const presetEl = document.getElementById('site_preset_preview');
    const routeEl = document.getElementById('site_route_preview');
    const publicEl = document.getElementById('site_public_preview');
    const noteEl = document.getElementById('site_preview_note');
    const validationEl = document.getElementById('site_wizard_validation');
    const siteName = String(document.getElementById('site_name')?.value || '').trim();
    const startCmd = String(document.getElementById('site_start_command')?.value || '').trim();
    const domain = String(document.getElementById('site_domain')?.value || '').trim();
    let validation = 'Nebula will create a managed workspace, map the public port, and keep the advanced host mounts empty by default.';
    let level = 'ok';

    if (!siteName) {
        validation = 'Website name is required so Nebula can create the instance and workspace.';
        level = 'warn';
    } else if (!Number.isFinite(publicPort) || publicPort < 1 || publicPort > 65535) {
        validation = 'Public port must be between 1 and 65535.';
        level = 'error';
    } else if (!presetName) {
        validation = 'No matching website preset is available yet. Open Container Architect for full manual deploy.';
        level = 'error';
    } else if (config.protocol !== 'static-web' && !startCmd) {
        validation = 'This website type needs a startup command. Nebula prefilled one, but it is currently empty.';
        level = 'warn';
    }

    if (presetEl) presetEl.textContent = presetName || `${config.label} (manual fallback)`;
    if (routeEl) routeEl.textContent = route;
    if (publicEl) publicEl.textContent = websiteWizardPreviewUrl();
    if (noteEl) noteEl.textContent = domain
        ? 'Using the domain you entered for the public address preview.'
        : 'Using the current panel host for preview. Replace with your domain anytime.';
    if (validationEl) {
        validationEl.className = `website-validation ${level}`;
        validationEl.textContent = validation;
    }
}

function onWebsiteTypeChange() {
    const installEl = document.getElementById('site_install_command');
    const startEl = document.getElementById('site_start_command');
    if (installEl) installEl.value = '';
    if (startEl) startEl.value = '';
    syncWebsiteWizardFields(true);
    updateWebsiteWizardPreview();
}

function inferArchitectProtocol(profileName = '', imageName = '') {
    const profile = String(profileName || '').toLowerCase();
    const image = String(imageName || '').toLowerCase();
    if (profile.includes('flask') || image.includes('flask')) return 'python-flask';
    if (profile.includes('fastapi') || image.includes('fastapi') || image.includes('uvicorn')) return 'python-fastapi';
    if (profile.includes('django') || image.includes('django')) return 'python-django';
    if (profile.includes('vite') || image.includes('vite')) return 'node-vite';
    if (profile.includes('express') || image.includes('node')) return 'node-npm';
    if (profile.includes('nginx') || profile.includes('caddy') || image.includes('nginx') || image.includes('caddy')) return 'static-web';
    if (profile.includes('python') || image.includes('python')) return 'python-pip';
    return 'generic';
}

function populateArchitectProtocolOptions(selectedValue = '') {
    const select = document.getElementById('cont_project_protocol');
    if (!select) return;
    const current = selectedValue || inferArchitectProtocol(activePresetConfig.profile_name || activePreset, document.getElementById('cont_image')?.value || '');
    const defs = Object.keys(architectProtocolDefinitions).length ? architectProtocolDefinitions : fallbackArchitectProtocolDefinitions();
    select.innerHTML = '';
    Object.entries(defs).forEach(([key, item]) => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = item.label || key;
        select.appendChild(option);
    });
    select.value = current || 'generic';
}

function onArchitectProtocolChange(forceApply = false) {
    const select = document.getElementById('cont_project_protocol');
    const installEl = document.getElementById('cont_install_command');
    const commandEl = document.getElementById('cont_command');
    const hintEl = document.getElementById('cont_protocol_hint');
    const defs = Object.keys(architectProtocolDefinitions).length ? architectProtocolDefinitions : fallbackArchitectProtocolDefinitions();
    const protocol = defs[String(select?.value || 'generic')] || defs.generic;
    if (hintEl) hintEl.textContent = protocol.hint || 'Select a protocol to prefill install/start commands.';
    if (installEl && (forceApply || !String(installEl.value || '').trim())) installEl.value = protocol.install || '';
    if (commandEl && (forceApply || !String(commandEl.value || '').trim())) commandEl.value = protocol.startup || '';
    updateArchitectLaunchPreview();
}

function firstArchitectPortRule() {
    const tokens = String(document.getElementById('cont_ports')?.value || '')
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
    return tokens.length ? tokens[0] : '';
}

function updateArchitectLaunchPreview() {
    const hostEl = document.getElementById('cont_launch_host_preview');
    const routeEl = document.getElementById('cont_launch_route_preview');
    const publicEl = document.getElementById('cont_launch_public_preview');
    const domain = String(document.getElementById('cont_domain_name')?.value || '').trim();
    const manualUrl = String(document.getElementById('cont_launch_url')?.value || '').trim();
    const route = firstArchitectPortRule();
    const tokens = route ? route.replace(/\/(tcp|udp)$/i, '').split(':').map((item) => item.trim()).filter(Boolean) : [];
    const hostPort = tokens.length >= 2 ? tokens[tokens.length - 2] : (tokens[0] || '');
    const host = domain || (window.location.hostname || 'panel-host');
    const portSuffix = hostPort && !['80', '443'].includes(hostPort) ? `:${hostPort}` : '';
    const preview = manualUrl || (hostPort ? `${hostPort === '443' ? 'https' : 'http'}://${host}${portSuffix}` : `${window.location.protocol}//${host}`);
    if (hostEl) hostEl.textContent = window.location.origin || 'panel host';
    if (routeEl) routeEl.textContent = route || 'No published ports yet';
    if (publicEl) publicEl.textContent = preview;
}

function firstHostVolumeMount() {
    const raw = String(document.getElementById('cont_volumes')?.value || '').trim();
    if (!raw) return null;
    const lines = raw.split('\n').map((line) => line.trim()).filter(Boolean);
    for (const line of lines) {
        const parts = line.split(':');
        if (parts.length < 2) continue;
        const mode = parts.length >= 3 && ['ro', 'rw'].includes(parts[parts.length - 1]) ? parts[parts.length - 1] : '';
        const containerPath = mode ? parts[parts.length - 2] : parts[parts.length - 1];
        const hostPath = mode ? parts.slice(0, -2).join(':') : parts.slice(0, -1).join(':');
        if (!String(hostPath || '').trim() || !String(containerPath || '').trim()) continue;
        return {
            raw: line,
            hostPath: String(hostPath).trim(),
            containerPath: String(containerPath).trim(),
            mode: mode || 'rw'
        };
    }
    return null;
}

function updateArchitectVolumeAdvisory() {
    const box = document.getElementById('volume_advisory');
    const command = document.getElementById('volume_advisory_command');
    if (!box || !command) return;
    const mount = firstHostVolumeMount();
    if (!mount) {
        box.className = 'volume-advisory ok';
        box.innerHTML = '<strong>Managed workspace will be used</strong><p>Nebula will create and prepare its own workspace automatically. This is the recommended default and does not require manual Linux permission setup.</p><code id="volume_advisory_command">No external host path configured.</code>';
        return;
    }

    const normalized = mount.hostPath.replace(/\\/g, '/');
    const isManaged = normalized.includes(MANAGED_WORKSPACE_HINT);
    if (isManaged) {
        box.className = 'volume-advisory ok';
        box.innerHTML = `<strong>Managed workspace detected</strong><p>This mount points to a Nebula-managed workspace path, so the panel can prepare group ownership automatically for new environments.</p><code id="volume_advisory_command">${escapeHtml(mount.raw)}</code>`;
        return;
    }

    const escapedPath = mount.hostPath.replaceAll("'", "'\\''");
    box.className = 'volume-advisory warn';
    box.innerHTML = `<strong>External host path detected</strong><p>This mount uses a custom host directory. Make sure it is writable for the panel service group <code>${escapeHtml(PANEL_GROUP_HINT)}</code> before deployment, otherwise File Explorer upload/edit will fail.</p><code id="volume_advisory_command">sudo chgrp -R ${PANEL_GROUP_HINT} '${escapeHtml(escapedPath)}'\nsudo chmod -R g+rwX '${escapeHtml(escapedPath)}'\nsudo find '${escapeHtml(escapedPath)}' -type d -exec chmod g+s {} +</code>`;
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
    if (typeof preset.project_protocol === 'string') setFieldValue('cont_project_protocol', preset.project_protocol);
    if (typeof preset.install_command === 'string') setFieldValue('cont_install_command', preset.install_command);
    if (typeof preset.command === 'string') setFieldValue('cont_command', preset.command);
    if (typeof preset.env === 'string') setFieldValue('cont_env', preset.env);
    if (typeof preset.volumes === 'string') setFieldValue('cont_volumes', preset.volumes);
    if (typeof preset.domain_name === 'string') setFieldValue('cont_domain_name', preset.domain_name);
    if (typeof preset.launch_url === 'string') setFieldValue('cont_launch_url', preset.launch_url);
    populateArchitectProtocolOptions(preset.project_protocol || inferArchitectProtocol(preset.profile_name || presetName, preset.image || ''));
    onArchitectProtocolChange(false);
    updateArchitectLaunchPreview();
    updateArchitectVolumeAdvisory();

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
        const card = document.createElement('div');
        card.className = 'user-assignment-card';
        card.innerHTML = `
            <div class="user-assignment-meta">
                <span class="user-assignment-name">${escapeHtml(u.username)}</span>
                <span class="user-assignment-db">${escapeHtml(u.db || 'system.db')}</span>
            </div>
            <select class="user-role-select" data-user-key="${escapeAttr(selectedUserKey(u))}">
                ${roleOptionsMarkup(u.role_tag || 'user')}
            </select>
            <button type="button" class="assignment-remove-btn" data-remove-user="${escapeAttr(selectedUserKey(u))}">
                <i class="bi bi-x-lg"></i>
            </button>
        `;
        const select = card.querySelector('select');
        if (select) {
            select.addEventListener('change', (event) => {
                const item = selectedUsers.get(selectedUserKey(u));
                if (!item) return;
                item.role_tag = String(event.target.value || 'user');
            });
        }
        const removeBtn = card.querySelector('[data-remove-user]');
        if (removeBtn) {
            removeBtn.addEventListener('click', () => {
                selectedUsers.delete(selectedUserKey(u));
                renderUserPicker(document.getElementById('cont_user_search')?.value || '');
            });
        }
        target.appendChild(card);
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

function toggleEditUserSelection(user) {
    const key = selectedUserKey(user);
    if (editSelectedUsers.has(key)) editSelectedUsers.delete(key);
    else editSelectedUsers.set(key, { username: user.username, db: user.db, role_tag: user.role_tag || 'user' });
    renderEditUserPicker(document.getElementById('edit_user_search')?.value || '');
}

function renderEditSelectedUsers() {
    const target = document.getElementById('edit_users_selected');
    if (!target) return;
    target.innerHTML = '';
    if (editSelectedUsers.size === 0) {
        target.innerHTML = '<span style="font-size:0.75rem; color: var(--text-muted);">No users selected</span>';
        return;
    }
    Array.from(editSelectedUsers.values())
        .sort((a, b) => `${a.username}:${a.db}`.localeCompare(`${b.username}:${b.db}`))
        .forEach(u => {
            const card = document.createElement('div');
            card.className = 'user-assignment-card';
            card.innerHTML = `
                <div class="user-assignment-meta">
                    <span class="user-assignment-name">${escapeHtml(u.username)}</span>
                    <span class="user-assignment-db">${escapeHtml(u.db || 'system.db')}</span>
                </div>
                <select class="user-role-select" data-edit-user-key="${escapeAttr(selectedUserKey(u))}">
                    ${roleOptionsMarkup(u.role_tag || 'user')}
                </select>
                <button type="button" class="assignment-remove-btn" data-edit-remove-user="${escapeAttr(selectedUserKey(u))}">
                    <i class="bi bi-x-lg"></i>
                </button>
            `;
            const select = card.querySelector('select');
            if (select) {
                select.addEventListener('change', (event) => {
                    const item = editSelectedUsers.get(selectedUserKey(u));
                    if (!item) return;
                    item.role_tag = String(event.target.value || 'user');
                });
            }
            const removeBtn = card.querySelector('[data-edit-remove-user]');
            if (removeBtn) {
                removeBtn.addEventListener('click', () => {
                    editSelectedUsers.delete(selectedUserKey(u));
                    renderEditUserPicker(document.getElementById('edit_user_search')?.value || '');
                });
            }
            target.appendChild(card);
        });
}

function renderEditUserPicker(searchText) {
    const listEl = document.getElementById('edit_users_list');
    if (!listEl) return;
    listEl.innerHTML = '';
    const query = (searchText || '').toLowerCase().trim();
    const filtered = assignableUsers.filter(u => {
        const label = `${u.username} ${u.db}`.toLowerCase();
        return !query || label.includes(query);
    });

    if (filtered.length === 0) {
        listEl.innerHTML = '<div style="padding:10px; color: var(--text-muted); font-size:0.8rem;">No users found</div>';
        renderEditSelectedUsers();
        return;
    }

    filtered.forEach(user => {
        const row = document.createElement('div');
        row.className = `user-option${editSelectedUsers.has(selectedUserKey(user)) ? ' is-selected' : ''}`;
        row.onclick = () => toggleEditUserSelection(user);
        const usernameHtml = escapeHtml(user.username);
        const dbHtml = escapeHtml(user.db);
        row.innerHTML = `
            <div>${usernameHtml}</div>
            <div style="font-size:0.72rem; color: var(--text-muted);">${dbHtml}</div>
        `;
        listEl.appendChild(row);
    });
    renderEditSelectedUsers();
}

function parseLegacyUserLabel(label) {
    const raw = String(label || '').trim();
    if (!raw) return null;
    const match = raw.match(/^(.+?)\s*\(([^)]+)\)\s*$/);
    if (!match) return { username: raw, role_tag: 'user', db: 'system.db' };
    return {
        username: String(match[1] || '').trim(),
        role_tag: String(match[2] || 'user').trim().toLowerCase(),
        db: 'system.db'
    };
}

function asNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function formatContainerRuntimeMetrics(container) {
    const cpu = Number(container?.cpu_percent);
    const memUsed = Number(container?.memory_used_mb);
    const memLimit = Number(container?.memory_limit_mb);
    const netUp = Number(container?.network_tx_mbps);
    const netDown = Number(container?.network_rx_mbps);

    const cpuLabel = Number.isFinite(cpu) ? `${cpu.toFixed(1)}% CPU` : 'CPU n/a';
    const memoryLabel = Number.isFinite(memUsed)
        ? `${memUsed >= 1024 ? `${(memUsed / 1024).toFixed(2)} GB` : `${memUsed.toFixed(0)} MB`} RAM`
        : 'RAM n/a';
    const memorySub = Number.isFinite(memUsed) && Number.isFinite(memLimit) && memLimit > 0
        ? `${((memUsed / memLimit) * 100).toFixed(1)}% of limit`
        : 'No memory cap data';
    const networkLabel = (Number.isFinite(netUp) || Number.isFinite(netDown))
        ? `↑ ${asNumber(netUp, 0).toFixed(2)} ↓ ${asNumber(netDown, 0).toFixed(2)} MB/s`
        : 'Network n/a';

    return `
        <div class="container-runtime-grid">
            <span class="runtime-pill"><i class="bi bi-cpu"></i>${escapeHtml(cpuLabel)}</span>
            <span class="runtime-pill"><i class="bi bi-memory"></i>${escapeHtml(memoryLabel)}</span>
            <span class="runtime-pill"><i class="bi bi-reception-4"></i>${escapeHtml(networkLabel)}</span>
        </div>
        <div class="container-runtime-sub">${escapeHtml(memorySub)}</div>
    `;
}

async function apiJson(url, options = undefined) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = data && (data.detail || data.error || data.message);
        throw new Error(String(detail || 'Request failed'));
    }
    return data;
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
        initContainerSocket();
        updateConsoleModeUi();
        bindContainerTableActions();
        const attachInput = document.getElementById('consoleAttachInput');
        if (attachInput) {
            attachInput.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    sendConsoleAttachInput();
                }
            });
        }
        window.addEventListener('resize', emitPtyResize);
        const sortSelect = document.getElementById('containers_sort');
        if (sortSelect) {
            sortSelect.value = getContainerSortMode();
            sortSelect.addEventListener('change', () => {
                setContainerSortMode(sortSelect.value);
                renderContainersTable(currentContainers);
            });
        }
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
        let activeContainers = asNumber(data.active_containers, 0);
        let totalContainers = asNumber(data.containers, activeContainers);

        if (Array.isArray(currentContainers) && currentContainers.length > 0 && activeContainers === 0 && totalContainers === 0) {
            totalContainers = currentContainers.length;
            activeContainers = currentContainers.filter(c => (c.status || '').toLowerCase() === 'running').length;
        } else if (lastContainerStatsSnapshot && activeContainers === 0 && totalContainers === 0) {
            activeContainers = lastContainerStatsSnapshot.activeContainers;
            totalContainers = lastContainerStatsSnapshot.totalContainers;
        }
        lastContainerStatsSnapshot = { activeContainers, totalContainers };

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
    lastContainerStatsSnapshot = { activeContainers: running, totalContainers: total };
    document.getElementById('stat_active_containers').innerHTML = `${running} <span class="stat-sub">running / ${total} total</span>`;
}

function bindContainerTableActions() {
    const tbody = document.getElementById('container_table_body');
    if (!tbody || tbody.dataset.boundActions === '1') return;
    tbody.addEventListener('click', (event) => {
        const menuTrigger = event.target.closest('.action-btn[data-menu-id]');
        if (menuTrigger) {
            event.preventDefault();
            toggleMenu(event, menuTrigger.dataset.menuId);
            return;
        }

        const menuAction = event.target.closest('[data-menu-action]');
        if (!menuAction) return;

        event.preventDefault();
        event.stopPropagation();
        document.querySelectorAll('.dropdown-content').forEach(d => d.classList.remove('show'));

        const action = String(menuAction.dataset.menuAction || '');
        const containerId = String(menuAction.dataset.containerId || '');
        const containerName = String(menuAction.dataset.containerName || containerId);
        if (!containerId) return;

        if (action === 'console') {
            openConsoleModal(containerId, containerName);
            return;
        }
        if (action === 'start') {
            startContainer(containerId);
            return;
        }
        if (action === 'stop') {
            stopContainer(containerId);
            return;
        }
        if (action === 'restart') {
            restartContainer(containerId);
            return;
        }
        if (action === 'settings') {
            openContainerSettingsModal(containerId);
            return;
        }
        if (action === 'pin') {
            togglePinnedContainer({ id: containerId, name: containerName });
            return;
        }
        if (action === 'delete') {
            deleteContainer(containerId);
        }
    });
    tbody.dataset.boundActions = '1';
}

function onNodeChanged() {
    hasContainerTableRendered = false;
    lastContainersSignature = '';
    fetchContainers(true);
}

function renderContainersTable(containers) {
    const tbody = document.getElementById('container_table_body');
    if (!tbody) return;

    if (!Array.isArray(containers) || containers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:40px; color:var(--text-muted);">No active containers on this node</td></tr>';
        updateActiveContainersStat([]);
        updateContainersSummary([]);
        hasContainerTableRendered = true;
        return;
    }

    updateActiveContainersStat(containers);
    updateContainersSummary(containers);
    tbody.innerHTML = '';

    getSortedContainers(containers).forEach((cont) => {
        const row = document.createElement('tr');
        const pinned = isPinnedContainer(cont);
        row.className = `table-row-hover${pinned ? ' is-pinned-row' : ''}`;
        row.style.borderBottom = '1px solid var(--border)';
        row.style.cursor = 'pointer';
        const containerId = String(cont.id ?? '');
        const containerName = String(cont.name || containerId);
        const containerStatus = String(cont.status || 'unknown').toLowerCase();
        const containerImage = String(cont.image || 'unknown');
        const containerIdPath = encodeURIComponent(containerId);
        const menuId = `drop-${safeDomId(containerId)}`;
        const color = containerStatus === 'running' ? '#4ade80' : '#ff4f4f';
        const usersHtml = (cont.users || []).map((u, i) => {
            const username = String(u || '');
            const displayLetter = escapeHtml((username[0] || '?').toUpperCase());
            return `<div class="user-avatar-mini" style="margin-left: ${i === 0 ? '0' : '-8px'}; background: ${stringToColor(username)}" title="${escapeAttr(username)}">${displayLetter}</div>`;
        }).join('');
        const projectItems = containerProjects(cont);
        const seenProjects = new Set();
        const dedupProjects = projectItems.filter((p) => {
            if (!p?.id || seenProjects.has(p.id)) return false;
            seenProjects.add(p.id);
            return true;
        });
        const projectsHtml = dedupProjects.length
            ? dedupProjects.map((p) => `<span class="badge badge-user" title="${escapeAttr(p.name)}">${escapeHtml(p.name)}</span>`).join(' ')
            : '<span style="color:var(--text-muted); font-size:0.78rem;">Unassigned</span>';
        row.onclick = (event) => {
            if (event.target.closest('.dropdown') || event.target.closest('a') || event.target.closest('button')) return;
            window.location.href = `/containers/view/${containerIdPath}`;
        };

        row.innerHTML = `
            <td style="padding: 18px 24px;">
                <div style="display:flex; align-items:center; gap:16px;">
                    <div class="container-icon-bg"><i class="bi bi-box-seam" style="color:var(--accent);"></i></div>
                    <div>
                        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <div style="font-weight:700; color:white; font-size:0.9rem;">
                                <a href="/containers/view/${containerIdPath}" style="color:inherit; text-decoration:none;">${escapeHtml(containerName)}</a>
                            </div>
                            ${pinned ? '<span class="pin-chip"><i class="bi bi-pin-angle-fill"></i> Pinned</span>' : ''}
                        </div>
                        <div style="font-size:0.75rem; color:var(--text-muted);">ID: ${escapeHtml(containerId)}</div>
                    </div>
                </div>
            </td>
            <td style="padding: 18px 24px;">
                <div class="container-env-cell">
                    <span class="badge badge-user">${escapeHtml(containerImage)}</span>
                    ${formatContainerRuntimeMetrics(cont)}
                </div>
            </td>
            <td style="padding: 18px 24px;">
                <div style="display:flex; flex-wrap:wrap; gap:6px;">${projectsHtml}</div>
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
                    <button class="action-btn" data-menu-id="${escapeAttr(menuId)}" aria-label="Container actions">
                        <i class="bi bi-three-dots"></i>
                    </button>
                    <div id="${menuId}" class="dropdown-content">
                        <a href="/containers/view/${containerIdPath}"><i class="bi bi-window-sidebar"></i> Container Mode</a>
                        <a href="#" data-menu-action="console" data-container-id="${escapeAttr(containerId)}" data-container-name="${escapeAttr(containerName)}"><i class="bi bi-terminal"></i> Console</a>
                        <a href="#" data-menu-action="pin" data-container-id="${escapeAttr(containerId)}" data-container-name="${escapeAttr(containerName)}"><i class="bi ${pinned ? 'bi-pin-angle-fill' : 'bi-pin-angle'}"></i> ${pinned ? 'Unpin from Focus' : 'Pin to Focus'}</a>
                        <a href="#" data-menu-action="start" data-container-id="${escapeAttr(containerId)}"><i class="bi bi-play-fill"></i> Start</a>
                        <a href="#" data-menu-action="stop" data-container-id="${escapeAttr(containerId)}"><i class="bi bi-stop-fill"></i> Stop</a>
                        <a href="#" data-menu-action="restart" data-container-id="${escapeAttr(containerId)}"><i class="bi bi-arrow-clockwise"></i> Restart</a>
                        ${isStaff ? `
                        <a href="#" data-menu-action="settings" data-container-id="${escapeAttr(containerId)}"><i class="bi bi-sliders"></i> Container Settings</a>
                        <hr style="border:0; border-top:1px solid var(--border); margin:6px 0;">
                        <a href="#" style="color:#ff6b6b;" data-menu-action="delete" data-container-id="${escapeAttr(containerId)}"><i class="bi bi-trash3"></i> Delete</a>
                        ` : ''}
                    </div>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });

    hasContainerTableRendered = true;
}

async function fetchContainers(showLoader = false) {
    const tbody = document.getElementById('container_table_body');
    const selectedNode = document.getElementById('node_selector')?.value || '';
    const listUrl = selectedNode ? `/api/containers/list?node=${encodeURIComponent(selectedNode)}` : '/api/containers/list';
    if (!hasContainerTableRendered || showLoader) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:60px;"><i class="bi bi-arrow-repeat spin" style="font-size:2rem; color:var(--accent);"></i></td></tr>';
    }

    try {
        await refreshContainerProjectsMap(showLoader);
        if (containersAbortController) containersAbortController.abort();
        containersAbortController = new AbortController();
        const response = await fetch(listUrl, { signal: containersAbortController.signal, cache: 'no-store' });
        const containers = await response.json();

        if (!response.ok) {
            const detail = (containers && containers.detail) ? containers.detail : 'Failed to sync with Nebula Core';
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:40px; color:#ff4f4f;">${escapeHtml(detail)}</td></tr>`;
            hasContainerTableRendered = true;
            return;
        }

        if (!Array.isArray(containers)) {
            currentContainers = [];
            lastContainersSignature = '[]';
            renderContainersTable([]);
            return;
        }

        const signature = JSON.stringify(containers.map(c => [c.id, c.name, c.status, c.image, (c.users || []).join(',')]));
        const renderStateKey = `${signature}|${getContainerSortMode()}|${loadPinnedContainers().map(pinnedContainerKey).join(',')}`;
        if (renderStateKey === lastContainersSignature && hasContainerTableRendered) {
            currentContainers = containers;
            return;
        }

        currentContainers = containers;
        lastContainersSignature = renderStateKey;
        renderContainersTable(containers);
    } catch (e) {
        if (e && e.name === 'AbortError') return;
        if (!hasContainerTableRendered) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:40px; color:#ff4f4f;">Failed to sync with Nebula Core</td></tr>';
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
    if (!consoleContainerId || consoleMode !== 'logs') return;
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

function updateConsoleModeUi() {
    const logsBtn = document.getElementById('consoleLogsModeBtn');
    const shellBtn = document.getElementById('consoleShellModeBtn');
    const refreshBtn = document.querySelector('#consoleModal button[onclick="refreshConsoleLogs()"]');
    const hint = document.getElementById('consoleModeHint');
    const input = document.getElementById('consoleAttachInput');
    const ctrlC = document.getElementById('consoleCtrlCBtn');
    if (logsBtn) logsBtn.classList.toggle('is-active', consoleMode === 'logs');
    if (shellBtn) {
        shellBtn.classList.toggle('is-active', consoleMode === 'shell');
        shellBtn.style.display = isStaff ? 'inline-flex' : 'none';
    }
    if (refreshBtn) refreshBtn.style.display = consoleMode === 'logs' ? 'inline-flex' : 'none';
    if (hint) {
        hint.textContent = consoleMode === 'shell'
            ? 'Persistent admin shell session. State stays inside the running PTY until you close it.'
            : 'Live log stream with optional app console input.';
    }
    if (input) {
        input.placeholder = consoleMode === 'shell'
            ? 'Send input into live shell session'
            : 'Attach input to container console';
    }
    if (ctrlC) ctrlC.style.display = consoleMode === 'shell' ? 'inline-flex' : 'none';
}

function emitPtyResize() {
    if (!containersSocket || !consolePtySessionId || consoleMode !== 'shell') return;
    const cols = Math.max(80, Math.min(220, Math.floor((window.innerWidth || 1280) / 9)));
    const rows = Math.max(18, Math.min(60, Math.floor((window.innerHeight || 900) / 24)));
    containersSocket.emit('container_pty_resize', { session_id: consolePtySessionId, cols, rows });
}

function startConsolePtySession() {
    if (!isStaff || !consoleContainerId || !containersSocket) return;
    const output = document.getElementById('consoleOutput');
    const status = document.getElementById('consoleStatus');
    if (consolePtySessionId) {
        if (status) status.textContent = 'Interactive shell is already attached.';
        return;
    }
    if (output) output.textContent = '';
    if (status) status.textContent = 'Starting interactive shell...';
    const cols = Math.max(80, Math.min(220, Math.floor((window.innerWidth || 1280) / 9)));
    const rows = Math.max(18, Math.min(60, Math.floor((window.innerHeight || 900) / 24)));
    containersSocket.emit('start_container_pty', { container_id: consoleContainerId, cols, rows });
}

function stopConsolePtySession() {
    if (!containersSocket || !consolePtySessionId) return;
    containersSocket.emit('stop_container_pty', { session_id: consolePtySessionId });
    consolePtySessionId = null;
}

function switchConsoleMode(mode = 'logs') {
    const nextMode = mode === 'shell' ? 'shell' : 'logs';
    if (nextMode === 'shell' && !isStaff) return;
    if (!consoleContainerId) return;
    if (consoleMode === nextMode) {
        if (consoleMode === 'shell') emitPtyResize();
        return;
    }
    const output = document.getElementById('consoleOutput');
    const status = document.getElementById('consoleStatus');
    consoleMode = nextMode;
    updateConsoleModeUi();
    if (consoleMode === 'shell') {
        if (!containersSocket) {
            consoleMode = 'logs';
            updateConsoleModeUi();
            if (status) status.textContent = 'Interactive shell requires live socket transport';
            return;
        }
        if (containersSocket) {
            containersSocket.emit('unsubscribe_container_stream', { container_id: consoleContainerId });
        } else if (consolePollInterval) {
            clearInterval(consolePollInterval);
            consolePollInterval = null;
        }
        if (output) output.textContent = '';
        startConsolePtySession();
        return;
    }
    stopConsolePtySession();
    if (output) output.textContent = '';
    if (status) status.textContent = 'Switching back to live logs...';
    if (containersSocket) {
        containersSocket.emit('subscribe_container_stream', { container_id: consoleContainerId });
    } else {
        refreshConsoleLogs();
        consolePollInterval = setInterval(refreshConsoleLogs, 4000);
    }
}

function openConsoleModal(containerId, containerName) {
    consoleContainerId = containerId;
    consoleContainerName = containerName;
    consoleMode = 'logs';
    consolePtySessionId = null;
    document.getElementById('consoleTitle').textContent = `Container Console: ${containerName}`;
    document.getElementById('consoleSub').textContent = `Container ID: ${containerId}`;
    document.getElementById('consoleModal').style.display = 'flex';
    updateConsoleModeUi();
    if (consolePollInterval) clearInterval(consolePollInterval);
    if (containersSocket) {
        document.getElementById('consoleStatus').textContent = 'Connecting live stream...';
        containersSocket.emit('subscribe_container_stream', { container_id: containerId });
    } else {
        refreshConsoleLogs();
        consolePollInterval = setInterval(refreshConsoleLogs, 4000);
    }
}

function closeConsoleModal() {
    document.getElementById('consoleModal').style.display = 'none';
    if (consolePollInterval) clearInterval(consolePollInterval);
    consolePollInterval = null;
    if (containersSocket) {
        containersSocket.emit('unsubscribe_container_stream', { container_id: consoleContainerId });
    }
    stopConsolePtySession();
    consoleContainerId = null;
    consoleContainerName = '';
    consoleMode = 'logs';
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
            closeWebsiteWizard();
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

async function submitDeployRequest(data, btn, busyText = 'Deploying...', idleText = 'Deploy Instance', onSuccess = null) {
    if (!isStaff) return false;
    if (!data?.name || !data?.image) {
        alert('Instance name and Image are required');
        return false;
    }
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="bi bi-arrow-repeat spin"></i> ${busyText}`;
    }
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
            if (typeof onSuccess === 'function') onSuccess();
            return true;
        } else {
            const err = await res.json();
            closeDeployProgressModal();
            const errorPayload = (err && err.detail) ? err.detail : { summary: 'Deployment failed', raw_error: JSON.stringify(err || {}) };
            openDeployErrorModal(errorPayload, []);
            return false;
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
        return false;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = idleText;
        }
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
    data.project_ids = Array.from(document.getElementById('cont_project_ids')?.selectedOptions || []).map(o => String(o.value));
    await submitDeployRequest(data, btn, 'Deploying...', 'Deploy Instance');
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
        project_protocol: document.getElementById('cont_project_protocol').value,
        install_command: document.getElementById('cont_install_command').value,
        command: document.getElementById('cont_command').value,
        env: document.getElementById('cont_env').value,
        volumes: document.getElementById('cont_volumes').value,
        domain_name: document.getElementById('cont_domain_name').value,
        launch_url: document.getElementById('cont_launch_url').value,
        workspace_mount: activePresetConfig.workspace_mount || '',
        explorer_root: activePresetConfig.explorer_root || '',
        console_cwd: activePresetConfig.console_cwd || '',
        profile_name: activePresetConfig.profile_name || activePreset || '',
        restart: document.getElementById('cont_restart').checked,
        preset: activePreset
    };
}

function collectWebsiteWizardForm() {
    const config = websiteWizardConfig();
    const presetName = websiteWizardResolvedPreset(config);
    const preset = containerPresets[presetName] || {};
    const publicPort = websiteWizardPublicPort();
    const internalPort = Number(config.internalPort || 80);
    const installCommand = String(document.getElementById('site_install_command')?.value || '').trim();
    const startupCommand = String(document.getElementById('site_start_command')?.value || '').trim();
    const ram = parseInt(document.getElementById('site_ram')?.value || '', 10);
    const disk = parseInt(document.getElementById('site_disk')?.value || '', 10);
    const domain = String(document.getElementById('site_domain')?.value || '').trim();
    const route = `${publicPort}:${internalPort}`;
    return {
        name: String(document.getElementById('site_name')?.value || '').trim(),
        image: preset.image || config.image,
        ram: Number.isFinite(ram) ? ram : (preset.ram || config.ram || 1024),
        swap: preset.swap ?? ((preset.ram || config.ram || 1024) * 2),
        disk: Number.isFinite(disk) ? disk : (preset.disk || config.disk || 20),
        cpu: preset.cpu ?? 1024,
        cpu_limit: preset.cpu_limit ?? 1,
        cpuset: preset.cpuset || '',
        cpu_quota: preset.cpu_quota || '',
        cpu_period: preset.cpu_period || '',
        pids_limit: preset.pids_limit ?? 256,
        shm: preset.shm ?? 128,
        ports: route,
        project_protocol: config.protocol,
        install_command: installCommand,
        command: startupCommand,
        env: preset.env || '',
        volumes: '',
        domain_name: domain,
        launch_url: domain ? websiteWizardPreviewUrl() : '',
        workspace_mount: preset.workspace_mount || activePresetConfig.workspace_mount || '/app',
        explorer_root: preset.explorer_root || activePresetConfig.explorer_root || '/app',
        console_cwd: preset.console_cwd || activePresetConfig.console_cwd || '/app',
        profile_name: preset.profile_name || config.protocol,
        restart: !!document.getElementById('site_restart')?.checked,
        preset: presetName,
        users: [],
        user_assignments: [],
        role_permissions: presetPermissionTemplates[presetName] ? cloneRoleMatrix(presetPermissionTemplates[presetName]) : cloneRoleMatrix({}),
        project_ids: []
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
    if (!menu) return;
    document.querySelectorAll('.dropdown-content').forEach(d => { if(d !== menu) d.classList.remove('show'); });
    menu.classList.toggle('show');
}

function renderDockerObjectsSummary(data) {
    const host = document.getElementById('docker_objects_summary');
    if (!host) return;
    const counts = data?.counts || {};
    host.innerHTML = `
        <div class="settings-ops-card"><span>Images</span><strong>${escapeHtml(String(counts.images ?? 0))}</strong></div>
        <div class="settings-ops-card"><span>Volumes</span><strong>${escapeHtml(String(counts.volumes ?? 0))}</strong></div>
        <div class="settings-ops-card"><span>Networks</span><strong>${escapeHtml(String(counts.networks ?? 0))}</strong></div>
    `;
}

function renderAdvancedDockerEvents(data) {
    const host = document.getElementById('advanced_events_list');
    if (!host) return;
    const items = Array.isArray(data?.events) ? data.events : [];
    host.textContent = items.length
        ? items.map((item) => {
            const ts = item.time ? new Date(item.time * 1000).toLocaleTimeString() : '--:--:--';
            return `[${ts}] ${item.type || 'container'}:${item.action || 'unknown'} ${item.name || item.id || ''}`.trim();
        }).join('\n')
        : 'No recent Docker events.';
}

function renderAdvancedInspect(bundle) {
    editInspectBundle = bundle || null;
    const factsHost = document.getElementById('advanced_runtime_facts');
    const preview = document.getElementById('advanced_inspect_preview');
    if (factsHost) factsHost.innerHTML = '';
    if (preview) preview.textContent = String(bundle?.raw_json || 'No inspect payload available.');
    if (!bundle) return;

    const state = bundle.state || {};
    const health = state.health || {};
    if (factsHost) {
        factsHost.innerHTML = [
            ['Status', state.status || 'unknown'],
            ['Health', health.status || 'n/a'],
            ['Networks', Array.isArray(bundle.networks) ? String(bundle.networks.length) : '0'],
            ['Mounts', Array.isArray(bundle.mounts) ? String(bundle.mounts.length) : '0'],
        ].map(([label, value]) => `
            <div class="settings-fact-item">
                <span>${escapeHtml(label)}</span>
                <strong>${escapeHtml(String(value || '—'))}</strong>
            </div>
        `).join('');
    }

    const blueprint = bundle.blueprint || {};
    const setValue = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.value = String(value || '');
    };
    setValue('advanced_env_editor', blueprint.env || '');
    setValue('advanced_mounts_editor', blueprint.mounts || '');
    setValue('advanced_labels_editor', blueprint.labels || '');
    setValue('advanced_network_name', blueprint.network || '');
    setValue('advanced_health_test', blueprint.healthcheck?.test || '');
    setValue('advanced_health_interval', blueprint.healthcheck?.interval_seconds || '');
    setValue('advanced_health_timeout', blueprint.healthcheck?.timeout_seconds || '');
    setValue('advanced_health_retries', blueprint.healthcheck?.retries || '');
    setValue('advanced_health_start_period', blueprint.healthcheck?.start_period_seconds || '');
    if (document.getElementById('inspectDrawer')?.classList.contains('open')) {
        openInspectDrawer(bundle);
    }
}

function collectAdvancedOverridePayload() {
    return {
        name: '',
        env: String(document.getElementById('advanced_env_editor')?.value || ''),
        mounts: String(document.getElementById('advanced_mounts_editor')?.value || ''),
        labels: String(document.getElementById('advanced_labels_editor')?.value || ''),
        network: String(document.getElementById('advanced_network_name')?.value || '').trim(),
        healthcheck: {
            test: String(document.getElementById('advanced_health_test')?.value || '').trim(),
            interval_seconds: Number(document.getElementById('advanced_health_interval')?.value || 0),
            timeout_seconds: Number(document.getElementById('advanced_health_timeout')?.value || 0),
            retries: Number(document.getElementById('advanced_health_retries')?.value || 0),
            start_period_seconds: Number(document.getElementById('advanced_health_start_period')?.value || 0),
        }
    };
}

async function refreshAdvancedDockerOps() {
    if (!isStaff || !editContainerId) return;
    try {
        const [objects, events, inspect] = await Promise.all([
            apiJson('/api/containers/docker-objects?limit=12'),
            apiJson('/api/containers/docker-events?limit=30'),
            apiJson(`/api/containers/inspect/${encodeURIComponent(editContainerId)}`),
        ]);
        renderDockerObjectsSummary(objects);
        renderAdvancedDockerEvents(events);
        renderAdvancedInspect(inspect);
    } catch (e) {
        const preview = document.getElementById('advanced_inspect_preview');
        if (preview) preview.textContent = `Failed to load advanced Docker data: ${e.message}`;
    }
}

async function duplicateContainerFromSettings() {
    if (!isStaff || !editContainerId) return;
    const name = prompt('New container name for duplicate:', `${document.getElementById('edit_cont_name')?.value || editContainerId}-copy`);
    if (!name) return;
    const payload = collectAdvancedOverridePayload();
    payload.name = name;
    try {
        await apiJson(`/api/containers/duplicate/${encodeURIComponent(editContainerId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        fetchContainers(true);
        alert('Container duplicated.');
    } catch (e) {
        alert(`Duplicate failed: ${e.message}`);
    }
}

async function recreateContainerFromSettings() {
    if (!isStaff || !editContainerId) return;
    openRecreateDiffModal();
}

function renderInspectTreeNode(value, label = '') {
    const wrap = document.createElement('div');
    if (label) {
        const head = document.createElement('div');
        head.innerHTML = `<span class="inspect-tree-key">${escapeHtml(label)}</span>`;
        wrap.appendChild(head);
    }
    if (Array.isArray(value)) {
        value.forEach((item, index) => {
            const node = document.createElement('div');
            node.className = 'inspect-tree-node';
            node.appendChild(renderInspectTreeNode(item, `[${index}]`));
            wrap.appendChild(node);
        });
        return wrap;
    }
    if (value && typeof value === 'object') {
        Object.entries(value).forEach(([key, itemValue]) => {
            const node = document.createElement('div');
            node.className = 'inspect-tree-node';
            node.appendChild(renderInspectTreeNode(itemValue, key));
            wrap.appendChild(node);
        });
        return wrap;
    }
    const leaf = document.createElement('div');
    leaf.innerHTML = `${label ? '' : ''}<span class="inspect-tree-value">${escapeHtml(String(value ?? 'null'))}</span>`;
    wrap.appendChild(leaf);
    return wrap;
}

function setInspectViewMode(mode) {
    inspectViewMode = mode === 'json' ? 'json' : 'tree';
    const tree = document.getElementById('inspectTreeView');
    const json = document.getElementById('inspectJsonView');
    if (tree) tree.style.display = inspectViewMode === 'tree' ? 'block' : 'none';
    if (json) json.style.display = inspectViewMode === 'json' ? 'block' : 'none';
}

function openInspectDrawer(bundle = null) {
    const data = bundle || editInspectBundle;
    if (!data) return;
    const drawer = document.getElementById('inspectDrawer');
    const title = document.getElementById('inspectDrawerTitle');
    const sub = document.getElementById('inspectDrawerSub');
    const tree = document.getElementById('inspectTreeView');
    const json = document.getElementById('inspectJsonView');
    if (!drawer || !tree || !json) return;
    title.textContent = `Container Inspect: ${data.name || data.id || 'container'}`;
    sub.textContent = `ID: ${data.id || 'unknown'} • Image: ${data.image || 'unknown'}`;
    tree.innerHTML = '';
    const treePayload = { ...data };
    delete treePayload.raw_json;
    tree.appendChild(renderInspectTreeNode(treePayload, 'inspect'));
    json.textContent = String(data.raw_json || '');
    drawer.classList.add('open');
    setInspectViewMode(inspectViewMode);
}

function openInspectDrawerFromConsole() {
    openInspectDrawer(editInspectBundle);
}

function closeInspectDrawer() {
    document.getElementById('inspectDrawer')?.classList.remove('open');
}

function normalizeDiffText(value) {
    return String(value || '').trim();
}

function buildRecreateDiffSections() {
    const current = editInspectBundle?.blueprint || {};
    const next = collectAdvancedOverridePayload();
    const sections = [
        ['Environment', normalizeDiffText(current.env), normalizeDiffText(next.env)],
        ['Labels', normalizeDiffText(current.labels), normalizeDiffText(next.labels)],
        ['Mounts', normalizeDiffText(current.mounts), normalizeDiffText(next.mounts)],
        ['Network', normalizeDiffText(current.network), normalizeDiffText(next.network)],
        ['Healthcheck', normalizeDiffText(JSON.stringify(current.healthcheck || {}, null, 2)), normalizeDiffText(JSON.stringify(next.healthcheck || {}, null, 2))],
    ];
    return sections.filter(([, before, after]) => before !== after);
}

function openRecreateDiffModal() {
    const modal = document.getElementById('recreateDiffModal');
    const host = document.getElementById('recreateDiffContent');
    if (!modal || !host) return;
    const sections = buildRecreateDiffSections();
    pendingRecreatePayload = collectAdvancedOverridePayload();
    if (!sections.length) {
        host.innerHTML = '<div class="input-hint">No advanced changes detected. Recreate will keep the same env, mounts, labels, network and healthcheck.</div>';
    } else {
        host.innerHTML = sections.map(([label, before, after]) => `
            <div class="recreate-diff-card">
                <div class="recreate-diff-head">${escapeHtml(label)}</div>
                <div class="recreate-diff-body">
                    <div class="recreate-diff-pane">
                        <span>Current</span>
                        <pre>${escapeHtml(before || '(empty)')}</pre>
                    </div>
                    <div class="recreate-diff-pane">
                        <span>Proposed</span>
                        <pre>${escapeHtml(after || '(empty)')}</pre>
                    </div>
                </div>
            </div>
        `).join('');
    }
    modal.style.display = 'flex';
}

function closeRecreateDiffModal() {
    document.getElementById('recreateDiffModal').style.display = 'none';
    pendingRecreatePayload = null;
}

async function confirmRecreateFromDiff() {
    if (!isStaff || !editContainerId) return;
    const payload = pendingRecreatePayload || collectAdvancedOverridePayload();
    try {
        await apiJson(`/api/containers/recreate/${encodeURIComponent(editContainerId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        closeRecreateDiffModal();
        closeContainerSettingsModal();
        fetchContainers(true);
    } catch (e) {
        alert(`Recreate failed: ${e.message}`);
    }
}

function sendConsoleAttachInput() {
    const input = document.getElementById('consoleAttachInput');
    const status = document.getElementById('consoleStatus');
    const rawValue = String(input?.value || '');
    const command = rawValue.trim();
    if (!consoleContainerId || !command) return;
    if (consoleMode === 'shell') {
        if (!containersSocket || !consolePtySessionId) {
            if (status) status.textContent = 'Interactive shell is not connected yet';
            return;
        }
        containersSocket.emit('container_pty_input', { session_id: consolePtySessionId, data: `${rawValue}\n` });
        if (input) input.value = '';
        if (status) status.textContent = 'Shell input sent...';
        return;
    }
    if (containersSocket) {
        containersSocket.emit('container_attach_input', { container_id: consoleContainerId, command });
        if (input) input.value = '';
        if (status) status.textContent = 'Attach input sent...';
        return;
    }
    fetch(`/api/containers/console-send/${encodeURIComponent(consoleContainerId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command }),
    }).then(() => {
        if (input) input.value = '';
        if (status) status.textContent = 'Attach input sent';
    }).catch(() => {
        if (status) status.textContent = 'Attach input failed';
    });
}

function sendConsoleCtrlC() {
    const status = document.getElementById('consoleStatus');
    if (consoleMode !== 'shell' || !containersSocket || !consolePtySessionId) return;
    containersSocket.emit('container_pty_input', { session_id: consolePtySessionId, data: '\u0003' });
    if (status) status.textContent = 'Sent Ctrl+C to shell session';
}

async function openContainerSettingsModal(containerId) {
    if (!isStaff) return;
    const id = String(containerId || '').trim();
    if (!id) return;

    const modal = document.getElementById('containerSettingsModal');
    if (!modal) return;
    modal.style.display = 'flex';

    document.getElementById('settingsModalTitle').textContent = 'Environment Settings';
    document.getElementById('settingsModalSub').textContent = 'Loading current settings...';
    document.getElementById('edit_cont_name').value = '';
    document.getElementById('edit_cont_id').value = id;
    document.getElementById('edit_cont_image').value = '';
    document.getElementById('edit_cont_command').value = '';
    document.getElementById('edit_cont_ports').value = '';
    document.getElementById('edit_ports_search').value = '';
    editAvailablePortRules = [];
    renderEditPortsSelection([], []);
    renderDockerObjectsSummary(null);
    renderAdvancedDockerEvents(null);
    renderAdvancedInspect(null);
    document.getElementById('edit_restart_policy').value = 'no';
    document.getElementById('edit_restart_retries').value = '0';
    editSelectedUsers = new Map();
    editRolePermissionMatrix = cloneRoleMatrix({});
    renderEditRolePermissionMatrix();
    renderEditUserPicker('');

    if (!assignableUsers.length) {
        await loadAssignableUsers().catch(() => {});
    }

    try {
        const [detail, settings, policy, perms] = await Promise.all([
            apiJson(`/api/containers/detail/${encodeURIComponent(id)}`),
            apiJson(`/api/containers/settings/${encodeURIComponent(id)}`),
            apiJson(`/api/containers/restart-policy/${encodeURIComponent(id)}`),
            apiJson(`/api/containers/permissions/${encodeURIComponent(id)}`)
        ]);

        editContainerId = String(detail.full_id || detail.id || id);
        const containerName = String(detail.name || detail.id || id);
        document.getElementById('settingsModalTitle').textContent = `Environment Settings: ${containerName}`;
        document.getElementById('settingsModalSub').textContent = `Edit runtime behavior, network exposure and access policies for ${editContainerId}`;
        document.getElementById('edit_cont_name').value = containerName;
        document.getElementById('edit_cont_id').value = editContainerId;
        document.getElementById('edit_cont_image').value = String(detail.image || 'unknown');
        document.getElementById('edit_cont_command').value = String(settings.startup_command || '');
        document.getElementById('edit_cont_ports').value = String(settings.allowed_ports || '');
        editAvailablePortRules = Array.isArray(settings.available_ports) ? settings.available_ports.map(v => String(v || '').trim()).filter(Boolean) : [];
        renderEditPortsSelection(editAvailablePortRules, String(settings.allowed_ports || '').split(',').map(v => v.trim()).filter(Boolean));
        document.getElementById('edit_restart_policy').value = String(policy.restart_policy || 'no');
        document.getElementById('edit_restart_retries').value = String(asNumber(policy.maximum_retry_count, 0));

        const rolePolicies = (perms && typeof perms.role_policies === 'object') ? perms.role_policies : {};
        editRolePermissionMatrix = cloneRoleMatrix(rolePolicies);
        renderEditRolePermissionMatrix();

        const assignments = Array.isArray(perms?.user_assignments) ? perms.user_assignments : [];
        if (assignments.length > 0) {
            assignments.forEach(item => {
                const username = String(item?.username || '').trim();
                if (!username) return;
                const db = String(item?.db_name || item?.db || 'system.db').trim() || 'system.db';
                const roleTag = String(item?.role_tag || 'user').trim().toLowerCase() || 'user';
                editSelectedUsers.set(selectedUserKey({ username, db }), { username, db, role_tag: roleTag });
            });
        } else {
            (Array.isArray(detail.users) ? detail.users : []).forEach(raw => {
                const parsed = parseLegacyUserLabel(raw);
                if (!parsed || !parsed.username) return;
                editSelectedUsers.set(
                    selectedUserKey({ username: parsed.username, db: parsed.db }),
                    { username: parsed.username, db: parsed.db, role_tag: parsed.role_tag || 'user' }
                );
            });
        }
        renderEditUserPicker('');
        renderSettingsModalAccessChips(editRolePermissionMatrix);
        await refreshAdvancedDockerOps();
        if (advancedOpsPollInterval) clearInterval(advancedOpsPollInterval);
        advancedOpsPollInterval = setInterval(refreshAdvancedDockerOps, 12000);
    } catch (e) {
        editContainerId = null;
        alert(`Failed to load container settings: ${e.message}`);
    }
}

function closeContainerSettingsModal() {
    const modal = document.getElementById('containerSettingsModal');
    if (modal) modal.style.display = 'none';
    closeRecreateDiffModal();
    editContainerId = null;
    editInspectBundle = null;
    editSelectedUsers = new Map();
    if (advancedOpsPollInterval) {
        clearInterval(advancedOpsPollInterval);
        advancedOpsPollInterval = null;
    }
}

async function saveContainerSettingsModal() {
    if (!isStaff || !editContainerId) return;
    const btn = document.getElementById('saveSettingsBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-arrow-repeat spin"></i> Saving...';
    }

    try {
        const settingsPayload = {
            startup_command: String(document.getElementById('edit_cont_command').value || ''),
            allowed_ports: Array.from(document.querySelectorAll('#edit_ports_selection input[type="checkbox"]:checked')).map(node => node.value).join(', ')
                || String(document.getElementById('edit_cont_ports').value || '')
        };
        const policyPayload = {
            restart_policy: String(document.getElementById('edit_restart_policy').value || 'no'),
            maximum_retry_count: Math.max(0, parseInt(document.getElementById('edit_restart_retries').value || '0', 10) || 0)
        };
        const permissionsPayload = {
            role_policies: editRolePermissionMatrix,
            user_assignments: Array.from(editSelectedUsers.values()).map(u => ({
                username: u.username,
                db_name: u.db || 'system.db',
                role_tag: u.role_tag || 'user'
            }))
        };

        const encodedId = encodeURIComponent(editContainerId);
        await apiJson(`/api/containers/settings/${encodedId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settingsPayload)
        });
        await apiJson(`/api/containers/restart-policy/${encodedId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(policyPayload)
        });
        await apiJson(`/api/containers/permissions/${encodedId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(permissionsPayload)
        });

        closeContainerSettingsModal();
        fetchContainers(true);
    } catch (e) {
        alert(`Save failed: ${e.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Save Changes';
        }
    }
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
    document.getElementById('cont_project_protocol').value = 'generic';
    document.getElementById('cont_install_command').value = '';
    document.getElementById('cont_command').value = '';
    document.getElementById('cont_env').value = '';
    document.getElementById('cont_volumes').value = '';
    document.getElementById('cont_domain_name').value = '';
    document.getElementById('cont_launch_url').value = '';
    const projectSelect = document.getElementById('cont_project_ids');
    if (projectSelect) projectSelect.innerHTML = '';
    activePresetConfig = {};
    rolePermissionMatrix = cloneRoleMatrix({});
    renderRolePermissionMatrix();
    populateArchitectProtocolOptions('generic');
    onArchitectProtocolChange(false);
    updateArchitectLaunchPreview();
    updateArchitectVolumeAdvisory();
    const select = document.getElementById('cont_preset_select');
    const firstPreset = select && select.options.length > 0 ? select.options[0].value : '';
    if (firstPreset) {
        applyContainerPreset(firstPreset);
    }
    renderUserPicker('');
    loadAvailableProjects().catch(() => {});
    document.getElementById('containerModal').style.display = 'flex';
}

function closeModal() { document.getElementById('containerModal').style.display = 'none'; }

function openWebsiteWizard() {
    if (!isStaff) return;
    const nameEl = document.getElementById('site_name');
    const typeEl = document.getElementById('site_type');
    const portEl = document.getElementById('site_public_port');
    const domainEl = document.getElementById('site_domain');
    const installEl = document.getElementById('site_install_command');
    const startEl = document.getElementById('site_start_command');
    const ramEl = document.getElementById('site_ram');
    const diskEl = document.getElementById('site_disk');
    const restartEl = document.getElementById('site_restart');
    const architectToggle = document.getElementById('site_open_architect');
    const firstType = Object.keys(websiteWizardDefinitions)[0] || 'generic';
    if (nameEl) nameEl.value = '';
    if (typeEl) typeEl.value = firstType;
    if (portEl) portEl.value = '';
    if (domainEl) domainEl.value = '';
    if (installEl) installEl.value = '';
    if (startEl) startEl.value = '';
    if (ramEl) ramEl.value = '';
    if (diskEl) diskEl.value = '';
    if (restartEl) restartEl.checked = true;
    if (architectToggle) architectToggle.checked = false;
    syncWebsiteWizardFields(true);
    updateWebsiteWizardPreview();
    document.getElementById('websiteWizardModal').style.display = 'flex';
}

function closeWebsiteWizard() {
    const modal = document.getElementById('websiteWizardModal');
    if (modal) modal.style.display = 'none';
}

function applyWebsiteWizardToArchitect(data) {
    openCreateModal();
    document.getElementById('cont_name').value = data.name || '';
    document.getElementById('cont_ports').value = data.ports || '';
    document.getElementById('cont_domain_name').value = data.domain_name || '';
    document.getElementById('cont_launch_url').value = data.launch_url || '';
    document.getElementById('cont_install_command').value = data.install_command || '';
    document.getElementById('cont_command').value = data.command || '';
    document.getElementById('cont_ram_input').value = String(data.ram || 1024);
    document.getElementById('cont_disk').value = String(data.disk || 20);
    document.getElementById('cont_restart').checked = !!data.restart;
    if (data.preset && containerPresets[data.preset]) {
        applyContainerPreset(data.preset);
        document.getElementById('cont_name').value = data.name || '';
        document.getElementById('cont_ports').value = data.ports || '';
        document.getElementById('cont_domain_name').value = data.domain_name || '';
        document.getElementById('cont_launch_url').value = data.launch_url || '';
        document.getElementById('cont_install_command').value = data.install_command || '';
        document.getElementById('cont_command').value = data.command || '';
        document.getElementById('cont_ram_input').value = String(data.ram || 1024);
        document.getElementById('cont_disk').value = String(data.disk || 20);
        document.getElementById('cont_restart').checked = !!data.restart;
    } else {
        document.getElementById('cont_image').value = data.image || '';
        document.getElementById('cont_project_protocol').value = data.project_protocol || 'generic';
    }
    onArchitectProtocolChange(false);
    updateArchitectLaunchPreview();
    closeWebsiteWizard();
}

async function deployWebsiteWizard() {
    if (!isStaff) return;
    const btn = document.getElementById('siteDeployBtn');
    const data = collectWebsiteWizardForm();
    const validationEl = document.getElementById('site_wizard_validation');
    if (!data.name) {
        if (validationEl) {
            validationEl.className = 'website-validation error';
            validationEl.textContent = 'Website name is required.';
        }
        return;
    }
    const publicPort = websiteWizardPublicPort();
    if (!Number.isFinite(publicPort) || publicPort < 1 || publicPort > 65535) {
        if (validationEl) {
            validationEl.className = 'website-validation error';
            validationEl.textContent = 'Public port must be between 1 and 65535.';
        }
        return;
    }
    if (!data.image) {
        if (validationEl) {
            validationEl.className = 'website-validation error';
            validationEl.textContent = 'No website image/preset is available for this type yet. Open full Container Architect instead.';
        }
        return;
    }
    if (document.getElementById('site_open_architect')?.checked) {
        applyWebsiteWizardToArchitect(data);
        return;
    }
    closeWebsiteWizard();
    await submitDeployRequest(data, btn, 'Deploying...', 'Deploy Website');
}

window.onclick = (e) => {
    document.querySelectorAll('.dropdown-content').forEach(d => d.classList.remove('show'));
    const websiteWizardModal = document.getElementById('websiteWizardModal');
    const containerModal = document.getElementById('containerModal');
    const containerSettingsModal = document.getElementById('containerSettingsModal');
    const consoleModal = document.getElementById('consoleModal');
    const deployModal = document.getElementById('deployProgressModal');
    const deployErrorModal = document.getElementById('deployErrorModal');
    const recreateDiffModal = document.getElementById('recreateDiffModal');
    if (websiteWizardModal && e.target === websiteWizardModal) closeWebsiteWizard();
    if (containerModal && e.target === containerModal) closeModal();
    if (containerSettingsModal && e.target === containerSettingsModal) closeContainerSettingsModal();
    if (consoleModal && e.target === consoleModal) closeConsoleModal();
    if (deployModal && e.target === deployModal) closeDeployProgressModal();
    if (deployErrorModal && e.target === deployErrorModal) closeDeployErrorModal();
    if (recreateDiffModal && e.target === recreateDiffModal) closeRecreateDiffModal();
};
document.addEventListener('DOMContentLoaded', initPage);
document.addEventListener('visibilitychange', onVisibilityChanged);
window.addEventListener('beforeunload', () => {
    if (metricsInterval) clearInterval(metricsInterval);
    if (containersInterval) clearInterval(containersInterval);
    if (consolePollInterval) clearInterval(consolePollInterval);
    if (deployPollInterval) clearInterval(deployPollInterval);
    if (advancedOpsPollInterval) clearInterval(advancedOpsPollInterval);
    if (metricsAbortController) metricsAbortController.abort();
    if (containersAbortController) containersAbortController.abort();
    if (containersSocket && consoleContainerId) {
        containersSocket.emit('unsubscribe_container_stream', { container_id: consoleContainerId });
    }
    if (containersSocket && consolePtySessionId) {
        containersSocket.emit('stop_container_pty', { session_id: consolePtySessionId });
    }
    window.removeEventListener('resize', emitPtyResize);
    document.removeEventListener('visibilitychange', onVisibilityChanged);
});
