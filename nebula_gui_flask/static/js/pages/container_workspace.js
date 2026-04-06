// nebula_gui_flask/static/js/pages/container_workspace.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

const workspaceContext = window.NebulaWorkspace || {};
const containerId = workspaceContext.containerId || '';
let activeTab = 'terminal';
let logsInterval = null;
let logsAutoEnabled = true;
let activeWorkspaceRoot = '/data';
let currentContainerImage = '';
let profilePolicy = null;
let accessPolicy = null;
let latestLogsText = '';
let consoleEvents = [];
let availablePorts = [];
let currentConsoleMode = 'console';
let activePreviewPath = '';
let currentExplorerEntries = [];
let selectedExplorerPath = '';
let selectedExplorerPaths = new Set();
let explorerAnchorPath = '';
let explorerClipboard = { mode: '', paths: [] };
let explorerContextPath = '';
let explorerSelectionMode = false;
let previewEditMode = false;
let previewDirty = false;
let latestAuditEntries = [];
let latestSftpInfo = null;
let explorerDragDepth = 0;
const WORKSPACE_UPLOAD_LIMIT_BYTES = 1024 * 1024 * 1024;

const capabilityDefinitions = [
    { key: 'allow_explorer', label: 'Explorer' },
    { key: 'allow_root_explorer', label: 'Root Explorer' },
    { key: 'allow_console', label: 'App Console' },
    { key: 'allow_shell', label: 'Shell' },
    { key: 'allow_settings', label: 'Environment Settings' },
    { key: 'allow_edit_files', label: 'Edit Files' },
    { key: 'allow_edit_startup', label: 'Edit Startup' },
    { key: 'allow_edit_ports', label: 'Edit Ports' }
];
const workspaceProtocolDefinitions = {
    generic: {
        label: 'Generic Project',
        install: '',
        startup: '',
        hint: 'Manual flow for custom apps or containers with a non-standard bootstrap process.',
        profiles: ['generic', 'web', 'python', 'steam', 'database', 'minecraft']
    },
    'node-npm': {
        label: 'Node.js / npm',
        install: 'npm install',
        startup: 'npm run start',
        hint: 'Standard Node.js app flow using package.json scripts.',
        profiles: ['web', 'generic']
    },
    'node-vite': {
        label: 'Vite Frontend',
        install: 'npm install',
        startup: 'npm run dev -- --host 0.0.0.0 --port 5173',
        hint: 'Frontend development server published for browser access.',
        profiles: ['web', 'generic']
    },
    'python-pip': {
        label: 'Python / requirements.txt',
        install: 'python -m pip install -r requirements.txt',
        startup: 'python app.py',
        hint: 'Simple Python entrypoint with dependencies installed from requirements.txt.',
        profiles: ['python', 'generic']
    },
    'python-flask': {
        label: 'Flask Site',
        install: 'python -m pip install -r requirements.txt',
        startup: 'python -m flask --app app:app run --host=0.0.0.0 --port ${PORT:-5000}',
        hint: 'Flask app served from app:app using the published container port.',
        profiles: ['python', 'web', 'generic']
    },
    'python-fastapi': {
        label: 'FastAPI / Uvicorn',
        install: 'python -m pip install -r requirements.txt',
        startup: 'uvicorn app.main:app --host 0.0.0.0 --port 8000',
        hint: 'FastAPI service started through uvicorn on port 8000.',
        profiles: ['python', 'web', 'generic']
    },
    'python-django': {
        label: 'Django App',
        install: 'python -m pip install -r requirements.txt',
        startup: 'python manage.py runserver 0.0.0.0:8000',
        hint: 'Django project served with a public bind for quick access.',
        profiles: ['python', 'web', 'generic']
    },
    'static-web': {
        label: 'Static Web Content',
        install: '',
        startup: '',
        hint: 'Use the container image default entrypoint for static content.',
        profiles: ['web', 'generic']
    }
};

function canManageFileContent() {
    const isStaff = !!(profilePolicy && profilePolicy.is_staff);
    if (isStaff) return true;
    return !!(accessPolicy && accessPolicy.allow_edit_files === true);
}

function canDownloadFileContent() {
    const isStaff = !!(profilePolicy && profilePolicy.is_staff);
    if (isStaff) return true;
    return !(accessPolicy && accessPolicy.allow_explorer === false);
}

function updateFilePreviewActions() {
    const editBtn = document.getElementById('preview-edit-btn');
    const downloadBtn = document.getElementById('preview-download-btn');
    const saveBtn = document.getElementById('preview-save-btn');
    const hasPath = !!activePreviewPath;
    const canManage = canManageFileContent();
    const canDownload = canDownloadFileContent();
    if (editBtn) editBtn.disabled = !hasPath || !canManage;
    if (downloadBtn) downloadBtn.disabled = !hasPath || !canDownload;
    if (saveBtn) saveBtn.disabled = !hasPath || !canManage || !previewEditMode || !previewDirty;
}

function currentExplorerPath() {
    return String(document.getElementById('files-path')?.value || activeWorkspaceRoot || '/data').trim() || '/data';
}

function getSelectedExplorerPaths() {
    if (selectedExplorerPaths.size) return Array.from(selectedExplorerPaths);
    if (selectedExplorerPath) return [selectedExplorerPath];
    return [];
}

function updateExplorerSelectionModeUi() {
    const list = document.getElementById('files-list');
    const batchBar = document.getElementById('explorer-batch-bar');
    const toggleBtn = document.getElementById('explorer-select-mode-btn');
    if (list) list.classList.toggle('selection-mode', explorerSelectionMode);
    if (batchBar) batchBar.classList.toggle('visible', explorerSelectionMode);
    if (toggleBtn) {
        toggleBtn.classList.toggle('active', explorerSelectionMode);
        toggleBtn.innerHTML = explorerSelectionMode
            ? '<i class="bi bi-x-lg"></i> Exit Selection'
            : '<i class="bi bi-check2-square"></i> Select';
    }
}

function toggleExplorerSelectionMode(forceValue = null) {
    explorerSelectionMode = typeof forceValue === 'boolean' ? forceValue : !explorerSelectionMode;
    if (!explorerSelectionMode && selectedExplorerPaths.size > 1) {
        const primary = getPrimarySelectedExplorerPath();
        setSelectedExplorerPath(primary);
    }
    updateExplorerSelectionModeUi();
    updateExplorerSelectionStatus();
}

function renderExplorerBreadcrumbs(path = currentExplorerPath()) {
    const host = document.getElementById('explorer-breadcrumbs');
    if (!host) return;
    const raw = String(path || '/').trim() || '/';
    const root = String(activeWorkspaceRoot || '/').trim() || '/';
    let parts = raw.split('/').filter(Boolean);
    if (root !== '/' && raw.startsWith(root)) {
        parts = raw.slice(root.length).split('/').filter(Boolean);
    }
    const crumbs = [{ label: 'Workspace', path: root }];
    let cursor = root === '/' ? '' : root;
    parts.forEach((part) => {
        cursor = `${cursor}/${part}`.replace(/\/+/g, '/');
        crumbs.push({ label: part, path: cursor || '/' });
    });
    host.innerHTML = '';
    crumbs.forEach((crumb, index) => {
        if (index > 0) {
            const sep = document.createElement('span');
            sep.className = 'breadcrumb-separator';
            sep.innerHTML = '<i class="bi bi-chevron-right"></i>';
            host.appendChild(sep);
        }
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'breadcrumb-chip';
        btn.textContent = crumb.label;
        btn.onclick = () => {
            const pathEl = document.getElementById('files-path');
            if (pathEl) pathEl.value = crumb.path;
            loadFiles();
        };
        host.appendChild(btn);
    });
}

function updateExplorerSelectionStatus() {
    const el = document.getElementById('explorer-selection-status');
    if (!el) return;
    const selected = getSelectedExplorerPaths();
    if (!selected.length) {
        el.textContent = explorerSelectionMode
            ? 'Selection mode is active. Pick one or more files and folders for batch actions.'
            : 'Open folders with a double click, use the right mouse button for actions, or drop files here to upload.';
        return;
    }
    if (selected.length === 1) {
        const entry = currentExplorerEntries.find((item) => item.path === selected[0]);
        if (!entry) {
            el.textContent = 'Selection cleared.';
            return;
        }
        const clipboardText = explorerClipboard.paths.length
            ? ` Clipboard: ${explorerClipboard.mode === 'cut' ? 'cut' : 'copied'} ${explorerClipboard.paths.length} item(s).`
            : '';
        el.textContent = explorerSelectionMode
            ? `${entry.type === 'dir' ? 'Folder' : 'File'} ready for batch actions: ${selected[0]}.${clipboardText}`
            : `${entry.type === 'dir' ? 'Folder' : 'File'} selected: ${selected[0]}.${clipboardText}`;
        return;
    }
    const dirs = selected.filter((path) => currentExplorerEntries.find((item) => item.path === path && item.type === 'dir')).length;
    const files = selected.length - dirs;
    const clipboardText = explorerClipboard.paths.length
        ? ` Clipboard: ${explorerClipboard.mode === 'cut' ? 'cut' : 'copied'} ${explorerClipboard.paths.length} item(s).`
        : '';
    el.textContent = `${selected.length} items selected (${files} files, ${dirs} folders).${clipboardText}`;
}

function syncExplorerSelectionClasses() {
    document.querySelectorAll('.file-row').forEach((node) => {
        const nodePath = String(node.dataset.path || '').trim();
        node.classList.toggle('is-selected', selectedExplorerPaths.has(nodePath));
        node.classList.toggle('is-cut', explorerClipboard.mode === 'cut' && explorerClipboard.paths.includes(nodePath));
        const checkbox = node.querySelector('.file-select');
        if (checkbox) checkbox.checked = selectedExplorerPaths.has(nodePath);
    });
}

function setSelectedExplorerPaths(paths = [], anchorPath = '') {
    const clean = Array.from(new Set((Array.isArray(paths) ? paths : [paths])
        .map((item) => String(item || '').trim())
        .filter(Boolean)));
    selectedExplorerPaths = new Set(clean);
    selectedExplorerPath = clean[0] || '';
    explorerAnchorPath = String(anchorPath || clean[clean.length - 1] || '').trim();
    syncExplorerSelectionClasses();
    updateExplorerSelectionModeUi();
    updateExplorerSelectionStatus();
}

function setSelectedExplorerPath(path = '') {
    const clean = String(path || '').trim();
    setSelectedExplorerPaths(clean ? [clean] : [], clean);
}

