// nebula_gui_flask/static/js/pages/dashboard.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

let ramChart = null;
let networkChart = null;
let containersChart = null;
let containerDiskChart = null;
let dashboardAbortController = null;
let dashboardFailures = 0;
let lastContainersSyncAt = 0;
let lastDisksSyncAt = 0;
let dashboardFallbackTimer = null;
let dashboardLiveWatchdogTimer = null;
let pinnedFocusTimer = null;
let hasTelemetryData = false;
let hasContainersData = false;
let hasDisksData = false;
let hasReceivedDashboardPayload = false;
let lastDashboardCounts = { containers: null, activeContainers: null };
let latestDashboardPayload = null;
const dashboardSocket = (typeof window.io === 'function') ? window.io() : null;
const dashboardContainerFocusKey = 'nebula-pinned-containers-v1';
const dashboardUserFocusKey = 'nebula-pinned-users-v1';
window.__nebulaDashboardManaged = true;

function formatTimePoint(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function safeNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function safeCapacityGb(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return 0;
  return n > 1_000_000 ? 0 : n;
}

function fastIntervalMs() {
  return document.hidden ? 12000 : 3000;
}

function containersIntervalMs() {
  return document.hidden ? 30000 : 12000;
}

function disksIntervalMs() {
  return document.hidden ? 60000 : 45000;
}

function pinnedFocusIntervalMs() {
  return document.hidden ? 30000 : 15000;
}

function setCardValue(id, value, fallback = '—') {
  const el = document.getElementById(id);
  if (el) el.textContent = value !== undefined && value !== null ? value : fallback;
}

function buildDashboardQuery(includeContainers, includeDisks) {
  return `include_containers=${includeContainers ? '1' : '0'}&include_disks=${includeDisks ? '1' : '0'}`;
}

function safeCount(value, fallback = '0') {
  return value !== undefined && value !== null ? String(value) : fallback;
}

function diskUsageValue(container) {
  if (!container || typeof container !== 'object') return 0;
  if (container.disk_used_mb !== undefined && container.disk_used_mb !== null) {
    return safeNumber(container.disk_used_mb);
  }
  return safeNumber(container.disk_rw_mb);
}

function readStoredPins(storageKey) {
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function ensureLineChart(canvasId, label, color, fillColor, showLegend = false, secondDataset = null) {
  if (typeof Chart === 'undefined') return null;
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  return new Chart(canvas, {
    type: 'line',
    data: {
      labels: [],
      datasets: secondDataset ? [
        {
          label,
          data: [],
          borderColor: color,
          backgroundColor: fillColor,
          fill: true,
          tension: 0.25,
          pointRadius: 0,
        },
        {
          label: secondDataset.label,
          data: [],
          borderColor: secondDataset.color,
          backgroundColor: secondDataset.fillColor,
          fill: true,
          tension: 0.25,
          pointRadius: 0,
        }
      ] : [{
        label,
        data: [],
        borderColor: color,
        backgroundColor: fillColor,
        fill: true,
        tension: 0.28,
        pointRadius: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: showLegend ? {
          labels: { color: '#bdc5e6', boxWidth: 10, boxHeight: 10 }
        } : { display: false }
      },
      scales: {
        y: { min: 0, ticks: { color: '#969fbf' }, grid: { color: 'rgba(255,255,255,0.08)' } },
        x: { ticks: { color: '#969fbf', maxRotation: 0 }, grid: { color: 'rgba(255,255,255,0.04)' } }
      }
    }
  });
}

function ensureDoughnutChart(canvasId, palette) {
  if (typeof Chart === 'undefined') return null;
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  return new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: [],
      datasets: [{
        data: [],
        borderWidth: 1,
        borderColor: 'rgba(8, 11, 18, 0.9)',
        backgroundColor: palette,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '58%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#bdc5e6', padding: 12, boxWidth: 10, boxHeight: 10 }
        }
      }
    }
  });
}

