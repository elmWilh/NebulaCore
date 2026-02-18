// nebula_gui_flask/static/js/pages/logs.js
// Copyright (c) 2026 Monolink Systems
// Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

  const socket = io();
  const container = document.getElementById('log-container');
  const autoscroll = document.getElementById('autoscroll');
  const statePill = document.getElementById('logs-state-pill');

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

  socket.on('log_update', handleLogEvent);
  socket.on('log_history', handleLogEvent);

  function clearLogs() {
    container.innerHTML = '<div class="logs-placeholder">Console cleared</div>';
  }

  // Initial history load
  fetch('/api/logs/history').then(r => r.json()).then(logs => {
    if (logs.length > 0) {
      container.innerHTML = '';
      logs.forEach(addLog);
      container.scrollTop = container.scrollHeight;
    }
  }).catch(() => {
    if (statePill) statePill.textContent = 'history unavailable';
  });
