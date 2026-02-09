// static/js/main.js — WORK WIDTH api_metrics
function updateMetrics() {
  fetch('/api/metrics')
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        document.body.style.opacity = "0.6";
        document.querySelectorAll('.card-value').forEach(el => el.textContent = "—");
        document.getElementById('cpu').textContent = "Core offline";
        return;
      }

      document.body.style.opacity = "1";

      document.getElementById('cpu').textContent = data.cpu || "—";
      document.getElementById('ram').textContent = data.ram || "—";
      document.getElementById('disk').textContent = data.disk || "—";
      document.getElementById('network').textContent = data.network || "—";
      document.getElementById('containers').textContent = data.containers || "0";
      document.getElementById('servers').textContent = data.servers || "0";
      document.getElementById('alerts').textContent = data.alerts || "0";
      document.getElementById('tasks').textContent = data.tasks || "0";
    })
    .catch(err => {
      console.error("Метрики не загрузились:", err);
      document.body.style.opacity = "0.6";
    });
}

// STARTUP
document.addEventListener('DOMContentLoaded', updateMetrics);
setInterval(updateMetrics, 3000);