function initCharts() {
  if (!ramChart) {
    ramChart = ensureLineChart('ramTimelineChart', 'RAM %', '#6f7dff', 'rgba(111, 125, 255, 0.2)');
  }
  if (!networkChart) {
    networkChart = ensureLineChart(
      'networkTimelineChart',
      'TX MB/s',
      '#8f9bff',
      'rgba(143, 155, 255, 0.13)',
      true,
      { label: 'RX MB/s', color: '#fbbf24', fillColor: 'rgba(251, 191, 36, 0.13)' }
    );
  }
  if (!containersChart) {
    containersChart = ensureDoughnutChart('containerMemoryChart', ['#5865ff', '#7b87ff', '#99a2ff', '#4b8fff', '#6fa7ff', '#7f8cff', '#a1a9ff', '#6f7aff', '#8d97f0']);
  }
  if (!containerDiskChart) {
    containerDiskChart = ensureDoughnutChart('containerDiskChart', ['#f59e0b', '#fb923c', '#fbbf24', '#fca5a5', '#fdba74', '#f97316', '#fcd34d', '#fb7185', '#f59e8b']);
  }
}

function updateLineChart(chart, labels, seriesList) {
  if (!chart) return;
  chart.data.labels = labels;
  seriesList.forEach((series, index) => {
    if (chart.data.datasets[index]) {
      chart.data.datasets[index].data = series;
    }
  });
  chart.update('none');
}

function updateDoughnutChart(chart, labels, values) {
  if (!chart) return;
  chart.data.labels = labels;
  if (chart.data.datasets[0]) {
    chart.data.datasets[0].data = values;
  }
  chart.update('none');
}

function renderDisks(disks) {
  const wrap = document.getElementById('disks-list');
  const countEl = document.getElementById('disk-count');
  if (!wrap || !countEl) return;
  if (!Array.isArray(disks) || disks.length === 0) {
    wrap.innerHTML = '<div class="disk-row disk-empty">No disk data</div>';
    countEl.textContent = '0';
    return;
  }
  countEl.textContent = String(disks.length);
  wrap.innerHTML = disks.map((d) => {
    const pct = Math.max(0, Math.min(100, safeNumber(d.percent)));
    return `
      <div class="disk-row">
        <div class="disk-main">
          <span class="mount">${d.mountpoint}</span>
          <span>${pct.toFixed(1)}%</span>
        </div>
        <div class="disk-sub">
          <span>${d.device} • ${d.fstype}</span>
          <span>${safeNumber(d.used_gb).toFixed(1)} / ${safeNumber(d.total_gb).toFixed(1)} GB</span>
        </div>
        <div class="disk-bar"><div class="disk-fill" style="width:${pct}%"></div></div>
      </div>
    `;
  }).join('');
}

function updateOverviewCards(overview) {
  if (!overview || typeof overview !== 'object') return;
  setCardValue('cpu', overview.cpu || '—');
  setCardValue('ram', overview.ram || '—');
  setCardValue('disk', overview.disk || '—');
  const nextTotal = overview.containers !== undefined && overview.containers !== null ? Number(overview.containers) : null;
  const nextActive = overview.active_containers !== undefined && overview.active_containers !== null ? Number(overview.active_containers) : null;
  if (Number.isFinite(nextTotal) && Number.isFinite(nextActive)) {
    lastDashboardCounts = { containers: nextTotal, activeContainers: nextActive };
    setCardValue('containers', `${nextActive} / ${nextTotal}`);
  } else if (lastDashboardCounts.containers !== null && lastDashboardCounts.activeContainers !== null) {
    setCardValue('containers', `${lastDashboardCounts.activeContainers} / ${lastDashboardCounts.containers}`);
  } else {
    setCardValue('containers', 'syncing...');
  }
  setCardValue('servers', safeCount(overview.servers));
  setCardValue('alerts', safeCount(overview.alerts));
  setCardValue('tasks', safeCount(overview.tasks));
}

function updateTelemetry(data) {
  if (!data || typeof data !== 'object') return;
  const ram = data.ram || {};
  const network = data.network || {};
  const ramHistory = Array.isArray(ram.history) ? ram.history : [];
  const labels = ramHistory.map((point) => formatTimePoint(point.t));
  const ramSeries = ramHistory.map((point) => safeNumber(point.v));
  const txSeries = (Array.isArray(network.history_tx) ? network.history_tx : []).map((point) => safeNumber(point.v));
  const rxSeries = (Array.isArray(network.history_rx) ? network.history_rx : []).map((point) => safeNumber(point.v));

  initCharts();
  updateLineChart(ramChart, labels, [ramSeries]);
  updateLineChart(networkChart, labels, [txSeries, rxSeries]);

  setCardValue('ram-quick', `${safeNumber(ram.used_gb).toFixed(1)} / ${safeNumber(ram.total_gb).toFixed(1)} GB (${safeNumber(ram.percent).toFixed(1)}%)`);
  setCardValue('network-quick', `↑ ${safeNumber(network.tx_mbps).toFixed(2)} MB/s  ↓ ${safeNumber(network.rx_mbps).toFixed(2)} MB/s`);
}