function toggleExplorerPathSelection(path, { additive = false, range = false } = {}) {
    const target = String(path || '').trim();
    if (!target) {
        setSelectedExplorerPath('');
        return;
    }
    if (range && explorerAnchorPath) {
        const ordered = currentExplorerEntries.map((item) => item.path);
        const start = ordered.indexOf(explorerAnchorPath);
        const end = ordered.indexOf(target);
        if (start !== -1 && end !== -1) {
            const [from, to] = start <= end ? [start, end] : [end, start];
            const rangeItems = ordered.slice(from, to + 1);
            setSelectedExplorerPaths(rangeItems, target);
            return;
        }
    }
    if (additive) {
        const next = new Set(selectedExplorerPaths);
        if (next.has(target)) next.delete(target);
        else next.add(target);
        setSelectedExplorerPaths(Array.from(next), target);
        return;
    }
    setSelectedExplorerPaths([target], target);
}

function getPrimarySelectedExplorerPath() {
    const selected = getSelectedExplorerPaths();
    return selected[0] || '';
}

function getExplorerEntry(path) {
    return currentExplorerEntries.find((item) => item.path === path) || null;
}

function hideExplorerContextMenu() {
    const menu = document.getElementById('explorer-context-menu');
    if (!menu) return;
    menu.classList.remove('open');
    menu.setAttribute('aria-hidden', 'true');
}

function showExplorerContextMenu(event, path) {
    const menu = document.getElementById('explorer-context-menu');
    if (!menu) return;
    if (menu.parentElement !== document.body) {
        document.body.appendChild(menu);
    }
    const target = String(path || '').trim();
    explorerContextPath = target;
    if (!selectedExplorerPaths.has(target)) {
        setSelectedExplorerPath(target);
    }
    menu.classList.add('open');
    menu.setAttribute('aria-hidden', 'false');
    menu.style.visibility = 'hidden';
    menu.style.left = '0px';
    menu.style.top = '0px';

    const rect = menu.getBoundingClientRect();
    const pointerOffset = 6;
    const maxLeft = Math.max(12, window.innerWidth - rect.width - 12);
    const maxTop = Math.max(12, window.innerHeight - rect.height - 12);
    const left = Math.min(event.clientX + pointerOffset, maxLeft);
    const top = Math.min(event.clientY + pointerOffset, maxTop);

    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.visibility = 'visible';
}

function showToast(message, level = 'ok', lifeMs = 2600) {
    const zone = document.getElementById('toast-zone');
    const node = document.createElement('div');
    node.className = `toast ${level}`;
    node.textContent = message;
    zone.appendChild(node);
    setTimeout(() => node.remove(), lifeMs);
}

function setStatus(text) {
    const el = document.getElementById('workspace-context');
    if (el) el.textContent = text;
}

function formatLocalDateTime(value) {
    if (!value && value !== 0) return 'unknown time';
    const num = Number(value);
    const date = Number.isFinite(num) && String(value).length <= 13
        ? new Date(num * 1000)
        : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], {
        year: 'numeric',
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function renderCapabilitySummary(policy = accessPolicy || {}) {
    const profileBadge = document.getElementById('ws-profile-badge');
    const roleBadge = document.getElementById('ws-role-badge');
    const title = document.getElementById('env-profile-title');
    const summary = document.getElementById('env-permissions-summary');
    const grid = document.getElementById('env-permissions-grid');
    const explorerSummary = document.getElementById('explorer-access-summary');
    const explorerNote = document.getElementById('explorer-access-note');

    const profileName = (profilePolicy && (profilePolicy.label || profilePolicy.profile)) || 'Container profile';
    const roleName = String(policy.role_tag || 'user');
    if (profileBadge) profileBadge.textContent = `profile: ${profileName}`;
    if (roleBadge) roleBadge.textContent = `role: ${roleName}`;
    if (title) title.textContent = profileName;

    const allowedCount = capabilityDefinitions.filter((cap) => policy[cap.key] === true).length;
    const restrictedCount = capabilityDefinitions.length - allowedCount;
    if (summary) {
        summary.textContent = `${roleName} can use ${allowedCount} of ${capabilityDefinitions.length} environment capabilities. ${restrictedCount} actions remain restricted.`;
    }
    if (explorerSummary) {
        explorerSummary.textContent = policy.allow_explorer === false
            ? 'Explorer disabled'
            : (policy.allow_edit_files ? 'Read/write explorer access' : 'Read-only explorer access');
    }
    if (explorerNote) {
        explorerNote.textContent = policy.allow_explorer === false
            ? 'Your role cannot browse this workspace.'
            : (policy.allow_root_explorer ? 'Root-level workspace browsing is enabled for this role.' : 'Navigation stays inside approved workspace roots.');
    }

    if (!grid) return;
    grid.innerHTML = '';
    capabilityDefinitions.forEach((cap) => {
        const item = document.createElement('span');
        item.className = `capability-pill ${policy[cap.key] === true ? 'allowed' : 'restricted'}`;
        item.innerHTML = `<i class="bi ${policy[cap.key] === true ? 'bi-check2-circle' : 'bi-slash-circle'}"></i><span>${cap.label}</span>`;
        grid.appendChild(item);
    });
}

function describeAuditEntry(entry) {
    const action = String(entry?.action || 'container.event');
    const details = entry?.details && typeof entry.details === 'object' ? entry.details : {};
    const actor = String(entry?.actor || 'system');
    const detailText = Object.entries(details)
        .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '')
        .slice(0, 3)
        .map(([key, value]) => `${key}: ${String(value)}`)
        .join(' • ');
    return {
        title: action.replaceAll('.', ' '),
        meta: detailText || 'No additional metadata',
        actor
    };
}

function renderAuditLog() {
    const host = document.getElementById('workspace-audit-list');
    if (!host) return;
    if (!Array.isArray(latestAuditEntries) || latestAuditEntries.length === 0) {
        host.innerHTML = '<div class="audit-empty">No recent workspace activity recorded yet.</div>';
        return;
    }
    host.innerHTML = '';
    latestAuditEntries.forEach((entry) => {
        const view = describeAuditEntry(entry);
        const node = document.createElement('div');
        node.className = 'audit-entry';
        node.innerHTML = `
            <div class="audit-entry-head">
                <span></span>
                <span>${formatLocalDateTime(entry.created_at || entry.createdAt || entry.timestamp)}</span>
            </div>
            <strong>${view.title}</strong>
            <p>${view.meta}</p>
            <p>Actor: ${view.actor}</p>
        `;
        const head = node.querySelector('.audit-entry-head span');
        if (head) head.textContent = `Actor: ${view.actor}`;
        host.appendChild(node);
    });
}

function switchTab(tab) {
    if (tab === 'files' && accessPolicy && !accessPolicy.allow_explorer) {
        showToast('File explorer is disabled for your role.', 'warn');
        return;
    }
    if (tab === 'settings' && accessPolicy && !accessPolicy.allow_settings) {
        showToast('Settings access is disabled for your role.', 'warn');
        return;
    }
    activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.toggle('active', pane.id === `tab-${tab}`));

    if (logsInterval) {
        clearInterval(logsInterval);
        logsInterval = null;
    }

    if (tab === 'terminal') {
        Promise.all([loadContainerMeta(), loadLogs()]);
        if (logsAutoEnabled) {
            logsInterval = setInterval(() => {
                loadContainerMeta();
                loadLogs();
            }, 4000);
        }
    } else if (tab === 'files') {
        loadFiles();
    } else if (tab === 'settings') {
        loadSettings();
        loadRestartPolicy();
        loadAuditLog();
    }
}

function refreshActiveTab() {
    if (activeTab === 'terminal') {
        Promise.all([loadContainerMeta(), loadLogs(true)]);
        return;
    }
    if (activeTab === 'files') {
        loadFiles();
        return;
    }
    if (activeTab === 'settings') {
        Promise.all([loadSettings(), loadRestartPolicy(), loadAuditLog()]);
        return;
    }
    loadContainerMeta();
}

function safeJoin(base, name) {
    if (!base || base === '/') return `/${name}`;
    return `${base.replace(/\/$/, '')}/${name}`;
}

function setStatusPill(status) {
    const el = document.getElementById('ws-status-pill');
    const normalized = String(status || 'unknown').toLowerCase();
    el.textContent = normalized;
    el.className = `status-pill ${normalized}`;
}

function formatNow() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function appendConsoleBlock(title, body, isError = false) {
    const prefix = isError ? 'ERROR' : 'OK';
    const block = `[${formatNow()}] ${prefix} - ${title}\n${(body || '(no output)').trim()}\n`;
    consoleEvents.push(block);
    if (consoleEvents.length > 25) consoleEvents = consoleEvents.slice(-25);
    renderConsoleOutput();
}

function stripAnsi(text) {
    return String(text || '').replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
}

function filterLogNoise(text) {
    const raw = stripAnsi(text || '');
    if (!raw) return raw;
    const noise = /Thread RCON Client .* (started|shutting down)$/;
    return raw
        .split('\n')
        .filter(line => !noise.test(line))
        .join('\n');
}

function renderConsoleOutput() {
    const out = document.getElementById('live-console-output');
    const eventsText = consoleEvents.length ? `${consoleEvents.join('\n')}\n\n` : '';
    const logsText = latestLogsText || '(no logs)';
    out.textContent = `${logsText}${eventsText}`;
    out.scrollTop = out.scrollHeight;
}

function parsePorts(raw) {
    return String(raw || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);
}

function parsePortRuleDescriptor(rule) {
    const raw = String(rule || '').trim();
    const withoutProto = raw.replace(/\/(tcp|udp)$/i, '');
    const protocolMatch = raw.match(/\/(tcp|udp)$/i);
    const protocol = protocolMatch ? protocolMatch[1].toUpperCase() : 'TCP';
    const parts = withoutProto.split(':').map(part => String(part || '').trim()).filter(Boolean);

    let bind = '0.0.0.0';
    let hostPort = '';
    let containerPort = '';

    if (parts.length >= 3) {
        bind = parts[0] || bind;
        hostPort = parts[1] || '';
        containerPort = parts[2] || '';
    } else if (parts.length === 2) {
        hostPort = parts[0] || '';
        containerPort = parts[1] || '';
    } else if (parts.length === 1) {
        hostPort = parts[0] || '';
        containerPort = parts[0] || '';
    }

    return {
        raw,
        protocol,
        bind,
        hostPort,
        containerPort,
        title: hostPort && containerPort ? `${hostPort} -> ${containerPort}` : raw || 'Unknown route',
        meta: `${protocol} traffic from ${bind}:${hostPort || '?'} reaches container port ${containerPort || '?'}.`
    };
}

function inferWorkspaceProtocol(profileName = '', imageName = '') {
    const profile = String(profileName || '').toLowerCase();
    const image = String(imageName || '').toLowerCase();
    if (profile.includes('flask') || image.includes('flask')) return 'python-flask';
    if (profile.includes('fastapi') || image.includes('fastapi') || image.includes('uvicorn')) return 'python-fastapi';
    if (profile.includes('django') || image.includes('django')) return 'python-django';
    if (profile.includes('vite') || image.includes('vite')) return 'node-vite';
    if (profile.includes('express') || image.includes('node')) return 'node-npm';
    if (profile.includes('nginx') || profile.includes('caddy') || image.includes('nginx') || image.includes('caddy')) return 'static-web';
    if (profile === 'python') return 'python-pip';
    return 'generic';
}

