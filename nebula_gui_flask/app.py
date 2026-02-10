import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO
import threading
import json
import websocket
import time 
from core.bridge import NebulaBridge

app = Flask(__name__)
app.config['SECRET_KEY'] = 'nebula-secret-2026'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

bridge = NebulaBridge()

@app.route('/')
@bridge.login_required
def dashboard():
    return render_template('pages/dashboard.html')

@app.route('/users')
@bridge.login_required
def users_page():
    return render_template('pages/users.html')

@app.route('/users/add')
@bridge.login_required
def add_user_page():
    return render_template('pages/adduser.html')

@app.route('/logs')
@bridge.login_required
def logs_page():
    return render_template('pages/logs.html')


@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        admin_id = request.form.get('admin_id')
        secure_key = request.form.get('secure_key')
        success, error = bridge.admin_auth(admin_id, secure_key)
        
        if success:
            return jsonify({"status": "success", "redirect": url_for('dashboard')})
        return jsonify({"detail": error}), 401
    return render_template('userlogin.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/api/auth/login', methods=['POST'])
def user_login_api():
    data = {
        "username": request.form.get('username'),
        "password": request.form.get('password')
    }
    res, code = bridge.proxy_request("POST", "/users/login", 
                                   params={"db_name": request.form.get('db_name', 'system.db')}, 
                                   form_data=data)
    return jsonify(res), code

@app.route('/api/users/databases')
def api_proxy_databases():
    res, code = bridge.proxy_request("GET", "/users/databases")
    return jsonify(res), code

@app.route('/api/users/list')
def api_proxy_user_list():
    res, code = bridge.proxy_request("GET", "/users/list", params={"db_name": request.args.get('db_name')})
    return jsonify(res), code

@app.route('/api/users/create', methods=['POST'])
def api_proxy_create_user():
    res, code = bridge.proxy_request("POST", "/users/create", 
                                   params={"db_name": request.args.get('db_name')}, 
                                   json_data=request.json)
    return jsonify(res), code

@app.route('/api/users/update', methods=['POST'])
def api_proxy_update_user():
    res, code = bridge.proxy_request("POST", "/users/update", json_data=request.json)
    return jsonify(res), code

@app.route('/api/users/delete', methods=['POST'])
def api_proxy_delete_user():
    params = {
        "db_name": request.args.get('db_name'),
        "username": request.args.get('username')
    }
    res, code = bridge.proxy_request("POST", "/users/delete", params=params)
    return jsonify(res), code

@app.route('/api/metrics')
def api_metrics():
    data = bridge.fetch_metrics()
    if not data:
        return jsonify({"error": "Core offline", "status": "offline"}), 503
    return jsonify({
        "cpu": data.get("cpu", "—"),
        "ram": f"{data.get('ram_percent', 0)}%",
        "disk": f"{data.get('disk_percent', 0)}%",
        "network": f"↑ {data.get('network_sent_mb', 0)} MB/s  ↓ {data.get('network_recv_mb', 0)} MB/s",
        "containers": 27, "servers": 12, "alerts": 2, "tasks": 9
    })

@app.route('/api/logs/history')
def api_logs_history():
    res, code = bridge.proxy_request("GET", "/logs/history", params={"limit": 200})
    return jsonify(res), code

def core_log_listener():
    while True:
        try:
            ws_url = bridge.core_url.replace("http", "ws") + "/logs/stream"
            print(f"[Log WS] Attempting connection to {ws_url}")
            
            def on_message(ws, message):
                try:
                    data = json.loads(message)
                    socketio.emit('log_history' if isinstance(data, list) else 'log_update', data)
                except: pass

            ws = websocket.WebSocketApp(
                ws_url,
                header={"Origin: http://127.0.0.1:8000"},
                on_message=on_message,
                on_error=lambda ws, err: print(f"[Log WS] Error: {err}"),
                on_open=lambda ws: print("[Log WS] Stream connected.")
            )
            ws.run_forever(ping_interval=10, ping_timeout=5)
        except Exception as e:
            print(f"[Log WS] Failure: {e}")
        time.sleep(5)

threading.Thread(target=core_log_listener, daemon=True).start()

if __name__ == '__main__':
    print(f"\n[BOOT] Nebula Panel online -> http://127.0.0.1:5000")
    socketio.run(app, host='127.0.0.1', port=5000, debug=False, use_reloader=False)