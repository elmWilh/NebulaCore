// nebula_gui_flask/static/js/pages/dashboard.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

  let ramChart = null;
  let networkChart = null;
  let containersChart = null;
  let containerDiskChart = null;
  let adminMetricsAbortController = null;
  let adminMetricsFailures = 0;

  function formatTimePoint(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function safeNumber(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }

  function makeOrUpdateChart(target, config, existing) {
    if (!existing) {
      return new Chart(target, config);
    }
    existing.data = config.data;
    existing.options = config.options;
    existing.update('none');
    return existing;
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

  async function updateAdminDashboardMetrics() {
    try {
      if (adminMetricsAbortController) adminMetricsAbortController.abort();
      adminMetricsAbortController = new AbortController();
      const r = await fetch('/api/admin/dashboard-metrics', { signal: adminMetricsAbortController.signal, cache: 'no-store' });
      if (!r.ok) throw new Error('failed');
      const data = await r.json();
      adminMetricsFailures = 0;

      const ram = data.ram || {};
      const network = data.network || {};
      const containers = Array.isArray(data.containers_memory) ? data.containers_memory : [];

      const ramHistory = Array.isArray(ram.history) ? ram.history : [];
      const labels = ramHistory.map((p) => formatTimePoint(p.t));
      const ramSeries = ramHistory.map((p) => safeNumber(p.v));
      const txSeries = (Array.isArray(network.history_tx) ? network.history_tx : []).map((p) => safeNumber(p.v));
      const rxSeries = (Array.isArray(network.history_rx) ? network.history_rx : []).map((p) => safeNumber(p.v));

      ramChart = makeOrUpdateChart(document.getElementById('ramTimelineChart'), {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'RAM %',
            data: ramSeries,
            borderColor: '#6f7dff',
            backgroundColor: 'rgba(111, 125, 255, 0.2)',
            fill: true,
            tension: 0.28,
            pointRadius: 0,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            y: { min: 0, max: 100, ticks: { color: '#969fbf' }, grid: { color: 'rgba(255,255,255,0.08)' } },
            x: { ticks: { color: '#969fbf', maxRotation: 0 }, grid: { color: 'rgba(255,255,255,0.04)' } }
          }
        }
      }, ramChart);

      networkChart = makeOrUpdateChart(document.getElementById('networkTimelineChart'), {
        type: 'line',
        data: {
          labels,
          datasets: [
            {
              label: 'TX MB/s',
              data: txSeries,
              borderColor: '#8f9bff',
              backgroundColor: 'rgba(143, 155, 255, 0.13)',
              fill: true,
              tension: 0.25,
              pointRadius: 0,
            },
            {
              label: 'RX MB/s',
              data: rxSeries,
              borderColor: '#fbbf24',
              backgroundColor: 'rgba(251, 191, 36, 0.13)',
              fill: true,
              tension: 0.25,
              pointRadius: 0,
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {
              labels: { color: '#bdc5e6', boxWidth: 10, boxHeight: 10 }
            }
          },
          scales: {
            y: { ticks: { color: '#969fbf' }, grid: { color: 'rgba(255,255,255,0.08)' } },
            x: { ticks: { color: '#969fbf', maxRotation: 0 }, grid: { color: 'rgba(255,255,255,0.04)' } }
          }
        }
      }, networkChart);

      const topContainers = containers.slice(0, 8);
      const donutLabels = topContainers.map((c) => c.name);
      const donutData = topContainers.map((c) => safeNumber(c.memory_used_mb));
      const diskDonutData = topContainers.map((c) => safeNumber(c.disk_used_mb ?? c.disk_rw_mb));
      const otherMemory = containers.slice(8).reduce((s, c) => s + safeNumber(c.memory_used_mb), 0);
      const otherDisk = containers.slice(8).reduce((s, c) => s + safeNumber(c.disk_used_mb ?? c.disk_rw_mb), 0);
      if (otherMemory > 0.1 || otherDisk > 0.1) {
        donutLabels.push('others');
        donutData.push(otherMemory);
        diskDonutData.push(otherDisk);
      }

      containersChart = makeOrUpdateChart(document.getElementById('containerMemoryChart'), {
        type: 'doughnut',
        data: {
          labels: donutLabels,
          datasets: [{
            data: donutData,
            borderWidth: 1,
            borderColor: 'rgba(8, 11, 18, 0.9)',
            backgroundColor: ['#5865ff', '#7b87ff', '#99a2ff', '#4b8fff', '#6fa7ff', '#7f8cff', '#a1a9ff', '#6f7aff', '#8d97f0']
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
      }, containersChart);

      containerDiskChart = makeOrUpdateChart(document.getElementById('containerDiskChart'), {
        type: 'doughnut',
        data: {
          labels: donutLabels,
          datasets: [{
            data: diskDonutData,
            borderWidth: 1,
            borderColor: 'rgba(8, 11, 18, 0.9)',
            backgroundColor: ['#f59e0b', '#fb923c', '#fbbf24', '#fca5a5', '#fdba74', '#f97316', '#fcd34d', '#fb7185', '#f59e8b']
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
      }, containerDiskChart);

      const ramPercent = safeNumber(ram.percent);
      const ramUsed = safeNumber(ram.used_gb);
      const ramTotal = safeNumber(ram.total_gb);
      document.getElementById('ram-quick').textContent = `${ramUsed.toFixed(1)} / ${ramTotal.toFixed(1)} GB (${ramPercent.toFixed(1)}%)`;

      const tx = safeNumber(network.tx_mbps);
      const rx = safeNumber(network.rx_mbps);
      document.getElementById('network-quick').textContent = `↑ ${tx.toFixed(2)} MB/s  ↓ ${rx.toFixed(2)} MB/s`;

      const totalContainerMb = containers.reduce((s, c) => s + safeNumber(c.memory_used_mb), 0);
      document.getElementById('containers-mem-total').textContent = `${(totalContainerMb / 1024).toFixed(2)} GB`;
      const totalContainerDiskMb = containers.reduce((s, c) => s + safeNumber(c.disk_used_mb ?? c.disk_rw_mb), 0);
      document.getElementById('containers-disk-total').textContent = `${(totalContainerDiskMb / 1024).toFixed(2)} GB`;

      renderDisks(Array.isArray(data.disks) ? data.disks : []);

      const upd = document.getElementById('admin-metrics-updated');
      if (upd) {
        upd.textContent = `updated ${new Date().toLocaleTimeString()}`;
      }
    } catch (_) {
      adminMetricsFailures += 1;
      const upd = document.getElementById('admin-metrics-updated');
      if (!upd) return;
      if (adminMetricsFailures >= 3) {
        upd.textContent = 'telemetry unavailable';
      } else {
        upd.textContent = 'sync delayed';
      }
    }
  }

  function adminMetricsIntervalMs() {
    return document.hidden ? 12000 : 3000;
  }

  function scheduleAdminMetrics() {
    if (window.__nebulaAdminMetricsTimer) {
      clearInterval(window.__nebulaAdminMetricsTimer);
    }
    window.__nebulaAdminMetricsTimer = setInterval(updateAdminDashboardMetrics, adminMetricsIntervalMs());
  }

  document.addEventListener('DOMContentLoaded', () => {
    updateAdminDashboardMetrics();
    scheduleAdminMetrics();
    document.addEventListener('visibilitychange', () => {
      scheduleAdminMetrics();
      if (!document.hidden) updateAdminDashboardMetrics();
    });
  });