function updateContainersBreakdown(containers) {
  if (!Array.isArray(containers)) return;
  initCharts();
  const topContainers = containers.slice(0, 8);
  const labels = topContainers.map((container) => container.name);
  const memoryValues = topContainers.map((container) => safeNumber(container.memory_used_mb));
  const diskValues = topContainers.map((container) => diskUsageValue(container));
  const otherMemory = containers.slice(8).reduce((sum, container) => sum + safeNumber(container.memory_used_mb), 0);
  const otherDisk = containers.slice(8).reduce((sum, container) => sum + diskUsageValue(container), 0);
  if (otherMemory > 0.1 || otherDisk > 0.1) {
    labels.push('others');
    memoryValues.push(otherMemory);
    diskValues.push(otherDisk);
  }

  updateDoughnutChart(containersChart, labels, memoryValues);
  updateDoughnutChart(containerDiskChart, labels, diskValues);

  const totalContainerMb = containers.reduce((sum, container) => sum + safeNumber(container.memory_used_mb), 0);
  const totalContainerDiskMb = containers.reduce((sum, container) => sum + diskUsageValue(container), 0);
  setCardValue('containers-mem-total', `${(totalContainerMb / 1024).toFixed(2)} GB`);
  setCardValue('containers-disk-total', `${(totalContainerDiskMb / 1024).toFixed(2)} GB`);
}

function setDashboardStatus(text) {
  const upd = document.getElementById('admin-metrics-updated');
  if (upd) upd.textContent = text;
}

function setChartLoading(boxId, isLoading) {
  const box = document.getElementById(boxId);
  if (!box) return;
  box.classList.toggle('is-loading', Boolean(isLoading));
}

function refreshChartLoadingState() {
  setChartLoading('ram-chart-box', !hasTelemetryData);
  setChartLoading('network-chart-box', !hasTelemetryData);
  setChartLoading('container-memory-box', !hasContainersData);
  setChartLoading('container-disk-box', !hasContainersData);
}

function payloadHasMeaningfulDashboardData(data) {
  if (!data || typeof data !== 'object') return false;
  const overview = (data.overview && typeof data.overview === 'object') ? data.overview : {};
  const hasUserOverview = (
    String(data.scope || overview.scope || '') === 'user_containers'
    && (
      overview.containers !== undefined && overview.containers !== null
      || overview.active_containers !== undefined && overview.active_containers !== null
      || (typeof overview.cpu === 'string' && overview.cpu !== '—')
      || (typeof overview.ram === 'string' && overview.ram !== '—')
    )
  );
  const hasTelemetry = Boolean(data.ram && Array.isArray(data.ram.history) && data.network);
  const hasContainersBreakdown = Boolean(data.included && data.included.containers && Array.isArray(data.containers_memory));
  const hasDisksBreakdown = Boolean(data.included && data.included.disks && Array.isArray(data.disks));
  return hasTelemetry || hasContainersBreakdown || hasDisksBreakdown || hasUserOverview;
}

function setBriefValue(id, value, subValue) {
  setCardValue(id, value);
  setCardValue(`${id}-sub`, subValue);
}