function allowedWorkspaceProtocols(profileName = 'generic') {
    const raw = String(profileName || 'generic').toLowerCase();
    const profile = raw.includes('python')
        ? 'python'
        : (raw.includes('nginx') || raw.includes('caddy') || raw.includes('web') || raw.includes('node'))
            ? 'web'
            : raw;
    return Object.entries(workspaceProtocolDefinitions)
        .filter(([, item]) => Array.isArray(item.profiles) && item.profiles.includes(profile))
        .map(([key]) => key);
}

function populateWorkspaceProtocolOptions(profileName = 'generic', selectedValue = '') {
    const select = document.getElementById('set-project-protocol');
    if (!select) return;
    const options = allowedWorkspaceProtocols(profileName);
    const selected = selectedValue || inferWorkspaceProtocol(profileName, currentContainerImage);
    select.innerHTML = '';
    options.forEach((key) => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = workspaceProtocolDefinitions[key]?.label || key;
        select.appendChild(option);
    });
    if (selected && !options.includes(selected)) {
        const fallback = document.createElement('option');
        fallback.value = selected;
        fallback.textContent = workspaceProtocolDefinitions[selected]?.label || selected;
        select.appendChild(fallback);
    }
    select.value = selected || 'generic';
}

function updateProtocolHint(protocolKey) {
    const protocol = workspaceProtocolDefinitions[protocolKey] || workspaceProtocolDefinitions.generic;
    const hint = document.getElementById('protocol-hint');
    const installHint = document.getElementById('install-command-hint');
    if (hint) hint.textContent = protocol.hint || 'Select a protocol to prefill install/start commands.';
    if (installHint) {
        installHint.textContent = protocol.install
            ? `Suggested install command: ${protocol.install}`
            : 'No dependency install command is suggested for this protocol.';
    }
}

function onProjectProtocolChange(forceApply = false) {
    const protocolKey = String(document.getElementById('set-project-protocol')?.value || 'generic');
    const protocol = workspaceProtocolDefinitions[protocolKey] || workspaceProtocolDefinitions.generic;
    const installEl = document.getElementById('set-install-command');
    const startEl = document.getElementById('set-command');
    updateProtocolHint(protocolKey);
    if (installEl && (forceApply || !String(installEl.value || '').trim())) {
        installEl.value = protocol.install || '';
    }
    if (startEl && (forceApply || !String(startEl.value || '').trim())) {
        startEl.value = protocol.startup || '';
    }
    updateLaunchPreview();
}

function getSelectedLaunchRoute() {
    const selectedRules = Array.from(document.querySelectorAll('#ports-selection input[type="checkbox"]:checked'))
        .map((node) => node.value);
    const fallbackRules = parsePorts(document.getElementById('set-ports')?.value || '');
    const pool = selectedRules.length ? selectedRules : fallbackRules;
    return pool.length ? parsePortRuleDescriptor(pool[0]) : null;
}

function buildLaunchPreviewUrl() {
    const manualUrl = String(document.getElementById('set-launch-url')?.value || '').trim();
    if (manualUrl) {
        return {
            value: manualUrl,
            note: 'Using the custom launch URL saved for this project.'
        };
    }
    const route = getSelectedLaunchRoute();
    const domain = String(document.getElementById('set-domain')?.value || '').trim();
    const host = domain || window.location.hostname || 'localhost';
    if (!route || !route.hostPort) {
        return {
            value: domain ? `${window.location.protocol}//${domain}` : (window.location.origin || 'http://localhost'),
            note: 'No published route is selected yet, so the preview falls back to the panel host/domain.'
        };
    }
    const portSegment = route.hostPort && !['80', '443'].includes(String(route.hostPort)) ? `:${route.hostPort}` : '';
    const scheme = String(route.hostPort) === '443' ? 'https:' : window.location.protocol;
    return {
        value: `${scheme}//${host}${portSegment}`,
        note: domain
            ? 'Preview combines your custom domain with the first active published route.'
            : 'Preview combines the current panel host with the first active published route.'
    };
}

function updateLaunchPreview() {
    const hostEl = document.getElementById('launch-current-host');
    const routeEl = document.getElementById('launch-route-preview');
    const urlEl = document.getElementById('launch-public-preview');
    const noteEl = document.getElementById('launch-public-note');
    const hintEl = document.getElementById('launch-url-hint');
    const route = getSelectedLaunchRoute();
    const preview = buildLaunchPreviewUrl();
    if (hostEl) hostEl.textContent = window.location.origin || 'unknown origin';
    if (routeEl) routeEl.textContent = route ? route.raw : 'No published route selected';
    if (urlEl) urlEl.textContent = preview.value;
    if (noteEl) noteEl.textContent = preview.note;
    if (hintEl) hintEl.textContent = preview.note;
}

function runStoredCommand(fieldId) {
    const cmd = String(document.getElementById(fieldId)?.value || '').trim();
    if (!cmd) {
        showToast('Command field is empty.', 'warn');
        return;
    }
    const modeSelect = document.getElementById('cmd-mode');
    const shellOpt = modeSelect?.querySelector('option[value="shell"]');
    if (modeSelect && shellOpt && !shellOpt.disabled) {
        modeSelect.value = 'shell';
        onConsoleModeChange();
    }
    switchTab('terminal');
    runCommandText(cmd, { detached: true });
}

function openLaunchPreview() {
    const preview = buildLaunchPreviewUrl();
    if (!preview.value) {
        showToast('Launch URL is not available yet.', 'warn');
        return;
    }
    window.open(preview.value, '_blank', 'noopener');
}

function updatePortsSelectionTelemetry() {
    const cards = Array.from(document.querySelectorAll('#ports-selection .port-toggle'));
    const selected = cards.filter((card) => {
        const node = card.querySelector('input[type="checkbox"]');
        return !!node?.checked;
    }).length;
    const visible = cards.filter((card) => card.style.display !== 'none').length;
    const total = cards.length;

    const selectedCount = document.getElementById('ports-selected-count');
    const visibleCount = document.getElementById('ports-visible-count');
    const totalCount = document.getElementById('ports-total-count');
    const filterMeta = document.getElementById('ports-filter-meta');

    if (selectedCount) selectedCount.textContent = String(selected);
    if (visibleCount) visibleCount.textContent = String(visible);
    if (totalCount) totalCount.textContent = String(total);
    if (filterMeta) {
        filterMeta.textContent = total
            ? `Showing ${visible} of ${total} routes`
            : 'Showing 0 routes';
    }
}

function configureQuickRow(profile, mode) {
    const quickRow = document.getElementById('quick-row');
    if (!quickRow) return;
    const appCommandsByProfile = {
        minecraft: ['help', 'list', 'save-all', 'stop']
    };
    const shellCommandsByProfile = {
        python: ['pwd', 'ls -la', 'python --version', 'pip --version'],
        web: ['pwd', 'ls -la', 'env | head -20', 'cat /etc/os-release | head -5'],
        generic: ['pwd', 'ls -la', 'env | head -20']
    };
    const commands = mode === 'shell'
        ? (shellCommandsByProfile[profile] || shellCommandsByProfile.generic)
        : (appCommandsByProfile[profile] || []);
    quickRow.innerHTML = '';
    if (!commands.length) {
        const hint = document.createElement('span');
        hint.className = 'head-note';
        hint.textContent = mode === 'shell'
            ? 'No shell shortcuts for this profile.'
            : 'No app-console shortcuts for this profile.';
        quickRow.appendChild(hint);
        return;
    }
    commands.forEach(cmd => {
        const btn = document.createElement('button');
        btn.className = 'chip-btn';
        btn.type = 'button';
        btn.textContent = cmd;
        btn.onclick = () => runCommandText(cmd);
        quickRow.appendChild(btn);
    });
}

function configureConsoleMode(mode, profileName = 'generic') {
    const prompt = document.querySelector('.prompt');
    const input = document.getElementById('cmd-input');
    const runBtn = document.getElementById('cmd-run-btn');
    const status = document.getElementById('cmd-status');
    const modeSelect = document.getElementById('cmd-mode');
    const canUseConsole = !!(profilePolicy && profilePolicy.console_allowed && (!accessPolicy || accessPolicy.allow_console !== false));
    const canUseShell = !!(profilePolicy && profilePolicy.shell_allowed && (!accessPolicy || accessPolicy.allow_shell !== false));

    if (modeSelect) modeSelect.value = mode;
    currentConsoleMode = mode;
    if (prompt) prompt.textContent = mode === 'shell' ? '$' : '>';
    if (runBtn) runBtn.textContent = mode === 'shell' ? 'Run' : 'Send';
    if (input) {
        input.placeholder = mode === 'shell'
            ? 'Type shell command in container workspace'
            : 'Type app console command (sent to process stdin)';
    }

    if (mode === 'shell' && !canUseShell) {
        if (input) input.disabled = true;
        if (runBtn) runBtn.disabled = true;
        if (status) status.textContent = 'Workspace shell is disabled for this profile.';
    } else if (mode === 'console' && !canUseConsole) {
        if (input) input.disabled = true;
        if (runBtn) runBtn.disabled = true;
        if (status) status.textContent = 'Application console stdin is unavailable for this profile.';
    } else {
        if (input) input.disabled = false;
        if (runBtn) runBtn.disabled = false;
        if (status) {
            status.textContent = mode === 'shell'
                ? 'Workspace shell mode enabled with safety restrictions.'
                : 'Application console mode enabled.';
        }
    }

    configureQuickRow(profileName, mode);
}

function onConsoleModeChange() {
    const modeSelect = document.getElementById('cmd-mode');
    const requested = modeSelect?.value === 'shell' ? 'shell' : 'console';
    const profileName = (profilePolicy && profilePolicy.profile) || 'generic';
    configureConsoleMode(requested, profileName);
}

function configureStartupCommandHint(profileName) {
    const input = document.getElementById('set-command');
    const hint = document.getElementById('startup-command-hint');
    if (!input || !hint) return;
    const byProfile = {
        minecraft: {
            placeholder: 'Keep empty for image default (recommended)',
            helper: 'For Minecraft presets, keep default entrypoint unless you know exact start flags.'
        },
        python: {
            placeholder: 'e.g. python app.py or flask run --host=0.0.0.0 --port 5000',
            helper: 'Defines the auto-start command used for Python apps and sites.'
        },
        web: {
            placeholder: 'e.g. nginx -g "daemon off;" or npm run start',
            helper: 'Defines the auto-start command used for web apps and frontend services.'
        },
        database: {
            placeholder: 'e.g. (usually keep image default)',
            helper: 'Databases usually should keep image startup defaults.'
        },
        generic: {
            placeholder: 'e.g. ./start.sh or python app.py',
            helper: 'Defines the saved auto-start command for this container.'
        }
    };
    const cfg = byProfile[profileName] || byProfile.generic;
    input.placeholder = cfg.placeholder;
    hint.textContent = cfg.helper;
}

