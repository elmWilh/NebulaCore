// --- Logic Core ---
let rolesCatalog = [];
let lastUsersPayload = [];

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
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

async function initPage() {
    try {
        const dbRes = await fetch('/api/users/databases');
        const dbData = await dbRes.json();
        const selector = document.getElementById('db_selector');
        const modalSelector = document.getElementById('edit_target_db');
        
        dbData.databases.forEach(db => {
            const option = document.createElement('option');
            option.value = db; option.textContent = db.toUpperCase();
            selector.appendChild(option);
            modalSelector.appendChild(option.cloneNode(true));
        });
        const rolesRes = await fetch('/api/roles/list');
        const rolesData = await rolesRes.json();
        rolesCatalog = Array.isArray(rolesData) ? rolesData : [];
        const roleSelect = document.getElementById('edit_role');
        roleSelect.innerHTML = '';
        rolesCatalog.forEach(role => {
            const opt = document.createElement('option');
            opt.value = role.name;
            opt.textContent = role.is_staff ? `${role.name} (staff)` : role.name;
            roleSelect.appendChild(opt);
        });
        if (dbData.databases.length > 0) fetchUsers();
    } catch (e) { console.error("Nebula API Error:", e); }
}

async function fetchUsers() {
    const dbName = document.getElementById('db_selector').value;
    const tbody = document.getElementById('user_table_body');
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:80px; color:var(--text-muted);"><i class="bi bi-arrow-repeat spin" style="display:inline-block; font-size:1.5rem; margin-bottom:10px;"></i><br>Syncing Data...</td></tr>';

    try {
        const response = await fetch(`/api/users/list?db_name=${dbName}`);
        const payload = await response.json();
        const users = Array.isArray(payload) ? payload : [];
        lastUsersPayload = users;
        tbody.innerHTML = '';

        if (users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:56px 24px; color:var(--text-muted);">No users found in this database.</td></tr>';
            return;
        }
        
        users.forEach(user => {
            const row = document.createElement('tr');
            row.className = "table-row-hover";
            row.style.borderBottom = '1px solid var(--border)';
            const username = String(user.username ?? '');
            const usernameHtml = escapeHtml(username);
            const usernameJs = jsQuote(username);
            const menuSuffix = safeDomId(username);
            const menuId = `drop-${menuSuffix}`;
            const encodedUsername = encodeURIComponent(username);
            row.innerHTML = `
                <td style="padding: 18px 24px;">
                    <div style="display:flex; align-items:center; gap:16px;">
                        <div style="width:40px; height:40px; background:#1a1a1e; border-radius:12px; display:flex; align-items:center; justify-content:center; border:1px solid var(--border);">
                            <i class="bi bi-person-circle" style="color:var(--text-muted); font-size:1.2rem;"></i>
                        </div>
                        <div>
                            <div style="font-weight:700; color:white; font-size:0.9rem;">${usernameHtml}</div>
                            <div style="font-size:0.75rem; color:var(--text-muted);">ID: ${user.id}</div>
                        </div>
                    </div>
                </td>
                <td style="padding: 18px 24px;">
                    <span class="badge ${user.is_staff ? 'badge-admin' : 'badge-user'}">
                        <i class="bi ${user.is_staff ? 'bi-shield-check' : 'bi-person'}"></i>
                        ${(user.role_tag || 'user').toUpperCase()}
                    </span>
                </td>
                <td style="padding: 18px 24px;">
                    <div style="display:flex; align-items:center; gap:10px; color:#4ade80; font-size:0.8rem; font-weight:700;">
                        <div class="pulse-dot"></div> ACTIVE
                    </div>
                </td>
                <td style="padding: 18px 24px; text-align: right;">
                    <div class="dropdown">
                        <button class="action-btn" onclick="toggleMenu(event, '${menuId}')">
                            <i class="bi bi-three-dots"></i>
                        </button>
                        <div id="${menuId}" class="dropdown-content">
                            <a href="/users/view/${encodedUsername}?db_name=${encodeURIComponent(dbName)}">
                                <i class="bi bi-shield-shaded"></i> Intelligence Profile
                            </a>
                            <a href="javascript:void(0)" onclick="openEditModal('${usernameJs}')">
                                <i class="bi bi-gear-wide-connected"></i> Full Configuration
                            </a>
                            <a href="javascript:void(0)" onclick="pinUser('${usernameJs}')">
                                <i class="bi bi-pin-angle"></i> Pin to Dashboard
                            </a>
                            <hr style="border: 0; border-top: 1px solid var(--border); margin: 6px 0;">
                            <a href="javascript:void(0)" style="color:#ff6b6b;" onclick="banUser('${usernameJs}')">
                                <i class="bi bi-trash3"></i> Delete Member
                            </a>
                        </div>
                    </div>
                </td>
            `;
            tbody.appendChild(row);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:40px; color:#ff4f4f;">Database Connection Lost</td></tr>';
    }
}

// --- Interaction UI ---
function toggleMenu(e, id) {
    e.stopPropagation();
    const menu = document.getElementById(id);
    document.querySelectorAll('.dropdown-content').forEach(d => { if(d !== menu) d.classList.remove('show'); });
    const shouldOpen = !menu.classList.contains('show');
    menu.classList.toggle('show', shouldOpen);
    if (shouldOpen) {
        positionMenu(menu, e.currentTarget || e.target);
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
    const userRow = lastUsersPayload.find(u => String(u.username) === String(name));
    const roleTag = userRow?.role_tag || (userRow?.is_staff ? 'admin' : 'user');
    document.getElementById('edit_role').value = roleTag;
    document.getElementById('edit_target_db').value = document.getElementById('db_selector').value;
    document.getElementById('editModal').style.display = 'flex';
}

function closeModal() { document.getElementById('editModal').style.display = 'none'; }

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

    if(res.ok) { closeModal(); fetchUsers(); } else { alert("Operational Error: DB Locked or Access Denied"); }
}

function pinUser(username) {
    localStorage.setItem('nebula-pinned-user', String(username || '').trim());
    alert(`Pinned: ${username}`);
}

async function banUser(username) {
    const name = String(username || '').trim();
    if (!name) return;
    if (!confirm(`Delete user "${name}"?`)) return;
    const dbName = document.getElementById('db_selector').value;
    const res = await fetch(`/api/users/delete?db_name=${encodeURIComponent(dbName)}&username=${encodeURIComponent(name)}`, {
        method: 'POST'
    });
    const out = await res.json();
    if (!res.ok) {
        alert(out.detail || 'Failed to delete user');
        return;
    }
    await fetchUsers();
}

document.addEventListener('click', (event) => {
    if (!event.target.closest('.dropdown')) {
        document.querySelectorAll('.dropdown-content').forEach(d => d.classList.remove('show'));
    }
});
window.addEventListener('resize', () => {
    document.querySelectorAll('.dropdown-content').forEach(d => d.classList.remove('show'));
});
window.addEventListener('scroll', () => {
    document.querySelectorAll('.dropdown-content').forEach(d => d.classList.remove('show'));
}, true);
document.addEventListener('DOMContentLoaded', initPage);