function renderOperationsBrief(overview, payload) {
  const cpu = safeNumber(overview?.cpu_percent);
  const ram = safeNumber(overview?.ram_percent);
  const disk = safeNumber(overview?.disk_percent);
  const netUp = safeNumber(overview?.network_sent_mb);
  const netDown = safeNumber(overview?.network_recv_mb);
  const health = String(overview?.health_status || 'optimal').toLowerCase();
  const pressure = Math.max(cpu, ram, disk);
  const totalContainers = overview?.containers !== undefined && overview?.containers !== null
    ? safeNumber(overview?.containers)
    : safeNumber(lastDashboardCounts.containers);
  const activeContainers = overview?.active_containers !== undefined && overview?.active_containers !== null
    ? safeNumber(overview?.active_containers)
    : safeNumber(lastDashboardCounts.activeContainers);
  const stoppedContainers = Math.max(0, totalContainers - activeContainers);
  const ramHeadroom = Math.max(0, safeCapacityGb(overview?.ram_total_gb) - safeCapacityGb(overview?.ram_used_gb));
  const diskHeadroom = Math.max(0, safeCapacityGb(overview?.disk_total_gb) - safeCapacityGb(overview?.disk_used_gb));
  const trafficTotal = netUp + netDown;
  const reportStamp = document.getElementById('ops-report-stamp');
  if (reportStamp) {
    reportStamp.textContent = `Updated ${new Date().toLocaleTimeString()} • ${health}`;
  }

  setBriefValue('brief-pressure', `${pressure.toFixed(1)}%`, `${health[0].toUpperCase()}${health.slice(1)} pressure based on CPU/RAM/disk ceiling`);
  const headroomPieces = [];
  if (ramHeadroom > 0) headroomPieces.push(`${ramHeadroom.toFixed(1)} GB RAM free`);
  if (diskHeadroom > 0) headroomPieces.push(`${diskHeadroom.toFixed(1)} GB disk free`);
  setBriefValue('brief-headroom', headroomPieces[0] || 'Unknown', headroomPieces[1] || 'Capacity headroom updates as telemetry arrives');

  if (totalContainers > 0) {
    setBriefValue('brief-availability', `${activeContainers}/${totalContainers}`, stoppedContainers > 0 ? `${stoppedContainers} container(s) need attention` : 'All visible containers are online');
  } else {
    setBriefValue('brief-availability', '0', 'No visible containers in current scope');
  }

  setBriefValue('brief-network', `${trafficTotal.toFixed(2)} MB/s`, `${netUp >= netDown ? 'Outbound' : 'Inbound'} traffic dominant right now`);

  renderPriorityWatchlist(overview, payload);
}

function renderPriorityWatchlist(overview, payload) {
  const host = document.getElementById('ops_watchlist');
  const countEl = document.getElementById('ops-watchlist-count');
  if (!host || !countEl) return;

  const items = [];
  const totalContainers = safeNumber(overview?.containers);
  const activeContainers = safeNumber(overview?.active_containers);
  const stoppedContainers = Math.max(0, totalContainers - activeContainers);
  const cpu = safeNumber(overview?.cpu_percent);
  const ram = safeNumber(overview?.ram_percent);
  const disk = safeNumber(overview?.disk_percent);

  if (disk >= 85) items.push({ level: 'critical', title: 'Disk pressure is high', sub: `${disk.toFixed(1)}% used on the host storage footprint.` });
  if (ram >= 80) items.push({ level: 'elevated', title: 'RAM headroom is shrinking', sub: `${ram.toFixed(1)}% memory utilization may affect new workloads.` });
  if (cpu >= 75) items.push({ level: 'elevated', title: 'CPU contention detected', sub: `${cpu.toFixed(1)}% current processor load across the node.` });
  if (stoppedContainers > 0) items.push({ level: 'warning', title: 'Stopped workloads detected', sub: `${stoppedContainers} visible container(s) are not running.` });

  const hotContainer = Array.isArray(payload?.containers_memory) ? payload.containers_memory[0] : null;
  if (hotContainer) {
    items.push({
      level: 'info',
      title: `Top memory consumer: ${hotContainer.name}`,
      sub: `${safeNumber(hotContainer.memory_used_mb).toFixed(0)} MB RAM, ${safeNumber(hotContainer.disk_used_mb || hotContainer.disk_rw_mb).toFixed(0)} MB disk.`,
    });
  }

  const stressedDisk = Array.isArray(payload?.disks) ? payload.disks.find((diskItem) => safeNumber(diskItem.percent) >= 80) : null;
  if (stressedDisk) {
    items.push({
      level: 'warning',
      title: `Disk mount nearing saturation: ${stressedDisk.mountpoint}`,
      sub: `${safeNumber(stressedDisk.percent).toFixed(1)}% used on ${stressedDisk.device}.`,
    });
  }

  const uniqueItems = items.slice(0, 5);
  countEl.textContent = `${uniqueItems.length} signal${uniqueItems.length === 1 ? '' : 's'}`;
  if (uniqueItems.length === 0) {
    host.innerHTML = '<div class="ops-watch-item ops-watch-empty">No actionable signals yet.</div>';
    return;
  }
  host.innerHTML = uniqueItems.map((item) => `
    <div class="ops-watch-item level-${item.level}">
      <div class="ops-watch-title">${item.title}</div>
      <div class="ops-watch-sub">${item.sub}</div>
    </div>
  `).join('');
}

