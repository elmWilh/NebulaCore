// nebula_gui_flask/static/js/pages/projects.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

const projectsContext = window.NebulaProjects || {};
const isStaff = !!projectsContext.isStaff;

let activeTab = 'active';
let currentProjects = [];
let allProjects = [];
let expandedProjectIds = new Set();
let availableContainers = [];
let linkModalProjectId = null;
let createSelectedContainerIds = new Set();

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function initials(value) {
    const text = String(value || '').trim();
    if (!text) return '?';
    const parts = text.split(/\s+/).filter(Boolean);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
}

function formatLoad(load) {
    const cpu = Number(load?.cpu_percent || 0);
    const ramMb = Number(load?.memory_mb || 0);
    const ramGb = ramMb / 1024;
    const running = Number(load?.running_containers || 0);
    const total = Number(load?.total_containers || 0);
    return {
        cpuText: `${cpu.toFixed(1)} CPU%`,
        ramText: ramGb >= 1 ? `${ramGb.toFixed(2)} GB RAM` : `${ramMb.toFixed(0)} MB RAM`,
        containersText: `${running}/${total} containers online`
    };
}

function setProjectTab(tab) {
    const normalized = tab === 'archived' ? 'archived' : 'active';
    activeTab = normalized;
    document.getElementById('tab_active')?.classList.toggle('active', normalized === 'active');
    document.getElementById('tab_archived')?.classList.toggle('active', normalized === 'archived');
    expandedProjectIds = new Set();
    fetchProjects();
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
        const detail = String(data?.detail || `HTTP ${res.status}`);
        throw new Error(detail);
    }
    return data;
}

