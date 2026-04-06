let accessOverview = null;
let accessDatabases = [];
let accessModalSubmitHandler = null;

function acEscape(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function acToast(message) {
  window.alert(message);
}

async function acJson(url, options = {}) {
  const res = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || 'Request failed');
  }
  return data;
}

function accessDbName() {
  const el = document.getElementById('access_db_selector');
  return el?.value || 'system.db';
}

function riskClass(level) {
  return `risk-${String(level || 'low').toLowerCase()}`;
}

function formatAgo(ts) {
  const value = Number(ts || 0) * 1000;
  if (!value) return 'Never';
  return new Date(value).toLocaleString();
}

function formatCountLabel(value, singular, plural) {
  const count = Number(value || 0);
  return `${count} ${count === 1 ? singular : plural}`;
}

function openAccessModal(label, title, contentHtml, onSubmit) {
  const modal = document.getElementById('access_modal');
  if (!modal) return;
  document.getElementById('access_modal_label').textContent = label;
  document.getElementById('access_modal_title').textContent = title;
  const subtitle = document.getElementById('access_modal_subtitle');
  if (subtitle) {
    subtitle.textContent = `Manage ${String(title || label || 'access').toLowerCase()}.`;
  }
  const host = document.getElementById('access_modal_content');
  host.innerHTML = contentHtml;
  const form = host.querySelector('form');
  if (accessModalSubmitHandler && form) {
    form.removeEventListener('submit', accessModalSubmitHandler);
  }
  if (form && typeof onSubmit === 'function') {
    accessModalSubmitHandler = async (event) => {
      event.preventDefault();
      try {
        await onSubmit(new FormData(form));
        closeAccessModal();
        await loadAccessOverview();
      } catch (error) {
        acToast(error.message);
      }
    };
    form.addEventListener('submit', accessModalSubmitHandler);
  }
  modal.style.display = 'flex';
  document.body.classList.add('modal-open');
  setTimeout(() => {
    host.querySelector('input, textarea, select, button')?.focus();
  }, 0);
}

function closeAccessModal() {
  const modal = document.getElementById('access_modal');
  if (modal) {
    modal.style.display = 'none';
  }
  document.body.classList.remove('modal-open');
  accessModalSubmitHandler = null;
}

function renderSummary(summary = {}) {
  const host = document.getElementById('access_summary_cards');
  if (!host) return;
  const cards = [
    ['Users', summary.users || 0, 'Accounts available in this database'],
    ['Roles', summary.roles || 0, 'Reusable access profiles'],
    ['Groups', summary.groups || 0, 'Shared bundles for teams and workloads'],
    ['Permissions', summary.permissions || 0, 'Capabilities that can be assigned'],
  ];
  host.innerHTML = cards.map(([label, value, sub]) => `
    <div class="metric-card">
      <div class="metric-kicker">${acEscape(label)}</div>
      <div class="metric-value">${acEscape(value)}</div>
      <div class="metric-label">${acEscape(sub)}</div>
    </div>
  `).join('');
}

function renderRoles(roles = [], permissions = []) {
  const host = document.getElementById('roles_board');
  if (!host) return;
  if (!roles.length) {
    host.innerHTML = '<div class="muted">No roles yet.</div>';
    return;
  }
  host.innerHTML = roles.map((role) => `
    <div class="role-card">
      <div class="role-card-header">
        <div class="stack-tight">
          <div class="card-title-line">
            <strong>${acEscape(role.name)}</strong>
            <span class="soft-pill">${role.is_staff ? 'staff' : 'standard'}</span>
          </div>
          <div class="muted">${acEscape(role.description || 'No description')}</div>
        </div>
        <div class="card-action-column">
          <span class="soft-pill">${acEscape(formatCountLabel(role.permission_count || 0, 'permission', 'permissions'))}</span>
          <button class="btn-secondary" type="button" data-role-edit="${acEscape(role.name)}">Edit</button>
        </div>
      </div>
      <div class="pill-row">
        ${(Array.isArray(role.permissions) ? role.permissions : []).slice(0, 6).map((item) => `<span class="perm-pill">${acEscape(item)}</span>`).join('') || '<span class="muted">No permissions assigned.</span>'}
        ${(role.permissions || []).length > 6 ? `<span class="soft-pill">+${acEscape((role.permissions || []).length - 6)} more</span>` : ''}
      </div>
    </div>
  `).join('');
  host.querySelectorAll('[data-role-edit]').forEach((button) => {
    button.addEventListener('click', () => openRolePermissionsEditor(button.dataset.roleEdit, roles, permissions));
  });
}