function configureToolbox(profile) {
    const cards = Array.from(document.querySelectorAll('#tool-grid .tool-card'));
    cards.forEach(card => {
        const raw = String(card.dataset.profiles || '').trim();
        if (!raw) {
            card.classList.remove('is-hidden');
            return;
        }
        const profiles = raw.split(',').map(v => v.trim()).filter(Boolean);
        const visible = profiles.includes(profile);
        card.classList.toggle('is-hidden', !visible);
    });
}

function syncPortsInputFromSelection() {
    const selected = Array.from(document.querySelectorAll('#ports-selection input[type="checkbox"]:checked'))
        .map(node => node.value);
    document.getElementById('set-ports').value = selected.join(', ');
    document.querySelectorAll('#ports-selection .port-toggle').forEach(card => {
        const node = card.querySelector('input[type="checkbox"]');
        card.classList.toggle('is-selected', !!node?.checked);
        card.classList.toggle('is-disabled', !!node?.disabled);
    });
    const meta = document.getElementById('ports-selection-meta');
    const summary = document.getElementById('ports-summary-text');
    const total = document.querySelectorAll('#ports-selection .port-toggle').length;
    if (meta && total > 0) {
        meta.textContent = `Selected ${selected.length} of ${total} available rules.`;
    } else if (meta) {
        meta.textContent = 'No pre-allocated ports found for this container.';
    }
    if (summary) {
        summary.textContent = selected.length
            ? `${selected.length} published route(s) will remain active after save.`
            : 'No route selected. Manual mappings from the text field will be saved as entered.';
    }
    updatePortsSelectionTelemetry();
    updateLaunchPreview();
}

function filterPortsSelection() {
    const filter = String(document.getElementById('ports-search')?.value || '').trim().toLowerCase();
    document.querySelectorAll('#ports-selection .port-toggle').forEach(label => {
        const text = String(label.dataset.rule || '').toLowerCase();
        label.style.display = !filter || text.includes(filter) ? '' : 'none';
    });
    updatePortsSelectionTelemetry();
}

function setAllPortsSelection(enabled) {
    document.querySelectorAll('#ports-selection .port-toggle input[type="checkbox"]').forEach(node => {
        if (node.closest('.port-toggle')?.style.display === 'none') return;
        node.checked = !!enabled;
    });
    syncPortsInputFromSelection();
}

function renderPortsSelection(rules, selectedRules = []) {
    const host = document.getElementById('ports-selection');
    const meta = document.getElementById('ports-selection-meta');
    if (!host || !meta) return;

    host.innerHTML = '';
    const selectedSet = new Set(selectedRules);
    if (!rules.length) {
        meta.textContent = 'No pre-allocated ports found for this container.';
        const empty = document.createElement('div');
        empty.className = 'head-note';
        empty.textContent = 'Deploy/recreate container with port bindings to manage allocation here.';
        host.appendChild(empty);
        updatePortsSelectionTelemetry();
        return;
    }

    meta.textContent = `Available rules: ${rules.length}. Toggle what should remain active for this container.`;
    rules.forEach(rule => {
        const id = `port-rule-${rule.replace(/[^a-zA-Z0-9]/g, '_')}`;
        const label = document.createElement('label');
        label.className = 'port-toggle';
        label.dataset.rule = rule;
        label.setAttribute('for', id);
        label.setAttribute('tabindex', '0');
        label.setAttribute('role', 'button');
        label.setAttribute('aria-label', `Toggle rule ${rule}`);

        const check = document.createElement('input');
        check.type = 'checkbox';
        check.id = id;
        check.value = rule;
        check.checked = selectedSet.size ? selectedSet.has(rule) : true;
        check.addEventListener('change', syncPortsInputFromSelection);

        const icon = document.createElement('span');
        icon.className = 'port-rule-icon';
        icon.innerHTML = '<i class="bi bi-plug-fill"></i>';

        const checkMark = document.createElement('span');
        checkMark.className = 'port-rule-check';
        checkMark.innerHTML = '<i class="bi bi-check2"></i>';

        const descriptor = parsePortRuleDescriptor(rule);
        const text = document.createElement('span');
        text.className = 'port-rule-text';
        text.innerHTML = `
            <span class="port-rule-title">${descriptor.title}</span>
            <span class="port-rule-meta">${descriptor.meta}</span>
            <span class="port-rule-badges">
                <span class="port-rule-badge protocol-${descriptor.protocol.toLowerCase()}">${descriptor.protocol}</span>
                <span class="port-rule-badge">Bind ${descriptor.bind}</span>
                <span class="port-rule-badge">Host ${descriptor.hostPort || '?'}</span>
                <span class="port-rule-badge">Container ${descriptor.containerPort || '?'}</span>
            </span>
        `;

        label.addEventListener('click', (event) => {
            event.preventDefault();
            if (check.disabled) return;
            check.checked = !check.checked;
            syncPortsInputFromSelection();
        });
        label.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            if (check.disabled) return;
            check.checked = !check.checked;
            syncPortsInputFromSelection();
        });

        label.appendChild(check);
        label.appendChild(checkMark);
        label.appendChild(icon);
        label.appendChild(text);
        host.appendChild(label);
    });
    syncPortsInputFromSelection();
    filterPortsSelection();
}

function setButtonBusy(id, busy, idleText = 'Run', busyText = 'Running...') {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = !!busy;
    btn.textContent = busy ? busyText : idleText;
}

async function apiJson(url, options = {}) {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const msg = data.detail || `Request failed (${res.status})`;
        throw new Error(msg);
    }
    return data;
}

async function loadContainerMeta() {
    try {
        const data = await apiJson(`/api/containers/detail/${containerId}`);
        document.getElementById('ws-name').textContent = data.name || containerId;
        document.getElementById('ws-id').textContent = (data.id || containerId).slice(0, 12);
        document.getElementById('ws-image').textContent = data.image || 'unknown';
        currentContainerImage = String(data.image || '').toLowerCase();
        setStatusPill(data.status);
        setStatus(`Working with ${data.name || containerId} (${data.status || 'unknown'}).`);
    } catch (e) {
        setStatus('Could not load container metadata.');
        showToast(e.message, 'error');
    }
}

async function loadProfilePolicy() {
    try {
        profilePolicy = await apiJson(`/api/containers/profile/${containerId}`);
        accessPolicy = profilePolicy.permissions || null;
        const profileLabel = profilePolicy.label || profilePolicy.profile || 'container';
        const profileName = profilePolicy.profile || 'generic';
        const consoleAllowed = !!profilePolicy.console_allowed;
        const shellAllowed = !!profilePolicy.shell_allowed;
        const modeSelect = document.getElementById('cmd-mode');
        if (modeSelect) {
            const appOpt = modeSelect.querySelector('option[value="console"]');
            const shellOpt = modeSelect.querySelector('option[value="shell"]');
            if (appOpt) appOpt.disabled = !consoleAllowed;
            if (shellOpt) shellOpt.disabled = !shellAllowed;
        }
        configureToolbox(profileName);
        configureStartupCommandHint(profileName);
        populateWorkspaceProtocolOptions(profileName, inferWorkspaceProtocol(profileName, currentContainerImage));
        updateProtocolHint(inferWorkspaceProtocol(profileName, currentContainerImage));
        applyWorkspacePermissions();
        renderCapabilitySummary(accessPolicy || {});

        if (consoleAllowed) {
            configureConsoleMode('console', profileName);
            setStatus(`Profile: ${profileLabel}. App console available.`);
            return;
        }
        if (shellAllowed) {
            configureConsoleMode('shell', profileName);
            setStatus(`Profile: ${profileLabel}. Workspace shell available.`);
            return;
        }
        configureConsoleMode('console', profileName);
        setStatus(`Profile: ${profileLabel}. Interactive console is restricted; use logs and files.`);
    } catch (e) {
        profilePolicy = null;
        accessPolicy = null;
    }
}

function applyWorkspacePermissions() {
    const policy = accessPolicy || {};
    const filesTab = document.getElementById('tab-btn-files');
    const settingsTab = document.getElementById('tab-btn-settings');
    if (filesTab) filesTab.style.display = policy.allow_explorer === false ? 'none' : '';
    if (settingsTab) settingsTab.style.display = policy.allow_settings === false ? 'none' : '';
    const runBtn = document.getElementById('cmd-run-btn');
    const modeSelect = document.getElementById('cmd-mode');
    if (modeSelect) {
        const appOpt = modeSelect.querySelector('option[value="console"]');
        const shellOpt = modeSelect.querySelector('option[value="shell"]');
        if (appOpt && policy.allow_console === false) appOpt.disabled = true;
        if (shellOpt && policy.allow_shell === false) shellOpt.disabled = true;
    }
    if (runBtn && policy.allow_console === false && policy.allow_shell === false) {
        runBtn.disabled = true;
    }
    renderCapabilitySummary(policy);
    updateFilePreviewActions();
}

async function loadAuditLog(showToastOnManual = false) {
    const host = document.getElementById('workspace-audit-list');
    if (host && !showToastOnManual) {
        host.innerHTML = '<div class="audit-empty">Loading recent activity...</div>';
    }
    try {
        const data = await apiJson(`/api/containers/audit/${containerId}?limit=25`);
        latestAuditEntries = Array.isArray(data.entries) ? data.entries : [];
        renderAuditLog();
        if (showToastOnManual) showToast('Activity refreshed.', 'ok', 1400);
    } catch (e) {
        if (host) host.innerHTML = `<div class="audit-empty">${e.message}</div>`;
        if (showToastOnManual) showToast(e.message, 'error');
    }
}