async function fetchProjects() {
    const tbody = document.getElementById('projects_tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="5" style="padding: 20px 24px; color: var(--text-muted);">Loading projects...</td></tr>';
    try {
        const payload = await apiJson(`/api/projects?tab=${encodeURIComponent(activeTab)}`);
        allProjects = Array.isArray(payload?.projects) ? payload.projects : [];
        currentProjects = applyProjectFiltersAndSort(allProjects);
        renderTagFilterOptions(allProjects);
        renderProjects();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" style="padding: 20px 24px; color: #ff7b7b;">Failed to load projects: ${escapeHtml(e.message)}</td></tr>`;
    }
}

function normalizedLoad(project) {
    const cpu = Number(project?.load?.cpu_percent || 0);
    const mem = Number(project?.load?.memory_mb || 0) / 1024;
    return cpu + mem;
}

function applyProjectFiltersAndSort(inputProjects) {
    const query = String(document.getElementById('project_search')?.value || '').trim().toLowerCase();
    const sortBy = String(document.getElementById('project_sort')?.value || 'load_desc');
    const tagFilter = String(document.getElementById('project_tag_filter')?.value || '').trim().toLowerCase();

    let items = (Array.isArray(inputProjects) ? inputProjects : []).filter(p => {
        const tags = Array.isArray(p?.tags) ? p.tags.map(t => String(t || '')) : [];
        if (tagFilter && !tags.some(t => t.toLowerCase() === tagFilter)) return false;
        if (!query) return true;
        const hay = [
            String(p?.name || ''),
            String(p?.description || ''),
            tags.join(' '),
        ].join(' ').toLowerCase();
        return hay.includes(query);
    });

    items.sort((a, b) => {
        if (sortBy === 'name_asc') return String(a?.name || '').localeCompare(String(b?.name || ''));
        if (sortBy === 'name_desc') return String(b?.name || '').localeCompare(String(a?.name || ''));
        if (sortBy === 'updated_desc') return Number(b?.updated_at || 0) - Number(a?.updated_at || 0);
        if (sortBy === 'load_asc') return normalizedLoad(a) - normalizedLoad(b);
        return normalizedLoad(b) - normalizedLoad(a);
    });

    return items;
}

function renderTagFilterOptions(inputProjects) {
    const select = document.getElementById('project_tag_filter');
    if (!select) return;
    const prev = String(select.value || '');
    const tags = new Set();
    (Array.isArray(inputProjects) ? inputProjects : []).forEach(p => {
        (Array.isArray(p?.tags) ? p.tags : []).forEach(tag => {
            const t = String(tag || '').trim();
            if (t) tags.add(t);
        });
    });
    select.innerHTML = '<option value="">All tags</option>';
    Array.from(tags).sort((a, b) => a.localeCompare(b)).forEach(tag => {
        const opt = document.createElement('option');
        opt.value = tag;
        opt.textContent = `#${tag}`;
        if (tag === prev) opt.selected = true;
        select.appendChild(opt);
    });
}

function onProjectFiltersChanged() {
    currentProjects = applyProjectFiltersAndSort(allProjects);
    expandedProjectIds = new Set();
    renderProjects();
}

function renderProjects() {
    const tbody = document.getElementById('projects_tbody');
    if (!tbody) return;

    if (!Array.isArray(currentProjects) || currentProjects.length === 0) {
        const msg = activeTab === 'archived'
            ? 'No archived projects found.'
            : 'No projects available for your account.';
        tbody.innerHTML = `<tr><td colspan="5" style="padding: 20px 24px; color: var(--text-muted);">${escapeHtml(msg)}</td></tr>`;
        return;
    }

    let html = '';
    currentProjects.forEach(project => {
        const pid = String(project.id || '');
        const detailsRowId = `project_details_${pid}`;
        const iconId = `icon_${pid}`;
        const open = expandedProjectIds.has(pid);
        const load = formatLoad(project.load || {});
        const team = Array.isArray(project.team) ? project.team : [];
        const teamPreview = team.slice(0, 3);
        const hiddenCount = Math.max(0, team.length - teamPreview.length);
        const teamHtml = teamPreview.map((u, idx) => {
            const uname = String(u.username || 'unknown');
            return `<div class="avatar-mini" style="margin-left:${idx === 0 ? '0' : '-10px'}" title="${escapeHtml(uname)}">${escapeHtml(initials(uname))}</div>`;
        }).join('');

        const actions = [];
        if (isStaff) {
            if (activeTab === 'active') {
                actions.push(`<button class="action-btn" title="Project settings" data-project-action="settings" data-project-id="${escapeHtml(pid)}"><i class="bi bi-sliders"></i></button>`);
                actions.push(`<button class="action-btn" title="Archive project" style="color: #ef4444;" data-project-action="archive" data-project-id="${escapeHtml(pid)}"><i class="bi bi-archive"></i></button>`);
            } else {
                actions.push(`<button class="action-btn" title="Restore project" data-project-action="restore" data-project-id="${escapeHtml(pid)}"><i class="bi bi-arrow-counterclockwise"></i></button>`);
            }
        } else {
            actions.push('<span style="color: var(--text-muted); font-size: 0.8rem;">View only</span>');
        }

        html += `
            <tr class="project-row" data-project-toggle="${escapeHtml(pid)}">
                <td style="text-align: center;"><i class="bi bi-chevron-right" id="${escapeHtml(iconId)}" style="transform: ${open ? 'rotate(90deg)' : 'rotate(0deg)'};"></i></td>
                <td style="padding: 18px 24px;">
                    <div style="display: flex; align-items: center; gap: 15px;">
                        <div class="container-icon-bg"><i class="bi bi-boxes"></i></div>
                        <div>
                            <div style="font-weight: 700; color: white;">${escapeHtml(project.name || '')}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted);">${escapeHtml(project.description || 'No description')}</div>
                        </div>
                    </div>
                </td>
                <td style="padding: 18px 24px;">
                    <div class="avatar-group" data-project-action="team" data-project-id="${escapeHtml(pid)}">
                        ${teamHtml || '<span style="color: var(--text-muted); font-size: 0.8rem;">No users</span>'}
                        ${hiddenCount > 0 ? `<div class="avatar-mini" style="background: #3f3f46;">+${hiddenCount}</div>` : ''}
                    </div>
                </td>
                <td style="padding: 18px 24px;">
                    <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                        <span class="badge badge-user" style="background: rgba(255,255,255,0.05); border: 1px solid var(--border);">${escapeHtml(load.containersText)}</span>
                        <span class="badge badge-user" style="background: rgba(255,255,255,0.05); border: 1px solid var(--border);">${escapeHtml(load.cpuText)}</span>
                        <span class="badge badge-user" style="background: rgba(255,255,255,0.05); border: 1px solid var(--border);">${escapeHtml(load.ramText)}</span>
                    </div>
                </td>
                <td style="padding: 18px 24px; text-align: right;">
                    <div style="display: flex; justify-content: flex-end; gap: 8px;">
                        ${actions.join('')}
                    </div>
                </td>
            </tr>
            <tr id="${escapeHtml(detailsRowId)}" class="project-details${open ? ' active' : ''}">
                <td colspan="5">
                    <div class="details-container">
                        <div style="display: flex; justify-content: space-between; align-items: center; gap: 10px;">
                            <h4 style="margin: 0; color: white; font-size: 0.8rem; text-transform: uppercase;">Linked Infrastructure</h4>
                            ${isStaff && activeTab === 'active' ? `<button class="btn-secondary" style="padding: 6px 12px; font-size: 0.75rem;" data-project-action="link-modal" data-project-id="${escapeHtml(pid)}">+ Link Container</button>` : ''}
                        </div>
                        <div class="container-grid">
                            ${renderProjectContainers(project)}
                        </div>
                    </div>
                </td>
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

function renderProjectContainers(project) {
    const containers = Array.isArray(project?.containers) ? project.containers : [];
    if (containers.length === 0) {
        return '<div style="padding: 14px; color: var(--text-muted);">No linked containers visible.</div>';
    }

    return containers.map(cont => {
        const isOnline = String(cont.status || '').toLowerCase() === 'running';
        const dot = isOnline ? 'status-online' : 'status-offline';
        const unlinkBtn = isStaff && activeTab === 'active'
            ? `<button class="action-btn" title="Unlink" data-project-action="unlink-container" data-project-id="${escapeHtml(project.id)}" data-container-id="${escapeHtml(cont.full_id || cont.id)}"><i class="bi bi-link-45deg"></i></button>`
            : '<i class="bi bi-link-45deg" style="color: var(--text-muted);"></i>';
        return `
            <div class="resource-card" data-project-action="open-container" data-container-id="${escapeHtml(cont.full_id || cont.id)}">
                <div style="display: flex; align-items: center; min-width: 0;">
                    <span class="status-dot ${dot}"></span>
                    <span style="color: #eee; font-size: 0.85rem; font-weight: 600; white-space: nowrap; text-overflow: ellipsis; overflow: hidden; text-decoration: underline;">${escapeHtml(cont.name || cont.id || 'unknown')}</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">${unlinkBtn}</div>
            </div>
        `;
    }).join('');
}

function toggleProject(projectId) {
    const pid = String(projectId || '');
    if (!pid) return;
    if (expandedProjectIds.has(pid)) expandedProjectIds.delete(pid);
    else expandedProjectIds.add(pid);

    const row = document.getElementById(`project_details_${pid}`);
    const icon = document.getElementById(`icon_${pid}`);
    if (row) row.classList.toggle('active', expandedProjectIds.has(pid));
    if (icon) icon.style.transform = expandedProjectIds.has(pid) ? 'rotate(90deg)' : 'rotate(0deg)';
}

function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'flex';
}

function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
}

function openCreateProjectModal() {
    if (!isStaff) return;
    createSelectedContainerIds = new Set();
    document.getElementById('create_project_name').value = '';
    document.getElementById('create_project_desc').value = '';
    document.getElementById('create_project_tags').value = '';
    const searchInput = document.getElementById('create_container_search');
    if (searchInput) searchInput.value = '';
    loadAvailableContainers()
        .then(() => {
            renderCreateContainerList();
            renderCreateSelectedContainers();
            openModal('createModal');
        })
        .catch((e) => {
            alert(`Failed to load containers: ${e.message}`);
        });
}

async function submitCreateProject() {
    if (!isStaff) return;
    const btn = document.getElementById('create_project_btn');
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = 'Creating...';
    try {
        const payload = {
            name: String(document.getElementById('create_project_name').value || '').trim(),
            description: String(document.getElementById('create_project_desc').value || '').trim(),
            tags: String(document.getElementById('create_project_tags').value || '').trim(),
            container_ids: Array.from(createSelectedContainerIds),
        };
        await apiJson('/api/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        closeModal('createModal');
        fetchProjects();
    } catch (e) {
        alert(`Create failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = oldText;
    }
}

function openProjectSettings(projectId) {
    if (!isStaff) return;
    const pid = String(projectId || '');
    const project = currentProjects.find(p => String(p.id) === pid);
    if (!project) return;

    document.getElementById('edit_project_id').value = pid;
    document.getElementById('edit_project_name').value = String(project.name || '');
    document.getElementById('edit_project_desc').value = String(project.description || '');
    document.getElementById('edit_project_tags').value = Array.isArray(project.tags) ? project.tags.join(', ') : '';
    openModal('settingsModal');
}

async function submitProjectUpdate() {
    if (!isStaff) return;
    const projectId = String(document.getElementById('edit_project_id').value || '').trim();
    if (!projectId) return;

    const btn = document.getElementById('save_project_btn');
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = 'Saving...';
    try {
        const payload = {
            name: String(document.getElementById('edit_project_name').value || '').trim(),
            description: String(document.getElementById('edit_project_desc').value || '').trim(),
            tags: String(document.getElementById('edit_project_tags').value || '').trim()
        };
        await apiJson(`/api/projects/${encodeURIComponent(projectId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        closeModal('settingsModal');
        fetchProjects();
    } catch (e) {
        alert(`Update failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = oldText;
    }
}

async function archiveProject(projectId) {
    if (!isStaff) return;
    if (!confirm('Archive this project?')) return;
    try {
        await apiJson(`/api/projects/${encodeURIComponent(projectId)}/archive`, { method: 'POST' });
        fetchProjects();
    } catch (e) {
        alert(`Archive failed: ${e.message}`);
    }
}

async function restoreProject(projectId) {
    if (!isStaff) return;
    try {
        await apiJson(`/api/projects/${encodeURIComponent(projectId)}/restore`, { method: 'POST' });
        fetchProjects();
    } catch (e) {
        alert(`Restore failed: ${e.message}`);
    }
}

function openTeamModal(projectId) {
    const pid = String(projectId || '');
    const project = currentProjects.find(p => String(p.id) === pid);
    if (!project) return;

    document.getElementById('team_modal_title').textContent = `Project Team: ${project.name || ''}`;
    const host = document.getElementById('team_modal_users');
    const users = Array.isArray(project.team) ? project.team : [];
    if (users.length === 0) {
        host.innerHTML = '<div style="padding: 12px; color: var(--text-muted);">No users assigned through linked containers.</div>';
    } else {
        host.innerHTML = users.map(u => {
            const uname = String(u.username || 'unknown');
            const db = String(u.db_name || 'system.db');
            return `
                <div class="user-option">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <div class="user-avatar-mini">${escapeHtml(initials(uname))}</div>
                        <span style="color:white;">${escapeHtml(uname)} <span style="color: var(--text-muted);">[${escapeHtml(db)}]</span></span>
                    </div>
                </div>
            `;
        }).join('');
    }

    openModal('teamModal');
}

async function loadAvailableContainers() {
    if (!isStaff) return;
    const payload = await apiJson('/api/projects/containers/available');
    availableContainers = Array.isArray(payload?.containers) ? payload.containers : [];
}

function toggleCreateContainer(containerId) {
    const cid = String(containerId || '').trim();
    if (!cid) return;
    if (createSelectedContainerIds.has(cid)) createSelectedContainerIds.delete(cid);
    else createSelectedContainerIds.add(cid);
    renderCreateContainerList();
    renderCreateSelectedContainers();
}

function renderCreateContainerList() {
    const host = document.getElementById('create_container_list');
    if (!host) return;
    const query = String(document.getElementById('create_container_search')?.value || '').trim().toLowerCase();
    const items = availableContainers.filter(c => {
        const name = String(c?.name || '').toLowerCase();
        const id = String(c?.full_id || c?.id || '').toLowerCase();
        if (!query) return true;
        return name.includes(query) || id.includes(query);
    });
    if (!items.length) {
        host.innerHTML = '<div style="padding:12px; color: var(--text-muted);">No containers found.</div>';
        return;
    }
    host.innerHTML = items.map(c => {
        const cid = String(c.full_id || c.id || '');
        const selected = createSelectedContainerIds.has(cid);
        return `
            <div class="user-option" style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
                <div style="min-width:0;">
                    <div style="color:white; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(c.name || cid)}</div>
                    <div style="font-size:0.75rem; color: var(--text-muted);">${escapeHtml(cid)} • ${escapeHtml(String(c.status || 'unknown'))}</div>
                </div>
                <button class="${selected ? 'btn-secondary' : 'btn-primary'}" style="padding: 6px 10px; font-size: 0.78rem;" data-create-container-toggle="${escapeHtml(cid)}">${selected ? 'Selected' : 'Add'}</button>
            </div>
        `;
    }).join('');
}

function renderCreateSelectedContainers() {
    const host = document.getElementById('create_container_selected');
    if (!host) return;
    const selected = Array.from(createSelectedContainerIds);
    if (!selected.length) {
        host.innerHTML = '<span style="font-size:0.75rem; color: var(--text-muted);">No containers selected</span>';
        return;
    }
    host.innerHTML = selected.map(cid => {
        const cont = availableContainers.find(c => String(c.full_id || c.id || '') === cid);
        const label = String(cont?.name || cid);
        return `<span class="badge badge-user" style="margin-right:8px; margin-bottom:8px; display:inline-flex; align-items:center; gap:8px;">${escapeHtml(label)} <button type="button" data-create-container-toggle="${escapeHtml(cid)}" style="background:none;border:none;color:#ff9e9e;cursor:pointer;">×</button></span>`;
    }).join('');
}

async function openLinkContainerModal(projectId) {
    if (!isStaff) return;
    linkModalProjectId = String(projectId || '').trim();
    if (!linkModalProjectId) return;
    try {
        await loadAvailableContainers();
        const input = document.getElementById('link_container_search');
        if (input) input.value = '';
        renderLinkContainerList();
        openModal('linkContainerModal');
    } catch (e) {
        alert(`Failed to load containers: ${e.message}`);
    }
}

function renderLinkContainerList() {
    const host = document.getElementById('link_container_list');
    if (!host) return;

    const project = currentProjects.find(p => String(p.id) === String(linkModalProjectId));
    const linkedIds = new Set((project?.containers || []).map(c => String(c.full_id || c.id || '')));

    const query = String(document.getElementById('link_container_search')?.value || '').trim().toLowerCase();
    const items = availableContainers.filter(c => {
        const name = String(c.name || '').toLowerCase();
        const id = String(c.full_id || c.id || '').toLowerCase();
        if (linkedIds.has(String(c.full_id || c.id || ''))) return false;
        if (!query) return true;
        return name.includes(query) || id.includes(query);
    });

    if (items.length === 0) {
        host.innerHTML = '<div style="padding:12px; color: var(--text-muted);">No matching containers available.</div>';
        return;
    }

    host.innerHTML = items.map(c => {
        const cid = String(c.full_id || c.id || '');
        const status = String(c.status || 'unknown');
        return `
            <div class="user-option" style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
                <div style="min-width:0;">
                    <div style="color:white; font-weight:600; white-space:nowrap; text-overflow:ellipsis; overflow:hidden;">${escapeHtml(c.name || cid)}</div>
                    <div style="color: var(--text-muted); font-size: 0.75rem;">${escapeHtml(cid)} • ${escapeHtml(status)}</div>
                </div>
                <button class="btn-primary" style="padding: 6px 10px; font-size: 0.78rem;" data-link-container-id="${escapeHtml(cid)}">Link</button>
            </div>
        `;
    }).join('');
}

async function linkContainerToProject(containerId) {
    if (!isStaff || !linkModalProjectId) return;
    try {
        await apiJson(`/api/projects/${encodeURIComponent(linkModalProjectId)}/containers/link`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ container_id: String(containerId || '') })
        });
        await fetchProjects();
        await loadAvailableContainers();
        renderLinkContainerList();
    } catch (e) {
        alert(`Link failed: ${e.message}`);
    }
}

