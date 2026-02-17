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
        loadLogs();
        if (logsAutoEnabled) {
            logsInterval = setInterval(loadLogs, 4000);
        }
    } else if (tab === 'files') {
        loadFiles();
    } else if (tab === 'settings') {
        loadSettings();
        loadRestartPolicy();
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
        Promise.all([loadSettings(), loadRestartPolicy()]);
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
            placeholder: 'e.g. python app.py or gunicorn app:app',
            helper: 'Overrides default startup command for Python services.'
        },
        web: {
            placeholder: 'e.g. nginx -g "daemon off;" or npm run start',
            helper: 'Overrides default startup command for web services.'
        },
        database: {
            placeholder: 'e.g. (usually keep image default)',
            helper: 'Databases usually should keep image startup defaults.'
        },
        generic: {
            placeholder: 'e.g. ./start.sh or python app.py',
            helper: 'Overrides default startup command for this container.'
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
    });
    const meta = document.getElementById('ports-selection-meta');
    const total = document.querySelectorAll('#ports-selection .port-toggle').length;
    if (meta && total > 0) {
        meta.textContent = `Selected ${selected.length} of ${total} available rules.`;
    }
}

function filterPortsSelection() {
    const filter = String(document.getElementById('ports-search')?.value || '').trim().toLowerCase();
    document.querySelectorAll('#ports-selection .port-toggle').forEach(label => {
        const text = String(label.dataset.rule || '').toLowerCase();
        label.style.display = !filter || text.includes(filter) ? '' : 'none';
    });
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

        const text = document.createElement('span');
        text.className = 'port-rule-text';
        text.textContent = rule;

        label.addEventListener('click', (event) => {
            event.preventDefault();
            check.checked = !check.checked;
            syncPortsInputFromSelection();
        });
        label.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            check.checked = !check.checked;
            syncPortsInputFromSelection();
        });

        label.appendChild(check);
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
        applyWorkspacePermissions();

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
                body: JSON.stringify({ command: cmd })
            });
            const exitCode = Number(data.exit_code ?? 0);
            appendConsoleBlock(`shell$ ${cmd} (exit ${exitCode})`, data.output || '(no output)', exitCode !== 0);
            document.getElementById('cmd-status').textContent = `Shell command completed at ${formatNow()}`;
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
    } catch (e) {
        appendConsoleBlock(cmd, e.message, true);
        document.getElementById('cmd-status').textContent = 'Command failed.';
        showToast(e.message, 'error');
    } finally {
        setButtonBusy('cmd-run-btn', false, idleLabel, busyLabel);
    }
}

function runCommandText(text) {
    if (document.getElementById('cmd-input')?.disabled) {
        showToast('Interactive console is unavailable for this profile.', 'warn');
        return;
    }
    const input = document.getElementById('cmd-input');
    input.value = text;
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
    loadLogs();
    logsInterval = setInterval(loadLogs, 4000);
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

async function loadFiles() {
    if (accessPolicy && accessPolicy.allow_explorer === false) {
        const list = document.getElementById('files-list');
        list.innerHTML = '<div class="file-row error"><div class="file-info"><i class="bi bi-shield-lock"></i><span>Explorer access disabled for your role.</span></div></div>';
        return;
    }
    const pathEl = document.getElementById('files-path');
    const list = document.getElementById('files-list');
    const path = (pathEl.value || activeWorkspaceRoot || '/data').trim() || '/data';
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

        if (!Array.isArray(data.entries) || data.entries.length === 0) {
            renderFileState('bi-inbox', 'This directory is empty.');
            return;
        }

        list.innerHTML = '';
        data.entries.forEach(entry => {
            const row = document.createElement('div');
            row.className = 'file-row';
            const icon = entry.type === 'dir' ? 'bi-folder2-open' : (entry.type === 'link' ? 'bi-link-45deg' : 'bi-file-earmark-text');
            const info = document.createElement('div');
            info.className = 'file-info';
            const iconEl = document.createElement('i');
            iconEl.className = `bi ${icon}`;
            const nameEl = document.createElement('span');
            nameEl.textContent = entry.name || '';
            info.appendChild(iconEl);
            info.appendChild(nameEl);
            const sizeEl = document.createElement('div');
            sizeEl.className = 'file-size';
            sizeEl.textContent = entry.size || '';
            row.appendChild(info);
            row.appendChild(sizeEl);
            row.onclick = () => {
                if (entry.type === 'dir') {
                    pathEl.value = safeJoin(data.path, entry.name);
                    loadFiles();
                } else {
                    previewFile(safeJoin(data.path, entry.name));
                }
            };
            list.appendChild(row);
        });

        setStatus(`Browsing ${pathEl.value}`);
    } catch (e) {
        renderFileState('bi-exclamation-triangle', e.message || 'Failed to load directory.', true);
    }
}

async function previewFile(path) {
    const modal = document.getElementById('file-preview-modal');
    const pre = document.getElementById('file-preview');
    const title = document.getElementById('preview-path-title');
    title.textContent = path;
    modal.style.display = 'flex';
    pre.textContent = `Reading ${path}...`;

    try {
        const data = await apiJson(`/api/containers/file-content/${containerId}?path=${encodeURIComponent(path)}&max_bytes=200000`);
        const trunc = data.truncated ? '\n\n--- file truncated ---' : '';
        pre.textContent = `${data.content || '(empty file)'}${trunc}`;
        setStatus(`Previewing ${path}`);
    } catch (e) {
        pre.textContent = `Cannot read file: ${e.message}`;
    }
}

function closeFilePreviewModal() {
    const modal = document.getElementById('file-preview-modal');
    modal.style.display = 'none';
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
        if (accessPolicy) {
            const canEditStartup = accessPolicy.allow_edit_startup !== false;
            const canEditPorts = accessPolicy.allow_edit_ports !== false;
            const commandEl = document.getElementById('set-command');
            const portsEl = document.getElementById('set-ports');
            if (commandEl) commandEl.disabled = !canEditStartup;
            if (portsEl) portsEl.disabled = !canEditPorts;
            document.querySelectorAll('#ports-selection input[type="checkbox"]').forEach(node => {
                node.disabled = !canEditPorts;
            });
        }
        if (currentContainerImage.includes('minecraft') && String(data.startup_command || '').trim()) {
            showToast('Minecraft image detected: custom startup command can break boot. Keep command empty.', 'warn', 4200);
        }
        if (data.updated_at) {
            document.getElementById('settings-meta').textContent = `Last saved: ${data.updated_at}${data.updated_by ? ` by ${data.updated_by}` : ''}`;
        } else {
            document.getElementById('settings-meta').textContent = 'No saved settings yet.';
        }
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
        startup_command: document.getElementById('set-command').value
    };
    if (accessPolicy) {
        if (accessPolicy.allow_edit_startup === false) payload.startup_command = '';
        if (accessPolicy.allow_edit_ports === false) payload.allowed_ports = '';
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

document.addEventListener('DOMContentLoaded', async () => {
    configureQuickRow('generic', 'console');
    configureToolbox('generic');
    configureStartupCommandHint('generic');
    configureConsoleMode('console', 'generic');
    await loadContainerMeta();
    await loadProfilePolicy();
    await initWorkspaceRoots();
    await loadSettings();
    await loadRestartPolicy();
    switchTab('terminal');
});