async function containerAction(action) {
    const labels = {
        start: 'starting',
        stop: 'stopping',
        restart: 'restarting'
    };
    try {
        setStatus(`Container is ${labels[action] || action}...`);
        await apiJson(`/api/containers/${action}/${containerId}`, { method: 'POST' });
        showToast(`Container ${action} command sent.`, 'ok');
        await loadContainerMeta();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function runCommand() {
    const input = document.getElementById('cmd-input');
    const detached = String(input?.dataset?.detached || '') === '1';
    const mode = currentConsoleMode === 'shell' ? 'shell' : 'console';
    if (accessPolicy) {
        if (mode === 'console' && accessPolicy.allow_console === false) {
            showToast('Console access is disabled for your role.', 'warn');
            return;
        }
        if (mode === 'shell' && accessPolicy.allow_shell === false) {
            showToast('Shell access is disabled for your role.', 'warn');
            return;
        }
    }
    const cmd = (input.value || '').trim();
    if (!cmd) {
        showToast('Enter a command first.', 'warn');
        return;
    }

    const idleLabel = mode === 'shell' ? 'Run' : 'Send';
    const busyLabel = mode === 'shell' ? 'Running...' : 'Sending...';
    setButtonBusy('cmd-run-btn', true, idleLabel, busyLabel);
    document.getElementById('cmd-status').textContent = `Executing: ${cmd}`;

    try {
        if (mode === 'shell') {
            const data = await apiJson(`/api/containers/exec/${containerId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd, detached })
            });
            const exitCode = Number(data.exit_code ?? 0);
            if (detached) {
                appendConsoleBlock(
                    `shell$ ${cmd} [detached pid ${data.pid || '?'}]`,
                    `${String(data.output || '').trim() || 'Background process launched.'}\nStartup log: ${data.log_path || '/tmp/nebula-startup.log'}`,
                    false
                );
                document.getElementById('cmd-status').textContent = `Background command launched at ${formatNow()}`;
            } else {
                appendConsoleBlock(`shell$ ${cmd} (exit ${exitCode})`, data.output || '(no output)', exitCode !== 0);
                document.getElementById('cmd-status').textContent = `Shell command completed at ${formatNow()}`;
            }
        } else {
            const data = await apiJson(`/api/containers/console-send/${containerId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd })
            });
            const transport = String(data.transport || 'stdin');
            const commandOutput = stripAnsi(String((data.output || '').trim()));
            const resultText = commandOutput || (data.status || 'sent');
            appendConsoleBlock(`console> ${cmd} [${transport}]`, resultText, false);
            document.getElementById('cmd-status').textContent = `Console command sent via ${transport} at ${formatNow()}`;
        }
        await loadLogs();
        input.value = '';
        if (input?.dataset) input.dataset.detached = '';
    } catch (e) {
        appendConsoleBlock(cmd, e.message, true);
        document.getElementById('cmd-status').textContent = 'Command failed.';
        showToast(e.message, 'error');
    } finally {
        if (input?.dataset) input.dataset.detached = '';
        await loadContainerMeta();
        setButtonBusy('cmd-run-btn', false, idleLabel, busyLabel);
    }
}

function runCommandText(text, options = {}) {
    if (document.getElementById('cmd-input')?.disabled) {
        showToast('Interactive console is unavailable for this profile.', 'warn');
        return;
    }
    const input = document.getElementById('cmd-input');
    input.value = text;
    if (input.dataset) input.dataset.detached = options && options.detached ? '1' : '';
    switchTab('terminal');
    runCommand();
}

async function loadLogs(showToastOnManual = false) {
    const syncState = document.getElementById('logs-sync-state');
    const rawTail = parseInt(document.getElementById('logs-tail').value || '300', 10);
    const tail = Math.max(50, Math.min(2000, Number.isFinite(rawTail) ? rawTail : 300));

    try {
        const data = await apiJson(`/api/containers/logs/${containerId}?tail=${tail}`);
        latestLogsText = filterLogNoise(data.logs || '');
        renderConsoleOutput();
        syncState.textContent = `synced ${formatNow()}`;
        if (showToastOnManual) showToast('Logs refreshed.', 'ok', 1600);
    } catch (e) {
        syncState.textContent = 'sync failed';
        if (showToastOnManual) showToast(e.message, 'error');
    }
}

function toggleAutoLogs() {
    const btn = document.getElementById('logs-auto-btn');
    if (logsInterval) {
        clearInterval(logsInterval);
        logsInterval = null;
        logsAutoEnabled = false;
        btn.classList.remove('active');
        btn.textContent = 'Auto: OFF';
        showToast('Auto log refresh paused.', 'warn');
        return;
    }
    logsAutoEnabled = true;
    loadContainerMeta();
    loadLogs();
    logsInterval = setInterval(() => {
        loadContainerMeta();
        loadLogs();
    }, 4000);
    btn.classList.add('active');
    btn.textContent = 'Auto: ON';
    showToast('Auto log refresh enabled.', 'ok');
}

function clearOutput(targetId, statusText = 'Cleared.') {
    const el = document.getElementById(targetId);
    if (!el) return;
    if (targetId === 'live-console-output') {
        consoleEvents = [];
        renderConsoleOutput();
    } else {
        el.textContent = '';
    }
    showToast(statusText, 'ok', 1400);
}

function copyText(targetId) {
    const el = document.getElementById(targetId);
    if (!el) return;
    const text = el.textContent || '';
    if (!text.trim()) {
        showToast('Nothing to copy.', 'warn');
        return;
    }
    navigator.clipboard.writeText(text)
        .then(() => showToast('Copied to clipboard.', 'ok', 1400))
        .catch(() => showToast('Clipboard is blocked in this browser.', 'warn'));
}