function renderPermissions(items = []) {
  const host = document.getElementById('permissions_list');
  if (!host) return;
  host.innerHTML = items.map((item) => `
    <div class="permission-card">
      <div class="stack-tight">
        <div class="card-title-line">
          <strong>${acEscape(item.label)}</strong>
          <span class="risk-pill ${riskClass(item.risk_level)}">${acEscape(item.risk_level || 'low')}</span>
        </div>
        <div class="muted mono">${acEscape(item.key)}</div>
        <div class="muted">${acEscape(item.description || item.category || 'No description')}</div>
      </div>
    </div>
  `).join('') || '<div class="muted">No permissions yet.</div>';
}

function renderGroups(groups = []) {
  const host = document.getElementById('groups_list');
  if (!host) return;
  host.innerHTML = groups.map((group) => `
    <div class="group-card">
      <div class="group-card-header">
        <div class="stack-tight">
          <div class="card-title-line">
            <strong>${acEscape(group.title || group.group_name)}</strong>
            <span class="soft-pill">${acEscape(group.scope || 'containers')}</span>
          </div>
          <div class="muted mono">${acEscape(group.group_name)}</div>
        </div>
        <button class="btn-secondary" type="button" data-group-edit="${acEscape(group.group_name)}">Manage</button>
      </div>
      <div class="simple-stats">
        <div class="simple-stat">
          <span>Members</span>
          <strong>${acEscape(group.members_count || 0)}</strong>
        </div>
        <div class="simple-stat">
          <span>Containers</span>
          <strong>${acEscape(group.containers_count || 0)}</strong>
        </div>
        <div class="simple-stat">
          <span>Priority</span>
          <strong>${acEscape(group.priority || 100)}</strong>
        </div>
      </div>
      <div class="pill-row">
        ${(group.members || []).slice(0, 3).map((item) => `<span class="perm-pill">${acEscape(item.username)}</span>`).join('') || '<span class="muted">No members assigned.</span>'}
      </div>
    </div>
  `).join('') || '<div class="muted">No groups yet.</div>';
  host.querySelectorAll('[data-group-edit]').forEach((button) => {
    button.addEventListener('click', () => openGroupEditor(button.dataset.groupEdit));
  });
}

function renderUsers(users = []) {
  const host = document.getElementById('users_table_wrap');
  if (!host) return;
  const query = String(document.getElementById('access_user_search')?.value || '').trim().toLowerCase();
  const filtered = users.filter((item) => !query || String(item.username || '').toLowerCase().includes(query));
  if (!filtered.length) {
    host.innerHTML = '<div class="muted">No users found.</div>';
    return;
  }
  host.innerHTML = filtered.map((item) => `
    <article class="user-row">
      <div class="row-content">
        <div class="row-main">
          <div class="stack-tight">
            <div class="card-title-line">
              <strong class="row-title">${acEscape(item.username)}</strong>
              ${item.is_staff ? '<span class="soft-pill">staff</span>' : ''}
              <span class="soft-pill">${acEscape(item.role_tag || 'user')}</span>
            </div>
            <div class="row-subtitle">${acEscape(item.email || 'No email')}</div>
          </div>
          <span class="risk-pill ${riskClass(item.network_risk_level)}">${acEscape(item.network_risk_level || 'low')} risk</span>
        </div>
        <div class="row-meta">
          <span class="soft-pill">${acEscape(formatCountLabel(item.groups_count || 0, 'group', 'groups'))}</span>
          <span class="soft-pill">${acEscape(formatCountLabel((item.direct_containers_count || 0) + (item.group_containers_count || 0), 'container', 'containers'))}</span>
          <span class="soft-pill mono">${acEscape(item.last_ip || 'unknown IP')}</span>
          <span class="soft-pill">Updated ${acEscape(formatAgo(item.last_ip_seen_at))}</span>
        </div>
        <div class="pill-row">
          ${(item.group_names || []).slice(0, 6).map((name) => `<span class="perm-pill">${acEscape(name)}</span>`).join('') || '<span class="muted">No groups assigned.</span>'}
        </div>
      </div>
      <button class="btn-secondary" type="button" data-user-history="${acEscape(item.username)}">History</button>
    </article>
  `).join('');
  host.querySelectorAll('[data-user-history]').forEach((button) => {
    button.addEventListener('click', () => openUserHistory(button.dataset.userHistory));
  });
}

