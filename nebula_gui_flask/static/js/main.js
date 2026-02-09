// static/js/main.js — WORK WIDTH api_metrics
function updateMetrics() {
    fetch('/api/metrics')
        .then(r => {
            if (!r.ok) throw new Error();
            return r.json();
        })
        .then(data => {
            if (data.error) {
                setUIOffline();
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
        .catch(() => {
            setUIOffline();
        });
}

function setUIOffline() {
    document.body.style.opacity = "0.6";
    document.querySelectorAll('.card-value').forEach(el => el.textContent = "—");
    const cpuEl = document.getElementById('cpu');
    if (cpuEl) cpuEl.textContent = "Core offline";
}

document.addEventListener('DOMContentLoaded', updateMetrics);
setInterval(updateMetrics, 3000);