function downloadLogs() {
    const text = document.getElementById('live-console-output').textContent || '';
    if (!text.trim()) {
        showToast('No logs to download.', 'warn');
        return;
    }
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `container-${containerId}-logs.txt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    showToast('Log file downloaded.', 'ok', 1400);
}

async function initWorkspaceRoots() {
    const rootsEl = document.getElementById('workspace-roots');
    rootsEl.innerHTML = '';

    try {
        const data = await apiJson(`/api/containers/workspace-roots/${containerId}`);
        const roots = Array.isArray(data.roots) ? data.roots : [];
        activeWorkspaceRoot = data.preferred_path || '/data';
        const activeRoot = document.getElementById('explorer-active-root');
        if (activeRoot) activeRoot.textContent = activeWorkspaceRoot;
        if (activeWorkspaceRoot && activeWorkspaceRoot !== '/') {
            document.getElementById('files-path').value = activeWorkspaceRoot;
        }

        const visibleRoots = roots.filter(root => root !== '/');
        if (visibleRoots.length === 0) {
            rootsEl.innerHTML = '<span class="head-note">No workspace roots detected.</span>';
            return;
        }

        visibleRoots.forEach(root => {
            const btn = document.createElement('button');
            btn.className = 'root-chip';
            btn.type = 'button';
            btn.textContent = root;
            btn.onclick = () => {
                document.getElementById('files-path').value = root;
                loadFiles();
            };
            rootsEl.appendChild(btn);
        });

        setStatus(`Explorer starts at ${activeWorkspaceRoot}. Use Root button only when needed.`);
    } catch (e) {
        rootsEl.innerHTML = '<span class="head-note">Workspace roots unavailable.</span>';
    }
}

async function loadSftpInfo() {
    const targetEl = document.getElementById('sftp-target');
    const noteEl = document.getElementById('sftp-note');
    if (targetEl) targetEl.textContent = 'Loading SFTP profile...';
    if (noteEl) noteEl.textContent = 'Checking host workspace connectivity.';
    try {
        const data = await apiJson(`/api/containers/sftp-info/${containerId}`);
        latestSftpInfo = data;
        if (targetEl) {
            targetEl.textContent = data.available
                ? `${data.username}@${data.host}:${data.port}`
                : 'Workspace path unavailable';
        }
        if (noteEl) {
            const pathHint = data.workspace_path ? ` Workspace path: ${data.workspace_path}` : '';
            const permsHint = data.owner || data.group || data.mode
                ? ` Ownership: ${data.owner || '?'}:${data.group || '?'} ${data.mode || ''}.`
                : '';
            const writableHint = data.writable === false
                ? ` Panel service cannot write here${data.panel_group ? `; expected shared group: ${data.panel_group}` : ''}.`
                : '';
            noteEl.textContent = `${data.note || 'SFTP profile loaded.'}${pathHint}${permsHint}${writableHint}`;
        }
    } catch (e) {
        latestSftpInfo = null;
        if (targetEl) targetEl.textContent = 'SFTP profile unavailable';
        if (noteEl) noteEl.textContent = e.message || 'Could not load SFTP access details.';
    }
}

function copySftpCommand() {
    const command = String(latestSftpInfo?.command || '').trim();
    if (!command) {
        showToast('SFTP command is not available yet.', 'warn');
        return;
    }
    navigator.clipboard.writeText(command)
        .then(() => showToast('SFTP command copied.', 'ok', 1400))
        .catch(() => showToast('Clipboard is blocked in this browser.', 'warn'));
}

function copySftpPath() {
    const path = String(latestSftpInfo?.workspace_path || '').trim();
    if (!path) {
        showToast('Workspace path is not available yet.', 'warn');
        return;
    }
    navigator.clipboard.writeText(path)
        .then(() => showToast('Workspace path copied.', 'ok', 1400))
        .catch(() => showToast('Clipboard is blocked in this browser.', 'warn'));
}

function setUploadStatus(text, level = 'idle') {
    const el = document.getElementById('upload-status');
    if (!el) return;
    el.textContent = text;
    el.className = `workspace-upload-status ${level}`;
}

async function uploadWorkspaceEntries(entries, targetPath) {
    const files = Array.isArray(entries) ? entries : [];
    if (!files.length) {
        setUploadStatus('No files selected.', 'idle');
        return;
    }
    let totalBytes = 0;
    files.forEach((item) => {
        totalBytes += Number(item?.file?.size || 0);
    });
    if (totalBytes > WORKSPACE_UPLOAD_LIMIT_BYTES) {
        setUploadStatus('Upload rejected: total request size exceeds 1 GB.', 'error');
        showToast('Upload exceeds 1 GB limit.', 'error');
        return;
    }

    const form = new FormData();
    form.append('target_path', targetPath);
    files.forEach((item) => {
        const file = item.file;
        const relativePath = String(item.relativePath || file?.name || '').trim();
        if (!file || !relativePath) return;
        form.append('files', file, file.name);
        form.append('relative_paths', relativePath);
    });

    setUploadStatus(`Uploading ${files.length} item(s) to ${targetPath}...`, 'working');
    try {
        const res = await fetch(`/api/containers/upload-files/${containerId}`, {
            method: 'POST',
            body: form,
            credentials: 'same-origin'
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data.detail || `Upload failed (${res.status})`);
        }
        setUploadStatus(`Uploaded ${data.files_saved || files.length} item(s), ${Math.round((Number(data.bytes_written || totalBytes) / 1048576) * 10) / 10} MB written.`, 'ok');
        showToast('Upload completed.', 'ok');
        await loadFiles();
    } catch (e) {
        setUploadStatus(`Upload failed: ${e.message}`, 'error');
        showToast(e.message, 'error');
    }
}

function readDroppedDirectoryEntries(reader) {
    return new Promise((resolve, reject) => {
        const all = [];
        const pump = () => {
            reader.readEntries((batch) => {
                if (!batch.length) {
                    resolve(all);
                    return;
                }
                all.push(...batch);
                pump();
            }, reject);
        };
        pump();
    });
}

function fileFromEntry(entry) {
    return new Promise((resolve, reject) => {
        entry.file(resolve, reject);
    });
}

async function collectDroppedEntries(entry, prefix = '') {
    const nextPath = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isFile) {
        const file = await fileFromEntry(entry);
        return [{ file, relativePath: nextPath }];
    }
    if (entry.isDirectory) {
        const reader = entry.createReader();
        const children = await readDroppedDirectoryEntries(reader);
        const nested = await Promise.all(children.map((child) => collectDroppedEntries(child, nextPath)));
        return nested.flat();
    }
    return [];
}

async function collectDragDropFiles(dataTransfer) {
    const items = Array.from(dataTransfer?.items || []);
    if (!items.length) {
        return Array.from(dataTransfer?.files || []).map((file) => ({ file, relativePath: file.name }));
    }
    const collected = [];
    for (const item of items) {
        const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
        if (entry) {
            const nested = await collectDroppedEntries(entry, '');
            collected.push(...nested);
            continue;
        }
        const file = item.getAsFile ? item.getAsFile() : null;
        if (file) collected.push({ file, relativePath: file.name });
    }
    return collected;
}

function onExplorerDragEnter(event) {
    event.preventDefault();
    explorerDragDepth += 1;
    document.getElementById('files-list')?.classList.add('is-drop-target');
}

function onExplorerDragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    document.getElementById('files-list')?.classList.add('is-drop-target');
}

function onExplorerDragLeave(event) {
    event.preventDefault();
    explorerDragDepth = Math.max(0, explorerDragDepth - 1);
    if (explorerDragDepth === 0) {
        document.getElementById('files-list')?.classList.remove('is-drop-target');
    }
}

async function onExplorerDrop(event) {
    event.preventDefault();
    explorerDragDepth = 0;
    document.getElementById('files-list')?.classList.remove('is-drop-target');
    if (!canManageFileContent()) {
        showToast('Upload requires file write permission.', 'warn');
        return;
    }
    const entries = await collectDragDropFiles(event.dataTransfer);
    await uploadWorkspaceEntries(entries, currentExplorerPath());
}

function triggerUploadFiles(isFolder) {
    if (!canManageFileContent()) {
        showToast('Upload requires file write permission.', 'warn');
        return;
    }
    const input = document.getElementById(isFolder ? 'workspace-upload-folder' : 'workspace-upload-files');
    if (!input) return;
    input.value = '';
    input.click();
}

async function handleFileUpload(event, isFolder = false) {
    const input = event?.target;
    const files = Array.from(input?.files || []);
    if (!files.length) {
        setUploadStatus('No files selected.', 'idle');
        return;
    }
    const currentPath = currentExplorerPath();
    const entries = files.map((file) => ({
        file,
        relativePath: isFolder && file.webkitRelativePath ? file.webkitRelativePath : file.name,
    }));
    try {
        await uploadWorkspaceEntries(entries, currentPath);
    } finally {
        if (input) input.value = '';
    }
}

async function loadFiles() {
    if (accessPolicy && accessPolicy.allow_explorer === false) {
        const list = document.getElementById('files-list');
        list.innerHTML = '<div class="file-row error"><div class="file-info"><i class="bi bi-shield-lock"></i><span>Explorer access disabled for your role.</span></div></div>';
        return;
    }
    const pathEl = document.getElementById('files-path');
    const list = document.getElementById('files-list');
    const path = (pathEl.value || activeWorkspaceRoot || '/data').trim() || '/data';
    currentExplorerEntries = [];
    setSelectedExplorerPaths([]);
    hideExplorerContextMenu();
    renderExplorerBreadcrumbs(path);
    updateExplorerSelectionModeUi();
    const renderFileState = (icon, text, isError = false) => {
        list.innerHTML = '';
        const row = document.createElement('div');
        row.className = isError ? 'file-row error' : 'file-row';
        const info = document.createElement('div');
        info.className = 'file-info';
        const iconEl = document.createElement('i');
        iconEl.className = `bi ${icon}`;
        const textEl = document.createElement('span');
        textEl.textContent = text;
        info.appendChild(iconEl);
        info.appendChild(textEl);
        row.appendChild(info);
        list.appendChild(row);
    };

    renderFileState('bi-arrow-repeat', 'Loading...');

    try {
        const data = await apiJson(`/api/containers/files/${containerId}?path=${encodeURIComponent(path)}`);
        pathEl.value = data.path || path;
        renderExplorerBreadcrumbs(data.path || path);
        currentExplorerEntries = Array.isArray(data.entries)
            ? data.entries.map((entry) => ({ ...entry, path: safeJoin(data.path || path, entry.name || '') }))
            : [];
        const backendMode = document.getElementById('explorer-backend-mode');
        const backendNote = document.getElementById('explorer-backend-note');
        if (backendMode) {
            backendMode.textContent = data.source === 'host-workspace'
                ? 'Host Workspace Bridge'
                : 'Live Container Filesystem';
        }
        if (backendNote) {
            backendNote.textContent = data.source === 'host-workspace'
                ? 'File Explorer is using the mounted workspace on the host, so it still works while the container is restarting.'
                : 'File Explorer is reading directly from the running container filesystem.';
        }

        if (!currentExplorerEntries.length) {
            list.innerHTML = `
                <div class="dropzone-empty">
                    <i class="bi bi-inbox"></i>
                    <strong>This directory is empty</strong>
                    <span>Upload files, create a folder, or drag items here.</span>
                </div>
            `;
            return;
        }

        list.innerHTML = '';
        currentExplorerEntries.forEach(entry => {
            const row = document.createElement('div');
            row.className = 'file-row';
            row.dataset.path = entry.path;
            const icon = entry.type === 'dir' ? 'bi-folder2-open' : (entry.type === 'link' ? 'bi-link-45deg' : 'bi-file-earmark-text');
            const info = document.createElement('div');
            info.className = 'file-info';
            const selectBox = document.createElement('input');
            selectBox.type = 'checkbox';
            selectBox.className = 'file-select';
            selectBox.checked = selectedExplorerPaths.has(entry.path);
            selectBox.onclick = (event) => {
                event.stopPropagation();
                toggleExplorerPathSelection(entry.path, { additive: true });
            };
            const iconEl = document.createElement('i');
            iconEl.className = `bi ${icon}`;
            const metaWrap = document.createElement('div');
            metaWrap.className = 'file-meta';
            const nameEl = document.createElement('span');
            nameEl.textContent = entry.name || '';
            const subEl = document.createElement('small');
            subEl.textContent = entry.type === 'dir'
                ? `${entry.modified || 'folder'}`
                : `${entry.size || '0'} bytes • ${entry.modified || 'unknown time'}`;
            info.appendChild(selectBox);
            info.appendChild(iconEl);
            metaWrap.appendChild(nameEl);
            metaWrap.appendChild(subEl);
            info.appendChild(metaWrap);
            const actionsEl = document.createElement('div');
            actionsEl.className = 'file-actions';
            if (entry.type !== 'dir') {
                const openBtn = document.createElement('button');
                openBtn.className = 'file-action-btn';
                openBtn.type = 'button';
                openBtn.innerHTML = '<i class="bi bi-eye"></i>';
                openBtn.title = 'Preview';
                openBtn.onclick = (event) => {
                    event.stopPropagation();
                    previewFile(entry.path);
                };
                actionsEl.appendChild(openBtn);
            }
            if (entry.type !== 'dir') {
                const dlBtn = document.createElement('button');
                dlBtn.className = 'file-action-btn';
                dlBtn.type = 'button';
                dlBtn.innerHTML = '<i class="bi bi-download"></i>';
                dlBtn.title = 'Download';
                dlBtn.onclick = (event) => {
                    event.stopPropagation();
                    downloadFile(entry.path);
                };
                actionsEl.appendChild(dlBtn);
            }
            if (canManageFileContent()) {
                const renameBtn = document.createElement('button');
                renameBtn.className = 'file-action-btn';
                renameBtn.type = 'button';
                renameBtn.innerHTML = '<i class="bi bi-pencil-square"></i>';
                renameBtn.title = 'Rename';
                renameBtn.onclick = (event) => {
                    event.stopPropagation();
                    setSelectedExplorerPath(entry.path);
                    renameSelectedPath();
                };
                actionsEl.appendChild(renameBtn);

                const deleteBtn = document.createElement('button');
                deleteBtn.className = 'file-action-btn danger';
                deleteBtn.type = 'button';
                deleteBtn.innerHTML = '<i class="bi bi-trash3"></i>';
                deleteBtn.title = 'Delete';
                deleteBtn.onclick = (event) => {
                    event.stopPropagation();
                    setSelectedExplorerPath(entry.path);
                    deleteSelectedPath();
                };
                actionsEl.appendChild(deleteBtn);
            }
            row.appendChild(info);
            row.appendChild(actionsEl);
            row.onclick = (event) => {
                if (explorerSelectionMode) {
                    toggleExplorerPathSelection(entry.path, {
                        additive: event.ctrlKey || event.metaKey || true,
                        range: event.shiftKey,
                    });
                    return;
                }
                setSelectedExplorerPath(entry.path);
            };
            row.oncontextmenu = (event) => {
                event.preventDefault();
                showExplorerContextMenu(event, entry.path);
            };
            row.ondblclick = () => {
                if (entry.type === 'dir') {
                    pathEl.value = entry.path;
                    loadFiles();
                } else {
                    previewFile(entry.path);
                }
            };
            list.appendChild(row);
        });

        setStatus(`Browsing ${pathEl.value}`);
        syncExplorerSelectionClasses();
        updateExplorerSelectionStatus();
    } catch (e) {
        renderFileState('bi-exclamation-triangle', e.message || 'Failed to load directory.', true);
    }
}

async function previewFile(path) {
    const modal = document.getElementById('file-preview-modal');
    const pre = document.getElementById('file-preview');
    const title = document.getElementById('preview-path-title');
    activePreviewPath = (path || '').trim();
    previewEditMode = false;
    previewDirty = false;
    title.textContent = activePreviewPath || 'File Preview';
    modal.style.display = 'flex';
    pre.contentEditable = 'false';
    pre.oninput = null;
    pre.textContent = `Reading ${activePreviewPath}...`;
    updateFilePreviewActions();

    try {
        const data = await apiJson(`/api/containers/file-content/${containerId}?path=${encodeURIComponent(activePreviewPath)}&max_bytes=200000`);
        const trunc = data.truncated ? '\n\n--- file truncated ---' : '';
        pre.textContent = `${data.content || '(empty file)'}${trunc}`;
        setStatus(`Previewing ${activePreviewPath}`);
    } catch (e) {
        pre.textContent = `Cannot read file: ${e.message}`;
    }
}

function closeFilePreviewModal() {
    const modal = document.getElementById('file-preview-modal');
    const pre = document.getElementById('file-preview');
    activePreviewPath = '';
    previewEditMode = false;
    previewDirty = false;
    pre.contentEditable = 'false';
    pre.oninput = null;
    modal.style.display = 'none';
    updateFilePreviewActions();
}

function copyPreviewText() {
    copyText('file-preview');
}

function downloadFile(path = activePreviewPath) {
    const target = (path || activePreviewPath || '').trim();
    if (!target) {
        showToast('No file selected for download.', 'warn');
        return;
    }
    if (!canDownloadFileContent()) {
        showToast('Download is not allowed for this workspace role.', 'warn');
        return;
    }
    const link = document.createElement('a');
    link.href = `/api/containers/download-file/${containerId}?path=${encodeURIComponent(target)}`;
    link.download = target.split('/').pop() || 'file';
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast('Download started.', 'ok', 1400);
}

function downloadSelectionFromContextMenu() {
    hideExplorerContextMenu();
    const selected = getSelectedExplorerPaths();
    if (selected.length !== 1) {
        showToast('Select one file to download.', 'warn');
        return;
    }
    downloadFile(selected[0]);
}

function openSelectionFromContextMenu() {
    hideExplorerContextMenu();
    const target = explorerContextPath || getPrimarySelectedExplorerPath();
    const entry = getExplorerEntry(target);
    if (!entry) return;
    if (entry.type === 'dir') {
        const pathEl = document.getElementById('files-path');
        if (pathEl) pathEl.value = entry.path;
        loadFiles();
        return;
    }
    previewFile(entry.path);
}

function previewSelectionFromContextMenu() {
    hideExplorerContextMenu();
    const selected = getSelectedExplorerPaths();
    if (selected.length !== 1) {
        showToast('Preview works with one file at a time.', 'warn');
        return;
    }
    const entry = getExplorerEntry(selected[0]);
    if (!entry || entry.type === 'dir') {
        showToast('Select a file to preview.', 'warn');
        return;
    }
    previewFile(entry.path);
}

async function createFolder() {
    if (!canManageFileContent()) {
        showToast('Creating folders requires file write permission.', 'warn');
        return;
    }
    const name = window.prompt('Folder name');
    if (!name) return;
    const cleanName = String(name).trim().replaceAll('\\', '/').replace(/^\/+/, '');
    if (!cleanName || cleanName.includes('..')) {
        showToast('Folder name is invalid.', 'warn');
        return;
    }
    const target = safeJoin(currentExplorerPath(), cleanName);
    try {
        await apiJson(`/api/containers/mkdir/${containerId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: target })
        });
        showToast('Folder created.', 'ok');
        await loadFiles();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function renameSelectedPath() {
    if (!canManageFileContent()) {
        showToast('Rename requires file write permission.', 'warn');
        return;
    }
    const target = getPrimarySelectedExplorerPath();
    const selected = getSelectedExplorerPaths();
    hideExplorerContextMenu();
    if (!target) {
        showToast('Select a file or folder first.', 'warn');
        return;
    }
    if (selected.length > 1) {
        showToast('Rename works with one selected item.', 'warn');
        return;
    }
    const currentName = target.split('/').pop() || '';
    const nextName = window.prompt('New name', currentName);
    if (!nextName) return;
    const cleanName = String(nextName).trim().replaceAll('\\', '/').replace(/^\/+/, '');
    if (!cleanName || cleanName.includes('..') || cleanName.includes('/')) {
        showToast('New name is invalid.', 'warn');
        return;
    }
    const parent = target.split('/').slice(0, -1).join('/') || '/';
    const destination = safeJoin(parent, cleanName);
    try {
        await apiJson(`/api/containers/move-path/${containerId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_path: target, destination_path: destination })
        });
        showToast('Item renamed.', 'ok');
        setSelectedExplorerPath(destination);
        await loadFiles();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function moveSelectedPath() {
    if (!canManageFileContent()) {
        showToast('Move requires file write permission.', 'warn');
        return;
    }
    const selected = getSelectedExplorerPaths();
    hideExplorerContextMenu();
    if (!selected.length) {
        showToast('Select a file or folder first.', 'warn');
        return;
    }
    const hint = `${currentExplorerPath()}/`;
    const raw = window.prompt(selected.length > 1 ? 'Move selected items to directory' : 'Move to path', hint);
    if (!raw) return;
    let destination = String(raw).trim().replaceAll('\\', '/');
    if (!destination.startsWith('/')) {
        destination = safeJoin(currentExplorerPath(), destination);
    }
    try {
        for (const sourcePath of selected) {
            const targetPath = selected.length > 1
                ? safeJoin(destination, sourcePath.split('/').pop() || 'item')
                : destination;
            await apiJson(`/api/containers/move-path/${containerId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_path: sourcePath, destination_path: targetPath })
            });
        }
        showToast(selected.length > 1 ? `${selected.length} items moved.` : 'Item moved.', 'ok');
        setSelectedExplorerPath('');
        await loadFiles();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteSelectedPath() {
    if (!canManageFileContent()) {
        showToast('Delete requires file write permission.', 'warn');
        return;
    }
    const selected = getSelectedExplorerPaths();
    hideExplorerContextMenu();
    if (!selected.length) {
        showToast('Select a file or folder first.', 'warn');
        return;
    }
    const label = selected.length === 1
        ? `"${selected[0].split('/').pop() || selected[0]}"`
        : `${selected.length} selected items`;
    const ok = window.confirm(`Delete ${label}? This cannot be undone.`);
    if (!ok) return;
    try {
        for (const path of selected) {
            await apiJson(`/api/containers/delete-path/${containerId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path })
            });
        }
        showToast(selected.length > 1 ? `${selected.length} items deleted.` : 'Item deleted.', 'ok');
        setSelectedExplorerPath('');
        await loadFiles();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function copySelectedPaths() {
    const selected = getSelectedExplorerPaths();
    hideExplorerContextMenu();
    if (!selected.length) {
        showToast('Select at least one file or folder first.', 'warn');
        return;
    }
    explorerClipboard = { mode: 'copy', paths: selected };
    syncExplorerSelectionClasses();
    updateExplorerSelectionStatus();
    showToast(`${selected.length} item(s) copied to clipboard.`, 'ok');
}

function cutSelectedPaths() {
    const selected = getSelectedExplorerPaths();
    hideExplorerContextMenu();
    if (!selected.length) {
        showToast('Select at least one file or folder first.', 'warn');
        return;
    }
    explorerClipboard = { mode: 'cut', paths: selected };
    syncExplorerSelectionClasses();
    updateExplorerSelectionStatus();
    showToast(`${selected.length} item(s) marked to move.`, 'ok');
}

async function pasteClipboard() {
    hideExplorerContextMenu();
    if (!canManageFileContent()) {
        showToast('Paste requires file write permission.', 'warn');
        return;
    }
    const paths = Array.isArray(explorerClipboard.paths) ? explorerClipboard.paths : [];
    if (!paths.length) {
        showToast('Clipboard is empty.', 'warn');
        return;
    }
    const destinationDir = currentExplorerPath();
    try {
        for (const sourcePath of paths) {
            const baseName = sourcePath.split('/').pop() || 'item';
            const destinationPath = safeJoin(destinationDir, baseName);
            const endpoint = explorerClipboard.mode === 'cut' ? 'move-path' : 'copy-path';
            await apiJson(`/api/containers/${endpoint}/${containerId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source_path: sourcePath,
                    destination_path: destinationPath,
                })
            });
        }
        const actionText = explorerClipboard.mode === 'cut' ? 'moved' : 'copied';
        showToast(`${paths.length} item(s) ${actionText} to ${destinationDir}.`, 'ok');
        if (explorerClipboard.mode === 'cut') {
            explorerClipboard = { mode: '', paths: [] };
        }
        await loadFiles();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function archiveSelectedPaths() {
    hideExplorerContextMenu();
    if (!canManageFileContent()) {
        showToast('Archive creation requires file write permission.', 'warn');
        return;
    }
    const selected = getSelectedExplorerPaths();
    if (!selected.length) {
        showToast('Select at least one file or folder first.', 'warn');
        return;
    }
    const defaultName = `${(selected.length === 1 ? selected[0].split('/').pop() : 'workspace-bundle') || 'workspace'}.zip`
        .replace(/\.zip\.zip$/i, '.zip');
    const fileName = window.prompt('ZIP file name', defaultName);
    if (!fileName) return;
    const cleanName = String(fileName).trim().replaceAll('\\', '/').replace(/^\/+/, '');
    if (!cleanName || cleanName.includes('..') || cleanName.includes('/')) {
        showToast('Archive name is invalid.', 'warn');
        return;
    }
    try {
        await apiJson(`/api/containers/archive-paths/${containerId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_paths: selected,
                destination_path: safeJoin(currentExplorerPath(), cleanName.endsWith('.zip') ? cleanName : `${cleanName}.zip`)
            })
        });
        showToast(`ZIP created from ${selected.length} item(s).`, 'ok');
        await loadFiles();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function extractSelectedArchive() {
    hideExplorerContextMenu();
    if (!canManageFileContent()) {
        showToast('Archive extraction requires file write permission.', 'warn');
        return;
    }
    const selected = getSelectedExplorerPaths();
    if (selected.length !== 1) {
        showToast('Select one ZIP file to extract.', 'warn');
        return;
    }
    const target = selected[0];
    if (!target.toLowerCase().endsWith('.zip')) {
        showToast('Only ZIP archives can be extracted from this panel.', 'warn');
        return;
    }
    const suggested = currentExplorerPath();
    const raw = window.prompt('Extract ZIP into directory', suggested);
    if (!raw) return;
    let destination = String(raw).trim().replaceAll('\\', '/');
    if (!destination.startsWith('/')) {
        destination = safeJoin(currentExplorerPath(), destination);
    }
    try {
        await apiJson(`/api/containers/extract-archive/${containerId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                archive_path: target,
                destination_path: destination,
            })
        });
        showToast('Archive extracted.', 'ok');
        await loadFiles();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function editTextFile(path = activePreviewPath) {
    const target = (path || activePreviewPath || '').trim();
    if (!target) {
        showToast('No file selected for editing.', 'warn');
        return;
    }
    if (!canManageFileContent()) {
        showToast('Editing is allowed only for users with file write permission.', 'warn');
        return;
    }
    const modal = document.getElementById('file-preview-modal');
    const pre = document.getElementById('file-preview');
    const title = document.getElementById('preview-path-title');
    activePreviewPath = target;
    previewEditMode = true;
    previewDirty = false;
    title.textContent = `Editing ${target}`;
    modal.style.display = 'flex';
    pre.contentEditable = 'true';
    pre.textContent = `Loading ${target}...`;
    pre.oninput = () => {
        previewDirty = true;
        updateFilePreviewActions();
    };
    updateFilePreviewActions();
    
    (async () => {
        try {
            const data = await apiJson(`/api/containers/file-content/${containerId}?path=${encodeURIComponent(target)}&max_bytes=500000`);
            pre.textContent = data.content || '';
            previewDirty = false;
            updateFilePreviewActions();
            setStatus(`Editing ${target}`);
        } catch (e) {
            pre.textContent = `Cannot load file for editing: ${e.message}`;
            previewEditMode = false;
            previewDirty = false;
            pre.contentEditable = 'false';
            updateFilePreviewActions();
        }
    })();
}

