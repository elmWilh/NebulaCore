// static/js/main.js — WORK WIDTH api_metrics
function updateMetrics() {
    // Стучимся во Flask (он у тебя на /api/metrics)
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

            // Используем ключи, которые твой Flask собирает в return jsonify({...})
            document.getElementById('cpu').textContent = data.cpu || "—";
            document.getElementById('ram').textContent = data.ram || "—";
            document.getElementById('disk').textContent = data.disk || "—";
            document.getElementById('network').textContent = data.network || "—";
            
            // Эти поля во Flask сейчас статические (27, 12, 2, 9), они тоже отобразятся
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
    // Ищем все значения и ставим прочерк при ошибке
    document.querySelectorAll('.card-value').forEach(el => el.textContent = "—");
    const cpuEl = document.getElementById('cpu');
    if (cpuEl) cpuEl.textContent = "Core offline";
}

document.addEventListener('DOMContentLoaded', updateMetrics);
setInterval(updateMetrics, 3000);