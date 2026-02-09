import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
import requests
import threading
import json
import websocket
import socket
import time 

app = Flask(__name__)
app.config['SECRET_KEY'] = 'nebula-secret-2026'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# Auto-detect Core port
def detect_core_url():
    ports = [8000, 8080, 5000]
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                url = f"http://127.0.0.1:{port}"
                print(f"Nebula Core found: {url}")
                return url
        except (OSError, ConnectionRefusedError):
            continue
    fallback = "http://127.0.0.1:8000"
    print(f"Core not found. Using fallback: {fallback}")
    return fallback

CORE_URL = detect_core_url()

# Fetch Metrics — ИСПРАВЛЕН ПУТЬ
def get_core_metrics():
    try:
        # Убрали /api/, так как в main.py ядра его нет
        r = requests.get(f"{CORE_URL}/metrics/current", timeout=5)
        if r.status_code == 200:
            return r.json()
        print(f"[Core] Response error: {r.status_code}")
    except Exception as e:
        print(f"[Error] Failed to connect to Core: {e}")
    return None

@app.route('/')
def dashboard():
    return render_template('pages/dashboard.html')

@app.route('/logs')
def logs_page():
    return render_template('pages/logs.html')

@app.route('/api/metrics')
def api_metrics():
    data = get_core_metrics()
    if not data:
        return jsonify({"error": "Core offline", "status": "offline"}), 503

    return jsonify({
        "cpu": data.get("cpu", "—"),
        # Приводим к формату твоего HTML
        "ram": f"{data.get('ram_percent', 0)}%",
        "disk": f"{data.get('disk_percent', 0)}%",
        "network": f"{data.get('network_recv_mb', 0)}↓ / {data.get('network_sent_mb', 0)}↑ Mb",
        "containers": 27,
        "servers": 12,
        "alerts": 2,
        "tasks": 9
    })

@app.route('/api/logs/history')
def api_logs_history():
    try:
        # Убрали /api/
        r = requests.get(f"{CORE_URL}/logs/history?limit=200", timeout=5)
        return jsonify(r.json() if r.status_code == 200 else [])
    except:
        return jsonify([])

# Real-time logs — ИСПРАВЛЕН ORIGIN
def core_log_listener():
    def on_message(ws, message):
        try:
            data = json.loads(message)
            if isinstance(data, list):
                socketio.emit('log_history', data)
            else:
                socketio.emit('log_update', data)
        except Exception as e:
            print(f"[Log WS] Parsing error: {e}")

    def on_error(ws, error):
        print(f"[Log WS] Connection error: {error}")

    def on_close(ws, close_status_code, close_msg):
        print("[Log WS] Connection closed. Reconnecting...")
        threading.Timer(3.0, core_log_listener).start()

    def on_open(ws):
        print("[Log WS] Connected to Nebula Core")

    while True:
        try:
            # Путь без /api/ и добавление header Origin для обхода 403
            ws_url = CORE_URL.replace("http", "ws") + "/logs/stream"
            ws = websocket.WebSocketApp(
                ws_url,
                header={"Origin: http://127.0.0.1:8000"},
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever(ping_interval=10, ping_timeout=5)
        except Exception as e:
            print(f"[Log WS] Critical error: {e}. Retrying in 5 seconds...")
            time.sleep(5)

threading.Thread(target=core_log_listener, daemon=True).start()

if __name__ == '__main__':
    print(f"Nebula Panel started → http://127.0.0.1:5000")
    socketio.run(app, host='127.0.0.1', port=5000, debug=False)