async function renderPinnedFocus() {
  const host = document.getElementById('pinned_focus');
  const countEl = document.getElementById('pinned-focus-count');
  if (!host || !countEl) return;

  const pinnedContainers = readStoredPins(dashboardContainerFocusKey);
  const pinnedUsers = readStoredPins(dashboardUserFocusKey);
  const items = [];

  if (pinnedContainers.length > 0) {
    try {
      const res = await fetch('/api/containers/list', { cache: 'no-store' });
      const containers = await res.json().catch(() => []);
      const visible = Array.isArray(containers) ? containers : [];
      pinnedContainers.forEach((pinned) => {
        const match = visible.find((cont) => String(cont.id || '').trim() === String(pinned.id || '').trim()
          || String(cont.name || '').trim() === String(pinned.name || '').trim());
        const label = String(match?.name || pinned.name || pinned.id || 'Container');
        const status = String(match?.status || 'missing').toLowerCase();
        items.push({
          type: 'container',
          title: label,
          sub: match ? `${status.toUpperCase()} • ${String(match.image || 'unknown')}` : 'Not visible in current scope',
          href: match ? `/containers/view/${encodeURIComponent(String(match.id || pinned.id || ''))}` : '/containers',
          state: status,
        });
      });
    } catch (_) {
      pinnedContainers.forEach((pinned) => {
        items.push({
          type: 'container',
          title: String(pinned.name || pinned.id || 'Container'),
          sub: 'Pinned container status unavailable',
          href: '/containers',
          state: 'missing',
        });
      });
    }
  }

  pinnedUsers.forEach((pinned) => {
    const username = String(pinned.username || '').trim();
    if (!username) return;
    const dbName = String(pinned.db || pinned.db_name || 'system.db').trim() || 'system.db';
    items.push({
      type: 'user',
      title: username,
      sub: `${String(pinned.role_tag || 'user').toUpperCase()} • ${dbName}`,
      href: `/users/view/${encodeURIComponent(username)}?db_name=${encodeURIComponent(dbName)}`,
      state: 'user',
    });
  });

  countEl.textContent = `${items.length} pinned`;
  if (items.length === 0) {
    host.innerHTML = '<div class="pin-focus-empty">Pin users or containers to keep them visible here.</div>';
    return;
  }

  host.innerHTML = items.slice(0, 8).map((item) => `
    <a class="pin-focus-card state-${item.state}" href="${item.href}">
      <div class="pin-focus-meta">${item.type}</div>
      <div class="pin-focus-title">${item.title}</div>
      <div class="pin-focus-sub">${item.sub}</div>
    </a>
  `).join('');
}

function schedulePinnedFocus() {
  if (pinnedFocusTimer) clearInterval(pinnedFocusTimer);
  renderPinnedFocus();
  pinnedFocusTimer = setInterval(renderPinnedFocus, pinnedFocusIntervalMs());
}

function applyDashboardPayload(data) {
  if (!data || typeof data !== 'object') return;
  latestDashboardPayload = data;
  const hasMeaningfulData = payloadHasMeaningfulDashboardData(data);
  if (hasMeaningfulData) {
    hasReceivedDashboardPayload = true;
    disarmLiveWatchdog();
    stopDashboardLoop();
  }
  dashboardFailures = 0;
  updateOverviewCards(data.overview || data);
  renderOperationsBrief(data.overview || data, data);
  if (data.ram && Array.isArray(data.ram.history) && data.network) {
    updateTelemetry(data);
    hasTelemetryData = data.ram.history.length > 0;
  }

  if (data.included && data.included.containers && Array.isArray(data.containers_memory)) {
    updateContainersBreakdown(data.containers_memory);
    lastContainersSyncAt = Date.now();
    hasContainersData = true;
  }
  if (data.included && data.included.disks) {
    renderDisks(Array.isArray(data.disks) ? data.disks : []);
    lastDisksSyncAt = Date.now();
    hasDisksData = true;
  }

  if (data.loading && typeof data.loading === 'object') {
    if (data.loading.telemetry && !hasTelemetryData) {
      setDashboardStatus('syncing telemetry');
    }
    if (data.loading.counts && lastDashboardCounts.containers === null) {
      setCardValue('containers', 'syncing...');
    }
    if (data.loading.containers && !hasContainersData) {
      setDashboardStatus('syncing container map');
    }
    if (
      (data.loading.telemetry && !hasTelemetryData) ||
      (data.loading.containers && !hasContainersData) ||
      (data.loading.counts && lastDashboardCounts.containers === null)
    ) {
      refreshChartLoadingState();
      return;
    }
  }

  refreshChartLoadingState();
  setDashboardStatus(`updated ${new Date().toLocaleTimeString()}`);
}

