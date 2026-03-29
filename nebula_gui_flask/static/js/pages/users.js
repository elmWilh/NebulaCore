// nebula_gui_flask/static/js/pages/users.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

let rolesCatalog = [];
let lastUsersPayload = [];
const USERS_SORT_STORAGE_KEY = 'nebula-users-sort-v1';
const PINNED_USERS_STORAGE_KEY = 'nebula-pinned-users-v1';

function t(key, params = {}, fallback = '') {
    return window.NebulaI18n?.t(key, params, fallback) || fallback || key;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function safeDomId(value) {
    return String(value ?? '').replace(/[^a-zA-Z0-9_-]/g, '_');
}

function getUsersSortMode() {
    return localStorage.getItem(USERS_SORT_STORAGE_KEY) || 'pinned';
}

function setUsersSortMode(value) {
    localStorage.setItem(USERS_SORT_STORAGE_KEY, value || 'pinned');
}

function normalizePinnedUser(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const username = String(raw.username || '').trim();
    const db = String(raw.db || raw.db_name || 'system.db').trim() || 'system.db';
    if (!username) return null;
    return {
        username,
        db,
        role_tag: String(raw.role_tag || 'user').trim() || 'user',
        is_staff: !!raw.is_staff,
    };
}

function loadPinnedUsers() {
    try {
        const parsed = JSON.parse(localStorage.getItem(PINNED_USERS_STORAGE_KEY) || '[]');
        return (Array.isArray(parsed) ? parsed : []).map(normalizePinnedUser).filter(Boolean);
    } catch (_) {
        return [];
    }
}

function savePinnedUsers(users) {
    localStorage.setItem(PINNED_USERS_STORAGE_KEY, JSON.stringify(users));
}

function pinnedUserKey(username, db) {
    return `${String(db || 'system.db')}::${String(username || '')}`;
}

function isPinnedUser(username, db) {
    const key = pinnedUserKey(username, db);
    return loadPinnedUsers().some((item) => pinnedUserKey(item.username, item.db) === key);
}

function togglePinnedUser(user, dbName) {
    const normalized = normalizePinnedUser({
        username: user?.username,
        db: dbName,
        role_tag: user?.role_tag,
        is_staff: user?.is_staff,
    });
    if (!normalized) return;
    const key = pinnedUserKey(normalized.username, normalized.db);
    const existing = loadPinnedUsers();
    const next = existing.some((item) => pinnedUserKey(item.username, item.db) === key)
        ? existing.filter((item) => pinnedUserKey(item.username, item.db) !== key)
        : [normalized, ...existing.filter((item) => pinnedUserKey(item.username, item.db) !== key)];
    savePinnedUsers(next);
    renderUsers();
}

function updateUsersSummary(users, dbName) {
    const summaryEl = document.getElementById('users_summary_text');
    if (!summaryEl) return;
    const pinnedCount = users.filter((user) => isPinnedUser(user.username, dbName)).length;
    const staffCount = users.filter((user) => !!user.is_staff).length;
    summaryEl.textContent = `${users.length} members, ${staffCount} staff, ${pinnedCount} pinned in ${String(dbName || '').toUpperCase() || 'DATABASE'}`;
}

function compareUsers(a, b, dbName, mode) {
    const pinnedDelta = Number(isPinnedUser(b.username, dbName)) - Number(isPinnedUser(a.username, dbName));
    if (pinnedDelta !== 0) return pinnedDelta;

    const roleA = String(a.role_tag || '').toLowerCase();
    const roleB = String(b.role_tag || '').toLowerCase();
    const nameCompare = String(a.username || '').localeCompare(String(b.username || ''));

    if (mode === 'name_desc') return -nameCompare;
    if (mode === 'role') {
        const staffDelta = Number(!!b.is_staff) - Number(!!a.is_staff);
        if (staffDelta !== 0) return staffDelta;
        const roleCompare = roleA.localeCompare(roleB);
        return roleCompare !== 0 ? roleCompare : nameCompare;
    }
    if (mode === 'staff_first') {
        const staffDelta = Number(!!b.is_staff) - Number(!!a.is_staff);
        return staffDelta !== 0 ? staffDelta : nameCompare;
    }
    return nameCompare;
}

function getSortedUsers(users, dbName) {
    const mode = getUsersSortMode();
    return [...users].sort((a, b) => compareUsers(a, b, dbName, mode));
}

function renderUsers() {
    const tbody = document.getElementById('user_table_body');
    const dbName = document.getElementById('db_selector').value;
    if (!tbody) return;
    tbody.innerHTML = '';

    const users = getSortedUsers(lastUsersPayload, dbName);
    updateUsersSummary(lastUsersPayload, dbName);

    if (users.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:56px 24px; color:var(--text-muted);">${t('users.no_users_db')}</td></tr>`;
        return;
    }

    users.forEach((user) => {
        const row = document.createElement('tr');
        row.className = `table-row-hover${isPinnedUser(user.username, dbName) ? ' is-pinned-row' : ''}`;
        row.style.borderBottom = '1px solid var(--border)';
        const username = String(user.username ?? '');
        const usernameHtml = escapeHtml(username);
        const menuSuffix = safeDomId(`${dbName}-${username}`);
        const menuId = `drop-${menuSuffix}`;
        const encodedUsername = encodeURIComponent(username);
        const pinned = isPinnedUser(username, dbName);
        row.innerHTML = `
            <td style="padding: 18px 24px;">
                <div style="display:flex; align-items:center; gap:16px;">
                    <div style="width:40px; height:40px; background:#1a1a1e; border-radius:12px; display:flex; align-items:center; justify-content:center; border:1px solid var(--border);">
                        <i class="bi bi-person-circle" style="color:var(--text-muted); font-size:1.2rem;"></i>
                    </div>
                    <div>
                        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                            <div style="font-weight:700; color:white; font-size:0.9rem;">${usernameHtml}</div>
                            ${pinned ? `<span class="pin-chip"><i class="bi bi-pin-angle-fill"></i> ${t('users.pinned')}</span>` : ''}
                        </div>
                        <div style="font-size:0.75rem; color:var(--text-muted);">ID: ${user.id} • DB: ${escapeHtml(dbName)}</div>
                    </div>
                </div>
            </td>
            <td style="padding: 18px 24px;">
                <span class="badge ${user.is_staff ? 'badge-admin' : 'badge-user'}">
                    <i class="bi ${user.is_staff ? 'bi-shield-check' : 'bi-person'}"></i>
                    ${escapeHtml((user.role_tag || 'user').toUpperCase())}
                </span>
            </td>
            <td style="padding: 18px 24px;">
                    <div style="display:flex; align-items:center; gap:10px; color:#4ade80; font-size:0.8rem; font-weight:700;">
                        <div class="pulse-dot"></div> ${t('users.active')}
                    </div>
            </td>
            <td style="padding: 18px 24px; text-align: right;">
                <div class="dropdown">
                    <button class="action-btn" type="button" data-user-menu-toggle="1" data-menu-id="${menuId}">
                        <i class="bi bi-three-dots"></i>
                    </button>
                    <div id="${menuId}" class="dropdown-content">
                        <a href="/users/view/${encodedUsername}?db_name=${encodeURIComponent(dbName)}">
                            <i class="bi bi-shield-shaded"></i> ${t('users.intelligence_profile')}
                        </a>
                        <a href="#" data-action="edit-user" data-username="${usernameHtml}">
                            <i class="bi bi-gear-wide-connected"></i> ${t('users.full_configuration')}
                        </a>
                        <a href="#" data-action="pin-user" data-username="${usernameHtml}">
                            <i class="bi ${pinned ? 'bi-pin-angle-fill' : 'bi-pin-angle'}"></i> ${pinned ? t('users.unpin_from_focus') : t('users.pin_to_focus')}
                        </a>
                        <hr style="border: 0; border-top: 1px solid var(--border); margin: 6px 0;">
                        <a href="#" style="color:#ff6b6b;" data-action="delete-user" data-username="${usernameHtml}">
                            <i class="bi bi-trash3"></i> ${t('users.delete_member')}
                        </a>
                    </div>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });
}

async function initPage() {
    try {
        const dbRes = await fetch('/api/users/databases');
        const dbData = await dbRes.json();
        const selector = document.getElementById('db_selector');
        const modalSelector = document.getElementById('edit_target_db');
        const sortSelect = document.getElementById('users_sort');

        if (sortSelect) {
            sortSelect.value = getUsersSortMode();
            sortSelect.addEventListener('change', () => {
                setUsersSortMode(sortSelect.value);
                renderUsers();
            });
        }

        dbData.databases.forEach((db) => {
            const option = document.createElement('option');
            option.value = db;
            option.textContent = db.toUpperCase();
            selector.appendChild(option);
            modalSelector.appendChild(option.cloneNode(true));
        });
        const rolesRes = await fetch('/api/roles/list');
        const rolesData = await rolesRes.json();
        rolesCatalog = Array.isArray(rolesData) ? rolesData : [];
        const roleSelect = document.getElementById('edit_role');
        roleSelect.innerHTML = '';
        rolesCatalog.forEach((role) => {
            const opt = document.createElement('option');
            opt.value = role.name;
            opt.textContent = role.is_staff ? `${role.name} (staff)` : role.name;
            roleSelect.appendChild(opt);
        });
        if (dbData.databases.length > 0) fetchUsers();
    } catch (e) {
        console.error('Nebula API Error:', e);
    }
}

async function fetchUsers() {
    const dbName = document.getElementById('db_selector').value;
    const tbody = document.getElementById('user_table_body');
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:80px; color:var(--text-muted);"><i class="bi bi-arrow-repeat spin" style="display:inline-block; font-size:1.5rem; margin-bottom:10px;"></i><br>${t('users.syncing')}</td></tr>`;

    try {
        const response = await fetch(`/api/users/list?db_name=${dbName}`);
        const payload = await response.json();
        if (!response.ok) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:40px; color:#ff4f4f;">${escapeHtml(payload.detail || 'Failed to load users')}</td></tr>`;
            return;
        }
        lastUsersPayload = Array.isArray(payload) ? payload : [];
        renderUsers();
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:40px; color:#ff4f4f;">${t('users.db_connection_lost')}</td></tr>`;
    }
}

function toggleMenu(e, id, triggerEl = null) {
    e.stopPropagation();
    const menu = document.getElementById(id);
    if (!menu) return;
    document.querySelectorAll('.dropdown-content').forEach((d) => { if (d !== menu) d.classList.remove('show'); });
    const shouldOpen = !menu.classList.contains('show');
    menu.classList.toggle('show', shouldOpen);
    if (shouldOpen) {
        positionMenu(menu, triggerEl || e.target);
    }
}

function positionMenu(menu, trigger) {
    const anchor = trigger?.closest('button') || trigger;
    if (!menu || !anchor) return;
    const rect = anchor.getBoundingClientRect();
    const vpW = window.innerWidth;
    const vpH = window.innerHeight;

    menu.style.left = '-9999px';
    menu.style.top = '-9999px';
    menu.classList.add('show');

    const menuWidth = menu.offsetWidth || 240;
    const menuHeight = menu.offsetHeight || 200;
    const sidePad = 8;
    const gap = 8;

    let left = rect.right - menuWidth;
    if (left < sidePad) left = sidePad;
    if (left + menuWidth > vpW - sidePad) left = vpW - menuWidth - sidePad;

    let top = rect.bottom + gap;
    if (top + menuHeight > vpH - sidePad) {
        top = rect.top - menuHeight - gap;
    }
    if (top < sidePad) top = sidePad;

    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
}

function openEditModal(name) {
    document.getElementById('edit_old_username').value = name;
    document.getElementById('edit_username').value = name;
    const userRow = lastUsersPayload.find((u) => String(u.username) === String(name));
    const roleTag = userRow?.role_tag || (userRow?.is_staff ? 'admin' : 'user');
    document.getElementById('edit_role').value = roleTag;
    document.getElementById('edit_target_db').value = document.getElementById('db_selector').value;
    document.getElementById('editModal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('editModal').style.display = 'none';
}

async function saveUserEdit() {
    const payload = {
        old_username: document.getElementById('edit_old_username').value,
        new_username: document.getElementById('edit_username').value,
        new_password: document.getElementById('edit_password').value,
        role_tag: document.getElementById('edit_role').value,
        is_active: document.getElementById('edit_is_active').checked,
        source_db: document.getElementById('db_selector').value,
        target_db: document.getElementById('edit_target_db').value
    };

    const res = await fetch('/api/users/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });

    if (res.ok) {
        closeModal();
        fetchUsers();
    } else {
        alert(t('users.operational_error'));
    }
}

async function banUser(username) {
    const name = String(username || '').trim();
    if (!name) return;
    if (!confirm(t('users.delete_confirm', { name }))) return;
    const dbName = document.getElementById('db_selector').value;
    const res = await fetch(`/api/users/delete?db_name=${encodeURIComponent(dbName)}&username=${encodeURIComponent(name)}`, {
        method: 'POST'
    });
    const out = await res.json();
    if (!res.ok) {
        alert(out.detail || t('users.failed_delete'));
        return;
    }
    await fetchUsers();
}

document.addEventListener('click', (event) => {
    const toggleBtn = event.target.closest('[data-user-menu-toggle][data-menu-id]');
    if (toggleBtn) {
        toggleMenu(event, toggleBtn.dataset.menuId, toggleBtn);
        return;
    }

    const actionLink = event.target.closest('[data-action][data-username]');
    if (actionLink) {
        event.preventDefault();
        const action = String(actionLink.dataset.action || '').trim();
        const username = String(actionLink.dataset.username || '').trim();
        document.querySelectorAll('.dropdown-content').forEach((d) => d.classList.remove('show'));
        if (action === 'edit-user') {
            openEditModal(username);
            return;
        }
        if (action === 'pin-user') {
            const dbName = document.getElementById('db_selector').value;
            const user = lastUsersPayload.find((item) => String(item.username) === username);
            togglePinnedUser(user || { username }, dbName);
            return;
        }
        if (action === 'delete-user') {
            banUser(username);
            return;
        }
    }

    if (!event.target.closest('.dropdown')) {
        document.querySelectorAll('.dropdown-content').forEach((d) => d.classList.remove('show'));
    }
});

window.addEventListener('resize', () => {
    document.querySelectorAll('.dropdown-content').forEach((d) => d.classList.remove('show'));
});

window.addEventListener('scroll', () => {
    document.querySelectorAll('.dropdown-content').forEach((d) => d.classList.remove('show'));
}, true);

document.addEventListener('DOMContentLoaded', initPage);