async function unlinkContainer(projectId, containerId) {
    if (!isStaff) return;
    if (!confirm('Unlink container from this project?')) return;
    try {
        await apiJson(`/api/projects/${encodeURIComponent(projectId)}/containers/unlink`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ container_id: String(containerId || '') })
        });
        fetchProjects();
    } catch (e) {
        alert(`Unlink failed: ${e.message}`);
    }
}

window.setProjectTab = setProjectTab;
window.toggleProject = toggleProject;
window.openModal = openModal;
window.closeModal = closeModal;
window.openCreateProjectModal = openCreateProjectModal;
window.onProjectFiltersChanged = onProjectFiltersChanged;
window.submitCreateProject = submitCreateProject;
window.openProjectSettings = openProjectSettings;
window.submitProjectUpdate = submitProjectUpdate;
window.archiveProject = archiveProject;
window.restoreProject = restoreProject;
window.openTeamModal = openTeamModal;
window.openLinkContainerModal = openLinkContainerModal;
window.renderLinkContainerList = renderLinkContainerList;
window.linkContainerToProject = linkContainerToProject;
window.unlinkContainer = unlinkContainer;
window.toggleCreateContainer = toggleCreateContainer;
window.renderCreateContainerList = renderCreateContainerList;

function setupProjectDelegatedHandlers() {
    const tbody = document.getElementById('projects_tbody');
    if (tbody) {
        tbody.addEventListener('click', (event) => {
            const actionEl = event.target.closest('[data-project-action]');
            if (actionEl) {
                event.stopPropagation();
                const action = String(actionEl.getAttribute('data-project-action') || '');
                const projectId = String(actionEl.getAttribute('data-project-id') || '');
                if (action === 'settings') openProjectSettings(projectId);
                else if (action === 'archive') archiveProject(projectId);
                else if (action === 'restore') restoreProject(projectId);
                else if (action === 'team') openTeamModal(projectId);
                else if (action === 'link-modal') openLinkContainerModal(projectId);
                else if (action === 'open-container') {
                    const containerId = String(actionEl.getAttribute('data-container-id') || '');
                    if (containerId) {
                        window.location.href = `/containers/view/${encodeURIComponent(containerId)}`;
                    }
                }
                else if (action === 'unlink-container') {
                    const containerId = String(actionEl.getAttribute('data-container-id') || '');
                    unlinkContainer(projectId, containerId);
                }
                return;
            }
            const toggleRow = event.target.closest('[data-project-toggle]');
            if (toggleRow) {
                const projectId = String(toggleRow.getAttribute('data-project-toggle') || '');
                toggleProject(projectId);
            }
        });
    }

    const createList = document.getElementById('create_container_list');
    if (createList) {
        createList.addEventListener('click', (event) => {
            const toggleEl = event.target.closest('[data-create-container-toggle]');
            if (!toggleEl) return;
            const containerId = String(toggleEl.getAttribute('data-create-container-toggle') || '');
            toggleCreateContainer(containerId);
        });
    }

    const createSelected = document.getElementById('create_container_selected');
    if (createSelected) {
        createSelected.addEventListener('click', (event) => {
            const toggleEl = event.target.closest('[data-create-container-toggle]');
            if (!toggleEl) return;
            const containerId = String(toggleEl.getAttribute('data-create-container-toggle') || '');
            toggleCreateContainer(containerId);
        });
    }

    const linkList = document.getElementById('link_container_list');
    if (linkList) {
        linkList.addEventListener('click', (event) => {
            const linkEl = event.target.closest('[data-link-container-id]');
            if (!linkEl) return;
            const containerId = String(linkEl.getAttribute('data-link-container-id') || '');
            linkContainerToProject(containerId);
        });
    }
}

window.addEventListener('click', (e) => {
    const target = e.target;
    if (target && target.classList && target.classList.contains('modal-overlay')) {
        target.style.display = 'none';
    }
});

window.addEventListener('DOMContentLoaded', () => {
    setupProjectDelegatedHandlers();
    setProjectTab('active');
});
