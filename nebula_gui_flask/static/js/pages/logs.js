// nebula_gui_flask/static/js/pages/logs.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

  const socket = (typeof window.io === 'function') ? window.io() : null;
  const container = document.getElementById('log-container');
  const autoscroll = document.getElementById('autoscroll');
  const statePill = document.getElementById('logs-state-pill');
  let historyPollTimer = null;

  function addLog(entry) {
    if (container.innerText === 'Waiting for logs...') {
      container.innerHTML = '';
    }

    const div = document.createElement('div');
    const level = String(entry.level || 'INFO').toUpperCase();
    div.className = `log-row level-${level.toLowerCase()}`;

    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = `[${entry.iso || '-'}]`;

    const lvl = document.createElement('span');
    lvl.className = 'level';
    lvl.textContent = level;

    const message = document.createElement('span');
    message.textContent = String(entry.message || '');

    div.appendChild(time);
    div.appendChild(lvl);
    div.appendChild(message);
    container.appendChild(div);
    
    if (autoscroll.checked) {
      container.scrollTop = container.scrollHeight;
    }
    if (statePill) {
      statePill.textContent = `updated ${new Date().toLocaleTimeString()}`;
    }
  }

  const handleLogEvent = (data) => {
    if (Array.isArray(data)) {
      container.innerHTML = '';
      data.forEach(addLog);
      return;
    }
    if (data && data.type === 'history') {
      container.innerHTML = '';
      (Array.isArray(data.data) ? data.data : []).forEach(addLog);
      return;
    }
    if (data && typeof data === 'object') {
      addLog(data);
    }
  };

  if (socket) {
    socket.on('connect', () => {
      if (statePill) statePill.textContent = 'live connected';
    });
    socket.on('disconnect', () => {
      if (statePill) statePill.textContent = 'live disconnected';
    });
    socket.on('log_update', handleLogEvent);
    socket.on('log_history', handleLogEvent);
  } else if (statePill) {
    statePill.textContent = 'socket unavailable, using history polling';
  }

  function clearLogs() {
    container.innerHTML = '<div class="logs-placeholder">Console cleared</div>';
  }

  function loadHistory() {
    return fetch('/api/logs/history', { credentials: 'same-origin' })
      .then(r => r.json().catch(() => ({})))
      .then(logs => {
        if (!Array.isArray(logs)) {
          return;
        }
        if (logs.length > 0) {
          container.innerHTML = '';
          logs.forEach(addLog);
          container.scrollTop = container.scrollHeight;
        }
      });
  }

  // Initial history load
  loadHistory().then(() => {
    if (!socket) {
      historyPollTimer = setInterval(() => {
        loadHistory().catch(() => {
          if (statePill) statePill.textContent = 'history unavailable';
        });
      }, 3000);
    }
  }).catch(() => {
    if (statePill) statePill.textContent = 'history unavailable';
  });

  window.addEventListener('beforeunload', () => {
    if (historyPollTimer) {
      clearInterval(historyPollTimer);
      historyPollTimer = null;
    }
  });
