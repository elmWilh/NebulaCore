# nebula_gui_flask/app.py — FINAL VERSION 2026 (with Core auto-detection)
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

# Auto-detect Core port (8000 → 8080 → 5000)
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

# Fetch Metrics
def get_core_metrics():
    try:
        r = requests.get(f"{CORE_URL}/metrics/current", timeout=5)
        if r.status_code == 200:
            return r.json()
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

    # Processing data using keys from your API
    return jsonify({
        "cpu": data.get("cpu", "—"),
        "ram": f"{data.get('ram_used_gb', 0):.1f} / {data.get('ram_total_gb', 0):.1f} GB",
        "disk": f"{data.get('disk_used_gb', 0):.0f} / {data.get('disk_total_gb', 0):.0f} GB",
        "network": f"Up {data.get('network_sent_mb', 0)//1024:.1f} GB  Down {data.get('network_recv_mb', 0)//1024:.1f} GB",
        "containers": 27, # Static placeholders from original
        "servers": 12,
        "alerts": 2,
        "tasks": 9
    })

@app.route('/api/logs/history')
def api_logs_history():
    try:
        r = requests.get(f"{CORE_URL}/logs/history?limit=200", timeout=5)
        return jsonify(r.json() if r.status_code == 200 else [])
    except:
        return jsonify([])

# Real-time logs via WebSocket
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
        print("[Log WS] Connection closed. Reconnecting in 3 seconds...")
        threading.Timer(3.0, core_log_listener).start()

    def on_open(ws):
        print("[Log WS] Connected to Nebula Core — Streaming real-time logs")

    while True:
        try:
            ws_url = CORE_URL.replace("http", "ws") + "/logs/stream"
            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever(ping_interval=10, ping_timeout=5)
        except Exception as e:
            print(f"[Log WS] Critical error: {e}. Retrying in 5 seconds...")
            time.sleep(5)

# Start log listener thread
threading.Thread(target=core_log_listener, daemon=True).start()

# Application entry point
if __name__ == '__main__':
    print(f"Nebula Panel started → http://127.0.0.1:5000")
    print(f"Connecting to Core: {CORE_URL}")
    socketio.run(app, host='127.0.0.1', port=5000, debug=False)