let auditUsers = [];
let auditConnections = [];

function auditEscape(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function auditRiskClass(level) {
  return `risk-${String(level || 'low').toLowerCase()}`;
}

function auditTime(ts) {
  const value = Number(ts || 0) * 1000;
  return value ? new Date(value).toLocaleString() : 'unknown time';
}

async function auditJson(url) {
  const res = await fetch(url, { credentials: 'same-origin' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || 'Audit request failed');
  }
  return data;
}

function renderUserAudit() {
  const host = document.getElementById('audit_user_list');
  const query = String(document.getElementById('audit_user_filter')?.value || '').trim().toLowerCase();
  const risk = String(document.getElementById('audit_user_risk')?.value || '').trim();
  const rows = auditUsers.filter((item) => {
    const userMatch = !query || String(item.username || item.actor || '').toLowerCase().includes(query);
    const riskMatch = !risk || String(item.risk_level || '') === risk;
    return userMatch && riskMatch;
  });
  host.innerHTML = rows.map((item) => `
    <div class="audit-item">
      <div class="audit-item-head">
        <div>
          <div class="audit-item-title">${auditEscape(item.summary || item.action || 'User event')}</div>
          <div class="audit-item-meta">${auditEscape(item.username || item.actor || 'system')} • ${auditEscape(auditTime(item.created_at))}</div>
        </div>
        <span class="audit-tag ${auditRiskClass(item.risk_level)}">${auditEscape(item.risk_level || 'low')}</span>
      </div>
      <div class="audit-tags">
        <span class="audit-tag">${auditEscape(item.action || 'event')}</span>
        ${item.source_ip ? `<span class="audit-tag">${auditEscape(item.source_ip)}</span>` : ''}
        ${item.db_name ? `<span class="audit-tag">${auditEscape(item.db_name)}</span>` : ''}
        ${item.target_type ? `<span class="audit-tag">${auditEscape(item.target_type)}</span>` : ''}
      </div>
    </div>
  `).join('') || '<div class="audit-item"><div class="audit-item-title">No user audit entries match the filters.</div></div>';
}

function renderConnectionAudit() {
  const host = document.getElementById('audit_connection_list');
  const query = String(document.getElementById('audit_connection_filter')?.value || '').trim().toLowerCase();
  const risk = String(document.getElementById('audit_connection_risk')?.value || '').trim();
  const service = String(document.getElementById('audit_connection_service')?.value || '').trim();
  const rows = auditConnections.filter((item) => {
    const userMatch = !query || String(item.username || '').toLowerCase().includes(query);
    const riskMatch = !risk || String(item.risk_level || '') === risk;
    const serviceMatch = !service || String(item.service_name || '') === service;
    return userMatch && riskMatch && serviceMatch;
  });
  host.innerHTML = rows.map((item) => `
    <div class="audit-item">
      <div class="audit-item-head">
        <div>
          <div class="audit-item-title">${auditEscape(item.service_name || 'service')} • ${auditEscape(item.request_path || item.target_label || '')}</div>
          <div class="audit-item-meta">${auditEscape(item.username || 'anonymous')} • ${auditEscape(item.source_ip || 'unknown ip')} • ${auditEscape(auditTime(item.created_at))}</div>
        </div>
        <span class="audit-tag ${auditRiskClass(item.risk_level)}">${auditEscape(item.risk_level || 'low')}</span>
      </div>
      <div class="audit-tags">
        <span class="audit-tag">${auditEscape(item.http_method || 'GET')} ${auditEscape(item.status_code || 0)}</span>
        <span class="audit-tag">${auditEscape(item.ip_classification || 'system')}</span>
        ${item.suspicion_reason ? `<span class="audit-tag">${auditEscape(item.suspicion_reason)}</span>` : ''}
        ${item.packet_summary ? `<span class="audit-tag">${auditEscape(item.packet_summary)}</span>` : ''}
      </div>
    </div>
  `).join('') || '<div class="audit-item"><div class="audit-item-title">No connection entries match the filters.</div></div>';
}

async function loadAudit() {
  auditUsers = await auditJson('/api/security/audit/users?limit=150');
  auditConnections = await auditJson('/api/security/audit/connections?limit=150');
  renderUserAudit();
  renderConnectionAudit();
}

function switchAuditTab(tabName) {
  document.querySelectorAll('.audit-tab').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.auditTab === tabName);
  });
  document.querySelectorAll('.audit-panel').forEach((panel) => {
    panel.classList.toggle('is-active', panel.id === `audit_panel_${tabName}`);
  });
}

function exportAudit(kind) {
  window.location.href = `/api/security/audit/export?kind=${encodeURIComponent(kind)}&limit=5000`;
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.audit-tab').forEach((button) => {
    button.addEventListener('click', () => switchAuditTab(button.dataset.auditTab));
  });
  document.getElementById('audit_refresh_btn')?.addEventListener('click', () => loadAudit().catch((error) => window.alert(error.message)));
  document.getElementById('audit_export_users_btn')?.addEventListener('click', () => exportAudit('users'));
  document.getElementById('audit_export_connections_btn')?.addEventListener('click', () => exportAudit('connections'));
  ['audit_user_filter', 'audit_user_risk'].forEach((id) => {
    document.getElementById(id)?.addEventListener('input', renderUserAudit);
    document.getElementById(id)?.addEventListener('change', renderUserAudit);
  });
  ['audit_connection_filter', 'audit_connection_risk', 'audit_connection_service'].forEach((id) => {
    document.getElementById(id)?.addEventListener('input', renderConnectionAudit);
    document.getElementById(id)?.addEventListener('change', renderConnectionAudit);
  });
  loadAudit().catch((error) => window.alert(error.message));
});