async function loadAccessOverview() {
  accessOverview = await acJson(`/api/security/overview?db_name=${encodeURIComponent(accessDbName())}`);
  renderSummary(accessOverview.summary || {});
  renderRoles(accessOverview.roles || [], accessOverview.permissions || []);
  renderPermissions(accessOverview.permissions || []);
  renderGroups(accessOverview.groups || []);
  renderUsers(accessOverview.users || []);
}

function openRoleCreator() {
  openAccessModal('Role', 'Create Role', `
    <form class="modal-body">
      <div class="modal-copy">Create a reusable role and assign permissions to it later.</div>
      <div class="modal-grid">
        <input name="name" placeholder="role name" required>
        <input name="description" placeholder="description">
      </div>
      <label><input type="checkbox" name="is_staff"> Mark as staff role</label>
      <div class="modal-actions"><button class="btn-primary" type="submit">Save role</button></div>
    </form>
  `, async (form) => {
    await acJson('/api/roles/create', {
      method: 'POST',
      body: JSON.stringify({
        name: form.get('name'),
        description: form.get('description'),
        is_staff: form.get('is_staff') === 'on',
      }),
    });
  });
}

function openPermissionCreator() {
  openAccessModal('Permission', 'Create Permission', `
    <form class="modal-body">
      <div class="modal-copy">Add a permission that can be attached to roles.</div>
      <div class="modal-grid">
        <input name="key" placeholder="containers.access.write" required>
        <input name="label" placeholder="Human label" required>
        <input name="category" placeholder="rbac / audit / containers">
        <select name="risk_level">
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="elevated">elevated</option>
          <option value="high">high</option>
          <option value="critical">critical</option>
        </select>
      </div>
      <textarea name="description" rows="4" placeholder="What this permission controls"></textarea>
      <div class="modal-actions"><button class="btn-primary" type="submit">Save permission</button></div>
    </form>
  `, async (form) => {
    await acJson('/api/security/permissions', {
      method: 'POST',
      body: JSON.stringify({
        key: form.get('key'),
        label: form.get('label'),
        category: form.get('category'),
        risk_level: form.get('risk_level'),
        description: form.get('description'),
      }),
    });
  });
}

function openGroupCreator() {
  openAccessModal('Group', 'Create Group', `
    <form class="modal-body">
      <div class="modal-copy">Create a group to bundle shared access for users and containers.</div>
      <div class="modal-grid">
        <input name="group_name" placeholder="platform-ops" required>
        <input name="title" placeholder="Platform Operations" required>
        <input name="priority" type="number" value="100" min="1">
        <input name="scope" value="containers" placeholder="containers">
      </div>
      <textarea name="description" rows="4" placeholder="Describe what this group unlocks"></textarea>
      <div class="modal-actions"><button class="btn-primary" type="submit">Save group</button></div>
    </form>
  `, async (form) => {
    await acJson('/api/security/groups', {
      method: 'POST',
      body: JSON.stringify({
        group_name: form.get('group_name'),
        title: form.get('title'),
        priority: Number(form.get('priority') || 100),
        scope: form.get('scope'),
        description: form.get('description'),
      }),
    });
  });
}

function openRolePermissionsEditor(roleName, roles, permissions) {
  const role = (roles || []).find((item) => item.name === roleName) || {};
  const assigned = new Set(role.permissions || []);
  openAccessModal('Role', `Edit ${roleName}`, `
    <form class="modal-body">
      <div class="modal-copy">Choose which permissions belong to this role.</div>
      <div class="stack-list">
        ${(permissions || []).map((perm) => `
          <label class="permission-card">
            <div class="role-card-header">
              <div class="stack-tight">
                <strong>${acEscape(perm.label)}</strong>
                <div class="muted mono">${acEscape(perm.key)}</div>
              </div>
              <input type="checkbox" name="permissions" value="${acEscape(perm.key)}" ${assigned.has(perm.key) ? 'checked' : ''}>
            </div>
          </label>
        `).join('')}
      </div>
      <div class="modal-actions"><button class="btn-primary" type="submit">Save</button></div>
    </form>
  `, async (form) => {
    await acJson(`/api/security/roles/${encodeURIComponent(roleName)}/permissions`, {
      method: 'POST',
      body: JSON.stringify({ permissions: form.getAll('permissions') }),
    });
  });
}

