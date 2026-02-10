# nebula_gui_flask/app.py
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
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
@bridge.staff_required
def users_page():
    return render_template('pages/users.html')

@app.route('/users/add')
@bridge.login_required
@bridge.staff_required
def add_user_page():
    return render_template('pages/adduser.html')

@app.route('/logs')
@bridge.login_required
@bridge.staff_required
def logs_page():
    return render_template('pages/logs.html')

@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        db_name, user_type = bridge.resolve_user_sector(username)
        
        if not db_name:
            return jsonify({"detail": "User not found"}), 401

        if user_type == 'staff' or db_name == 'system.db':
            success, error = bridge.admin_auth(username, password)
        else:
            success, error = bridge.user_auth(username, password, db_name)
        
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
@bridge.login_required
@bridge.staff_required
def api_proxy_databases():
    res, code = bridge.proxy_request("GET", "/users/databases")
    return jsonify(res), code

@app.route('/api/users/list')
@bridge.login_required
@bridge.staff_required
def api_proxy_user_list():
    res, code = bridge.proxy_request("GET", "/users/list", params={"db_name": request.args.get('db_name')})
    return jsonify(res), code

@app.route('/api/users/create', methods=['POST'])
@bridge.login_required
@bridge.staff_required
def api_proxy_create_user():
    res, code = bridge.proxy_request("POST", "/users/create", 
                                   params={"db_name": request.args.get('db_name')}, 
                                   json_data=request.json)
    return jsonify(res), code

@app.route('/api/users/update', methods=['POST'])
@bridge.login_required
@bridge.staff_required
def api_proxy_update_user():
    res, code = bridge.proxy_request("POST", "/users/update", json_data=request.json)
    return jsonify(res), code

@app.route('/api/users/delete', methods=['POST'])
@bridge.login_required
@bridge.staff_required
def api_proxy_delete_user():
    params = {
        "db_name": request.args.get('db_name'),
        "username": request.args.get('username')
    }
    res, code = bridge.proxy_request("POST", "/users/delete", params=params)
    return jsonify(res), code

@app.route('/api/metrics')
@bridge.login_required
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

@app.route('/users/view/<username>')
@bridge.login_required
def view_user_page(username):
    if not session.get('is_staff') and session.get('user_id') != username:
        abort(403)
    user_data = {'username': username} 
    return render_template('pages/userdata.html', user=user_data)

@app.route('/api/users/detail/<username>')
@bridge.login_required
def api_user_detail(username):
    if not session.get('is_staff') and session.get('user_id') != username:
        return jsonify({"detail": "Access Denied"}), 403

    mock_data = {
        "status": "active",
        "access_level": "Root Administrator" if username == "admin" else "Standard User",
        "network_id": f"10.0.8.{hash(username) % 255}",
        "containers": [
            {"id": "nebula-node-01", "status": "running", "uptime": "12d 4h"},
            {"id": "proxy-shifter", "status": "running", "uptime": "2d 18h"},
            {"id": "storage-vault", "status": "paused", "uptime": "0s"}
        ],
        "open_ports": [80, 443, 22, 8080],
        "last_activity": "2 minutes ago"
    }
    return jsonify(mock_data)

@app.route('/api/logs/history')
@bridge.login_required
@bridge.staff_required
def api_logs_history():
    res, code = bridge.proxy_request("GET", "/logs/history", params={"limit": 200})
    return jsonify(res), code

def core_log_listener():
    while True:
        try:
            ws_url = bridge.core_url.replace("http", "ws") + "/logs/stream"
            
            def on_message(ws, message):
                try:
                    data = json.loads(message)
                    socketio.emit('log_history' if isinstance(data, list) else 'log_update', data)
                except: pass

            ws = websocket.WebSocketApp(
                ws_url,
                header={"Origin: http://127.0.0.1:8000"},
                on_message=on_message,
                on_error=lambda ws, err: None,
                on_open=lambda ws: None
            )
            ws.run_forever(ping_interval=10, ping_timeout=5)
        except:
            pass
        time.sleep(5)

threading.Thread(target=core_log_listener, daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, host='127.0.0.1', port=5000, debug=False, use_reloader=False)