let roleCatalog = [];

function normalizeDbName(raw) {
    const cleaned = String(raw || '').trim();
    if (!cleaned) return '';
    return cleaned.endsWith('.db') ? cleaned : `${cleaned}.db`;
}

async function loadDatabases() {
    const res = await fetch('/api/users/databases');
    const data = await res.json();
    const select = document.getElementById('target_db');
    select.innerHTML = '<option value="">-- New Database --</option>';
    (data.databases || []).forEach(db => {
        const opt = document.createElement('option');
        opt.value = db;
        opt.textContent = db;
        select.appendChild(opt);
    });
}

async function loadRoles() {
    const res = await fetch('/api/roles/list');
    const data = await res.json();
    roleCatalog = Array.isArray(data) ? data : [];
    const select = document.getElementById('role_select');
    select.innerHTML = '';
    roleCatalog.forEach(role => {
        const opt = document.createElement('option');
        opt.value = role.name;
        opt.textContent = role.is_staff ? `${role.name} (staff)` : role.name;
        select.appendChild(opt);
    });
    if (!select.value && roleCatalog.length > 0) {
        select.value = roleCatalog[0].name;
    }
    updateRoleDescription();
}

function updateRoleDescription() {
    const selected = document.getElementById('role_select').value;
    const role = roleCatalog.find(r => r.name === selected);
    document.getElementById('role_desc').textContent = role?.description || 'No role description.';
}

async function createRole() {
    const name = String(document.getElementById('new_role_name').value || '').trim();
    if (!name) {
        alert('Role name is required');
        return;
    }
    const payload = {
        name,
        description: String(document.getElementById('new_role_desc').value || '').trim(),
        is_staff: !!document.getElementById('new_role_staff').checked
    };
    const res = await fetch('/api/roles/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const out = await res.json();
    if (!res.ok) {
        alert(out.detail || 'Failed to create role');
        return;
    }
    document.getElementById('new_role_name').value = '';
    document.getElementById('new_role_desc').value = '';
    document.getElementById('new_role_staff').checked = false;
    await loadRoles();
    document.getElementById('role_select').value = out.name;
    updateRoleDescription();
}

document.getElementById('role_select').addEventListener('change', updateRoleDescription);

document.getElementById('createUserForm').onsubmit = async (e) => {
    e.preventDefault();
    const password = document.getElementById('password').value;
    const confirmPassword = document.getElementById('confirm_password').value;
    if (password !== confirmPassword) {
        alert('Passwords do not match');
        return;
    }
    const selectedDb = document.getElementById('target_db').value;
    const newDbName = normalizeDbName(document.getElementById('new_db_name').value);
    const dbToUse = newDbName || selectedDb;
    if (!dbToUse) {
        alert('Select DB or enter new DB name');
        return;
    }
    const roleTag = document.getElementById('role_select').value || 'user';
    const payload = {
        username: document.getElementById('username').value,
        password,
        email: document.getElementById('email').value,
        role_tag: roleTag
    };
    const res = await fetch(`/api/users/create?db_name=${encodeURIComponent(dbToUse)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const out = await res.json();
    if (!res.ok) {
        alert(out.detail || 'Failed to create user');
        return;
    }
    window.location.href = '/users';
};

document.addEventListener('DOMContentLoaded', async () => {
    await loadDatabases();
    await loadRoles();
});