async function updateDashboard() {
  const now = Date.now();
  const includeContainers = (now - lastContainersSyncAt) >= containersIntervalMs();
  const includeDisks = (now - lastDisksSyncAt) >= disksIntervalMs();

  try {
    const requestOptions = {
      cache: 'no-store'
    };
    if (typeof AbortController !== 'undefined') {
      if (dashboardAbortController) dashboardAbortController.abort();
      dashboardAbortController = new AbortController();
      requestOptions.signal = dashboardAbortController.signal;
    }
    const response = await fetch(`/api/dashboard/payload?${buildDashboardQuery(includeContainers, includeDisks)}`, requestOptions);
    if (!response.ok) throw new Error('failed');
    const data = await response.json();
    applyDashboardPayload(data);
  } catch (error) {
    if (error && error.name === 'AbortError') {
      return;
    }
    dashboardFailures += 1;
    try {
      const fallbackResponse = await fetch('/api/metrics', { cache: 'no-store' });
      if (fallbackResponse.ok) {
        const fallbackData = await fallbackResponse.json();
        updateOverviewCards(fallbackData);
        renderOperationsBrief(fallbackData, latestDashboardPayload || {});
      }
    } catch (_) {
      // Keep status text below; fallback is best-effort.
    }
    refreshChartLoadingState();
    setDashboardStatus(dashboardFailures >= 3 ? 'telemetry unavailable' : 'sync delayed');
  }
}

function scheduleDashboardLoop() {
  if (dashboardFallbackTimer) {
    clearInterval(dashboardFallbackTimer);
  }
  dashboardFallbackTimer = setInterval(updateDashboard, fastIntervalMs());
}

function stopDashboardLoop() {
  if (dashboardFallbackTimer) {
    clearInterval(dashboardFallbackTimer);
    dashboardFallbackTimer = null;
  }
}

function armLiveWatchdog() {
  if (dashboardLiveWatchdogTimer) {
    clearTimeout(dashboardLiveWatchdogTimer);
  }
  dashboardLiveWatchdogTimer = setTimeout(() => {
    if (!hasReceivedDashboardPayload) {
      setDashboardStatus('live sync delayed');
      updateDashboard();
      scheduleDashboardLoop();
    }
  }, 1800);
}

function disarmLiveWatchdog() {
  if (dashboardLiveWatchdogTimer) {
    clearTimeout(dashboardLiveWatchdogTimer);
    dashboardLiveWatchdogTimer = null;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  refreshChartLoadingState();
  renderOperationsBrief({}, {});
  schedulePinnedFocus();
  updateDashboard();
  if (dashboardSocket) {
    dashboardSocket.on('connect', () => {
      stopDashboardLoop();
      setDashboardStatus('live connected');
      dashboardSocket.emit('subscribe_dashboard', { page: 'dashboard' });
      armLiveWatchdog();
    });
    dashboardSocket.on('disconnect', () => {
      setDashboardStatus('live disconnected');
      scheduleDashboardLoop();
    });
    dashboardSocket.on('dashboard_update', applyDashboardPayload);
    dashboardSocket.on('dashboard_status', (data) => {
      const state = data && data.state ? String(data.state) : 'sync delayed';
      setDashboardStatus(state.replace(/_/g, ' '));
      if (state === 'sync_delayed' && !hasReceivedDashboardPayload) {
        scheduleDashboardLoop();
      }
    });
  } else {
    scheduleDashboardLoop();
  }
  document.addEventListener('visibilitychange', () => {
    schedulePinnedFocus();
    if (!dashboardSocket || !dashboardSocket.connected) {
      scheduleDashboardLoop();
    }
    if (!document.hidden) {
      updateDashboard();
      renderPinnedFocus();
      if (dashboardSocket && dashboardSocket.connected) {
        armLiveWatchdog();
      }
    }
  });
  window.addEventListener('storage', (event) => {
    if (event.key === dashboardContainerFocusKey || event.key === dashboardUserFocusKey) {
      renderPinnedFocus();
    }
  });
});