async function saveFile() {
    if (!activePreviewPath) {
        showToast('No file selected for saving.', 'warn');
        return;
    }
    if (!previewEditMode) {
        showToast('Enable edit mode before saving.', 'warn');
        return;
    }
    if (!canManageFileContent()) {
        showToast('Saving is allowed only for users with file write permission.', 'warn');
        return;
    }
    const pre = document.getElementById('file-preview');
    const saveBtn = document.getElementById('preview-save-btn');
    if (!saveBtn) {
        showToast('Save control is unavailable in UI.', 'error');
        return;
    }
    const newContent = pre.textContent || '';
    saveBtn.disabled = true;
    const oldText = saveBtn.textContent;
    saveBtn.textContent = 'Saving...';
    try {
        await apiJson(`/api/containers/save-file/${containerId}?path=${encodeURIComponent(activePreviewPath)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: newContent })
        });
        previewDirty = false;
        updateFilePreviewActions();
        showToast('File saved.', 'ok');
        setStatus(`Saved ${activePreviewPath}`);
        await loadFiles();
    } catch (e) {
        showToast(`Failed to save file: ${e.message}`, 'error');
    } finally {
        saveBtn.textContent = oldText || 'Save';
        updateFilePreviewActions();
    }
}

function goParentDir() {
    const pathEl = document.getElementById('files-path');
    const base = activeWorkspaceRoot || '/data';
    const raw = (pathEl.value || base).trim();
    if (!raw || raw === '/' || raw === base) {
        pathEl.value = base;
        loadFiles();
        return;
    }

    const parts = raw.split('/').filter(Boolean);
    parts.pop();
    pathEl.value = '/' + parts.join('/');
    if (pathEl.value === '' || pathEl.value === '/') pathEl.value = base;
    loadFiles();
}

function handleExplorerKeyboardShortcuts(event) {
    const activeTag = String(document.activeElement?.tagName || '').toLowerCase();
    const typing = activeTag === 'input' || activeTag === 'textarea' || document.activeElement?.isContentEditable;
    if (typing) return;
    if (activeTab !== 'files') return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
        event.preventDefault();
        copySelectedPaths();
        return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'x') {
        event.preventDefault();
        cutSelectedPaths();
        return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') {
        event.preventDefault();
        pasteClipboard();
        return;
    }
    if (event.key === 'Delete') {
        event.preventDefault();
        deleteSelectedPath();
    }
}

async function loadSettings() {
    if (accessPolicy && accessPolicy.allow_settings === false) {
        document.getElementById('settings-meta').textContent = 'Settings access disabled for your role.';
        return;
    }
    try {
        const data = await apiJson(`/api/containers/settings/${containerId}`);
        const savedRules = parsePorts(data.allowed_ports || '');
        availablePorts = Array.isArray(data.available_ports) ? data.available_ports.map(v => String(v || '').trim()).filter(Boolean) : [];
        document.getElementById('set-ports').value = savedRules.join(', ');
        renderPortsSelection(availablePorts, savedRules);
        document.getElementById('set-command').value = data.startup_command || '';
        document.getElementById('set-install-command').value = data.install_command || '';
        document.getElementById('set-domain').value = data.domain_name || '';
        document.getElementById('set-launch-url').value = data.launch_url || '';
        const profileName = (profilePolicy && profilePolicy.profile) || 'generic';
        const protocolValue = data.project_protocol || inferWorkspaceProtocol(profileName, currentContainerImage);
        populateWorkspaceProtocolOptions(profileName, protocolValue);
        updateProtocolHint(protocolValue);
        if (accessPolicy) {
            const canEditStartup = accessPolicy.allow_edit_startup !== false;
            const canEditPorts = accessPolicy.allow_edit_ports !== false;
            const commandEl = document.getElementById('set-command');
            const installEl = document.getElementById('set-install-command');
            const protocolEl = document.getElementById('set-project-protocol');
            const portsEl = document.getElementById('set-ports');
            const domainEl = document.getElementById('set-domain');
            const launchEl = document.getElementById('set-launch-url');
            if (commandEl) commandEl.disabled = !canEditStartup;
            if (installEl) installEl.disabled = !canEditStartup;
            if (protocolEl) protocolEl.disabled = !canEditStartup;
            if (portsEl) portsEl.disabled = !canEditPorts;
            if (domainEl) domainEl.disabled = !canEditPorts;
            if (launchEl) launchEl.disabled = !canEditPorts;
            document.querySelectorAll('#ports-selection input[type="checkbox"]').forEach(node => {
                node.disabled = !canEditPorts;
            });
            syncPortsInputFromSelection();
        }
        if (currentContainerImage.includes('minecraft') && String(data.startup_command || '').trim()) {
            showToast('Minecraft image detected: custom startup command can break boot. Keep command empty.', 'warn', 4200);
        }
        if (data.updated_at) {
            document.getElementById('settings-meta').textContent = `Last saved: ${data.updated_at}${data.updated_by ? ` by ${data.updated_by}` : ''}`;
        } else {
            document.getElementById('settings-meta').textContent = 'No saved settings yet.';
        }
        updateLaunchPreview();
        renderCapabilitySummary(accessPolicy || {});
    } catch (e) {
        document.getElementById('settings-meta').textContent = e.message;
    }
}

async function saveSettings() {
    if (accessPolicy && accessPolicy.allow_settings === false) {
        showToast('Settings access disabled for your role.', 'warn');
        return false;
    }
    const selectedRules = Array.from(document.querySelectorAll('#ports-selection input[type="checkbox"]:checked'))
        .map(node => node.value);
    const fallbackRules = parsePorts(document.getElementById('set-ports').value);
    const finalRules = selectedRules.length ? selectedRules : fallbackRules;
    const payload = {
        allowed_ports: finalRules.join(', '),
        startup_command: document.getElementById('set-command').value,
        project_protocol: document.getElementById('set-project-protocol').value,
        install_command: document.getElementById('set-install-command').value,
        domain_name: document.getElementById('set-domain').value,
        launch_url: document.getElementById('set-launch-url').value
    };
    if (accessPolicy) {
        if (accessPolicy.allow_edit_startup === false) {
            payload.startup_command = '';
            payload.project_protocol = '';
            payload.install_command = '';
        }
        if (accessPolicy.allow_edit_ports === false) {
            payload.allowed_ports = '';
            payload.domain_name = '';
            payload.launch_url = '';
        }
    }

    try {
        const data = await apiJson(`/api/containers/settings/${containerId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        document.getElementById('settings-meta').textContent = `Saved: ${data.updated_at || formatNow()}`;
        showToast('Settings saved.', 'ok');
        return true;
    } catch (e) {
        showToast(e.message, 'error');
        return false;
    }
}

async function loadRestartPolicy() {
    if (accessPolicy && accessPolicy.allow_settings === false) {
        return;
    }
    try {
        const data = await apiJson(`/api/containers/restart-policy/${containerId}`);
        document.getElementById('restart-policy-select').value = data.restart_policy || 'no';
        document.getElementById('restart-retries').value = String(data.maximum_retry_count || 0);
        updateRestartHint();
    } catch (e) {
        showToast(`Restart policy unavailable: ${e.message}`, 'warn');
    }
}

function updateRestartHint() {
    const policy = document.getElementById('restart-policy-select').value;
    const hint = document.getElementById('restart-policy-hint');
    if (policy === 'no') {
        hint.textContent = 'Auto-restart is disabled. Useful for debugging and controlled shutdowns.';
        return;
    }
    if (policy === 'on-failure') {
        hint.textContent = 'Container restarts only on failure. Set retries if needed.';
        return;
    }
    hint.textContent = 'Container will restart automatically. Disable only if you want manual lifecycle control.';
}

async function saveRestartPolicy() {
    if (accessPolicy && accessPolicy.allow_settings === false) {
        showToast('Settings access disabled for your role.', 'warn');
        return;
    }
    const policy = document.getElementById('restart-policy-select').value;
    const retriesRaw = parseInt(document.getElementById('restart-retries').value || '0', 10);
    const retries = Number.isFinite(retriesRaw) ? Math.max(0, retriesRaw) : 0;

    try {
        await apiJson(`/api/containers/restart-policy/${containerId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                restart_policy: policy,
                maximum_retry_count: retries
            })
        });
        updateRestartHint();
        showToast(`Restart policy updated to '${policy}'.`, 'ok');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function applyScenario(type) {
    if (type === 'minecraft') {
        document.getElementById('set-ports').value = '25565:25565';
        document.getElementById('set-command').value = '';
        switchTab('settings');
        showToast('Minecraft profile applied. Startup command was cleared to avoid Java flag crash.', 'warn', 4200);
        return;
    }

    if (type === 'steamcmd') {
        document.getElementById('set-ports').value = '27015:27015/udp, 27016:27016/udp';
        document.getElementById('set-command').value = './start.sh';
        switchTab('settings');
        showToast('SteamCMD scenario applied to Settings.', 'ok');
    }
}

async function fixMinecraftBootLoop() {
    try {
        document.getElementById('set-ports').value = '25565:25565';
        document.getElementById('set-command').value = '';
        const saved = await saveSettings();
        if (!saved) return;
        showToast('Minecraft startup command cleared. Restarting container...', 'ok', 2600);
        await containerAction('restart');
    } catch (e) {
        showToast(`Fix failed: ${e.message}`, 'error');
    }
}

document.getElementById('restart-policy-select')?.addEventListener('change', updateRestartHint);

window.addEventListener('beforeunload', () => {
    if (logsInterval) clearInterval(logsInterval);
});

document.addEventListener('click', (event) => {
    const menu = document.getElementById('explorer-context-menu');
    if (!menu || !menu.classList.contains('open')) return;
    if (!menu.contains(event.target)) {
        hideExplorerContextMenu();
    }
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hideExplorerContextMenu();
    handleExplorerKeyboardShortcuts(event);
});

document.addEventListener('DOMContentLoaded', async () => {
    configureQuickRow('generic', 'console');
    configureToolbox('generic');
    configureStartupCommandHint('generic');
    configureConsoleMode('console', 'generic');
    populateWorkspaceProtocolOptions('generic', 'generic');
    updateProtocolHint('generic');
    updateLaunchPreview();
    updateFilePreviewActions();
    renderExplorerBreadcrumbs(activeWorkspaceRoot || '/data');
    await loadContainerMeta();
    await loadProfilePolicy();
    await initWorkspaceRoots();
    await loadSftpInfo();
    await loadSettings();
    await loadRestartPolicy();
    await loadAuditLog();
    switchTab('terminal');
});