function openGroupEditor(groupName) {
  const group = (accessOverview?.groups || []).find((item) => item.group_name === groupName);
  if (!group) return;
  openAccessModal('Group', `Manage ${group.title || group.group_name}`, `
    <form class="modal-body">
      <div class="modal-copy">Update members and container access. Use one entry per line.</div>
      <div class="muted">Members: <span class="mono">username@db_name</span></div>
      <textarea name="members" rows="6">${(group.members || []).map((item) => `${item.username}@${item.db_name}`).join('\n')}</textarea>
      <div class="muted">Containers: <span class="mono">container_id|role_tag</span></div>
      <textarea name="containers" rows="6">${(group.container_access || []).map((item) => `${item.container_id}|${item.role_tag}`).join('\n')}</textarea>
      <div class="modal-actions"><button class="btn-primary" type="submit">Save</button></div>
    </form>
  `, async (form) => {
    const members = String(form.get('members') || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [username, dbName] = line.split('@');
        return {
          username: String(username || '').trim(),
          db_name: String(dbName || accessDbName()).trim() || accessDbName(),
        };
      });
    const containers = String(form.get('containers') || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [containerId, roleTag] = line.split('|');
        return {
          container_id: String(containerId || '').trim(),
          role_tag: String(roleTag || 'user').trim() || 'user',
        };
      });
    await acJson(`/api/security/groups/${encodeURIComponent(groupName)}/members`, {
      method: 'POST',
      body: JSON.stringify({ members }),
    });
    await acJson(`/api/security/groups/${encodeURIComponent(groupName)}/containers`, {
      method: 'POST',
      body: JSON.stringify({ container_access: containers }),
    });
  });
}

async function openUserHistory(username) {
  const data = await acJson(`/api/security/users/${encodeURIComponent(username)}/history?db_name=${encodeURIComponent(accessDbName())}`);
  openAccessModal('User', `${username} history`, `
    <div class="history-shell">
      <div class="history-hero">
        <div class="history-user">${acEscape(username)}</div>
        <div class="history-caption">Recent security context and access activity for this user.</div>
      </div>
      <div class="stack-list history-stack">
        <div class="history-item">
          <div class="history-heading">IP history</div>
          <div class="pill-row">
            ${(data.ip_history || []).map((item) => `<span class="risk-pill ${riskClass(item.risk_level)}">${acEscape(item.ip_address)} • ${acEscape(item.risk_level)}</span>`).join('') || '<span class="muted">No IP history recorded.</span>'}
          </div>
        </div>
        <div class="history-item">
          <div class="history-heading">Groups</div>
          <div class="pill-row">
            ${(data.groups || []).map((item) => `<span class="perm-pill">${acEscape(item.group_name)}</span>`).join('') || '<span class="muted">No group memberships.</span>'}
          </div>
        </div>
        <div class="history-item">
          <div class="history-heading">Recent activity</div>
          <div class="stack-list">
            ${(data.audit_events || []).slice(0, 8).map((item) => `
              <div class="permission-card history-event-card">
                <div class="role-card-header">
                  <div class="stack-tight">
                    <strong>${acEscape(item.summary || item.action)}</strong>
                    <div class="muted">${acEscape(formatAgo(item.created_at))}</div>
                  </div>
                  <span class="risk-pill ${riskClass(item.risk_level)}">${acEscape(item.risk_level)}</span>
                </div>
              </div>
            `).join('') || '<div class="muted">No audit events.</div>'}
          </div>
        </div>
      </div>
    </div>
  `);
}

async function initAccessControl() {
  const dbSelector = document.getElementById('access_db_selector');
  const dbs = await acJson('/api/users/databases');
  accessDatabases = ['system.db', ...(dbs.databases || [])];
  dbSelector.innerHTML = accessDatabases.map((item) => `<option value="${acEscape(item)}">${acEscape(item.toUpperCase())}</option>`).join('');
  if (accessDatabases.length) {
    dbSelector.value = accessDatabases[0];
  }
  dbSelector.addEventListener('change', loadAccessOverview);
  document.getElementById('access_refresh_btn')?.addEventListener('click', loadAccessOverview);
  document.getElementById('create_role_btn')?.addEventListener('click', openRoleCreator);
  document.getElementById('create_permission_btn')?.addEventListener('click', openPermissionCreator);
  document.getElementById('create_group_btn')?.addEventListener('click', openGroupCreator);
  document.getElementById('access_user_search')?.addEventListener('input', () => renderUsers(accessOverview?.users || []));
  document.getElementById('access_modal_close')?.addEventListener('click', closeAccessModal);
  document.getElementById('access_modal')?.addEventListener('click', (event) => {
    if (event.target.id === 'access_modal') closeAccessModal();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeAccessModal();
    }
  });
  await loadAccessOverview();
}

document.addEventListener('DOMContentLoaded', () => {
  initAccessControl().catch((error) => acToast(error.message));
});
