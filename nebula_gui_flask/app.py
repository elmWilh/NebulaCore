import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from flask_socketio import SocketIO, join_room
import socketio as socketio_client 
from websocket import WebSocketApp
import json
import time
import logging
import psutil
import threading
import uuid
import requests
import os
import secrets
from urllib.parse import urlparse
from datetime import timedelta

from core.bridge import NebulaBridge

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')

app = Flask(__name__)
def _resolve_gui_secret_key():
    env_key = os.getenv("NEBULA_GUI_SECRET_KEY")
    if env_key:
        return env_key
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for candidate in [
        os.path.join(project_root, ".env"),
        os.path.join(project_root, "install", ".env"),
    ]:
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == "NEBULA_GUI_SECRET_KEY":
                        return v.strip().strip('"').strip("'")
        except Exception:
            continue
    return secrets.token_urlsafe(32)


def _resolve_gui_allowed_origins():
    raw = os.getenv("NEBULA_GUI_CORS_ORIGINS", "http://127.0.0.1:5000,http://localhost:5000")
    return [o.strip() for o in raw.split(",") if o.strip()]


GUI_COOKIE_SECURE = os.getenv("NEBULA_GUI_COOKIE_SECURE", "false").strip().lower() == "true"
GUI_ALLOWED_ORIGINS = _resolve_gui_allowed_origins()

app.config['SECRET_KEY'] = _resolve_gui_secret_key()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = "Lax"
app.config['SESSION_COOKIE_SECURE'] = GUI_COOKIE_SECURE
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
socketio = SocketIO(app, cors_allowed_origins=GUI_ALLOWED_ORIGINS, async_mode="eventlet")

bridge = NebulaBridge()
def _read_env_value(file_path: str, key: str):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except Exception:
        return None
    return None

def _resolve_internal_auth_key():
    env_key = os.getenv("NEBULA_INSTALLER_TOKEN")
    if env_key:
        return env_key
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for candidate in [
        os.path.join(project_root, ".env"),
        os.path.join(project_root, "install", ".env"),
    ]:
        val = _read_env_value(candidate, "NEBULA_INSTALLER_TOKEN")
        if val:
            return val
    return ""

INTERNAL_AUTH_KEY = _resolve_internal_auth_key()
if not INTERNAL_AUTH_KEY:
    logging.getLogger("nebula_gui_flask").warning(
        "NEBULA_INSTALLER_TOKEN is not configured; Core logs stream may return 403."
    )
deploy_jobs = {}
deploy_jobs_lock = threading.Lock()
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_TRUSTED_ORIGINS = set(GUI_ALLOWED_ORIGINS)


def _origin_is_trusted(origin_value: str) -> bool:
    if not origin_value:
        return False
    parsed = urlparse(origin_value)
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in CSRF_TRUSTED_ORIGINS:
        return True
    return origin.rstrip("/") == request.host_url.rstrip("/")


@app.before_request
def csrf_guard():
    if request.method not in UNSAFE_METHODS:
        return None
    if not request.path.startswith("/api/"):
        return None
    if "user_id" not in session:
        return None

    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin and _origin_is_trusted(origin):
        return None
    if referer and _origin_is_trusted(referer):
        return None
    return jsonify({"detail": "CSRF validation failed"}), 403


def _append_deploy_log(job_id: str, message: str):
    with deploy_jobs_lock:
        job = deploy_jobs.get(job_id)
        if not job:
            return
        job["logs"].append(message)
        job["updated_at"] = time.time()


def _update_deploy_job(job_id: str, **updates):
    with deploy_jobs_lock:
        job = deploy_jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def _core_request_with_session(method: str, endpoint: str, core_session: str, params=None, json_data=None):
    url = f"{bridge.core_url}{endpoint}"
    cookies = {"nebula_session": core_session}
    r = requests.request(
        method=method,
        url=url,
        params=params,
        json=json_data,
        cookies=cookies,
        timeout=20
    )
    try:
        body = r.json()
    except Exception:
        body = {"detail": r.text}
    return body, r.status_code


def _run_deploy_job(job_id: str, payload: dict, started_by: str, core_session: str):
    try:
        _update_deploy_job(job_id, status="running", stage="Validating configuration", progress=12)
        _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Payload validation started by {started_by}")
        time.sleep(0.35)

        _update_deploy_job(job_id, stage="Preparing environment", progress=28)
        _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Preparing runtime resources")
        time.sleep(0.35)

        _update_deploy_job(job_id, stage="Deploying container", progress=55)
        _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Sending deploy request to Nebula Core")
        res, code = _core_request_with_session(
            "POST",
            "/containers/deploy",
            core_session,
            json_data=payload
        )

        if code >= 400:
            detail = res.get("detail") if isinstance(res, dict) else str(res)
            _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Deployment failed: {detail}")
            _update_deploy_job(
                job_id,
                status="failed",
                stage="Deployment failed",
                progress=100,
                error=detail or "Deployment failed",
                result=None
            )
            return

        deployed_id = res.get("id") if isinstance(res, dict) else None
        _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Container deployed successfully: {deployed_id or 'unknown id'}")
        _update_deploy_job(
            job_id,
            status="success",
            stage="Completed",
            progress=100,
            result={"id": deployed_id}
        )
    except Exception as e:
        _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Fatal deploy error: {str(e)}")
        _update_deploy_job(
            job_id,
            status="failed",
            stage="Deployment failed",
            progress=100,
            error=str(e),
            result=None
        )

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

@app.route('/containers')
@bridge.login_required
def containers_page():
    return render_template('pages/containers.html')

@app.route('/containers/view/<container_id>')
@bridge.login_required
def container_workspace_page(container_id):
    return render_template('pages/container_workspace.html', container_id=container_id)

@app.route('/userpanel')
@bridge.login_required
def user_panel_page():
    return render_template(
        'pages/userpanel.html',
        username=session.get('user_id'),
        is_staff=bool(session.get('is_staff'))
    )

@app.route('/logs')
@bridge.login_required
@bridge.staff_required
def logs_page():
    return render_template('pages/logs.html')

@app.route('/api/containers/list')
@bridge.login_required
def api_list_containers():
    params = {}
    node = request.args.get("node")
    if node:
        params["node"] = node
    res, code = bridge.proxy_request("GET", "/containers/list", params=params or None)
    return jsonify(res), code

@app.route('/api/containers/deploy', methods=['POST'])
@bridge.login_required
@bridge.staff_required
def api_deploy_container():
    res, code = bridge.proxy_request("POST", "/containers/deploy", json_data=request.json)
    return jsonify(res), code

@app.route('/api/containers/deploy/start', methods=['POST'])
@bridge.login_required
@bridge.staff_required
def api_deploy_container_start():
    payload = request.json or {}
    job_id = uuid.uuid4().hex
    started_by = session.get('user_id', 'unknown')
    core_session = session.get("core_session")
    if not core_session:
        return jsonify({"detail": "No active core session"}), 401
    now = time.time()

    with deploy_jobs_lock:
        deploy_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "Queued",
            "progress": 3,
            "logs": [f"[{time.strftime('%H:%M:%S')}] Deployment job queued"],
            "error": None,
            "result": None,
            "created_by": started_by,
            "created_at": now,
            "updated_at": now,
        }

    worker = threading.Thread(
        target=_run_deploy_job,
        args=(job_id, payload, started_by, core_session),
        daemon=True
    )
    worker.start()
    return jsonify({"job_id": job_id, "status": "queued"}), 202

@app.route('/api/containers/deploy/status/<job_id>')
@bridge.login_required
@bridge.staff_required
def api_deploy_container_status(job_id):
    with deploy_jobs_lock:
        job = deploy_jobs.get(job_id)
        if not job:
            return jsonify({"detail": "Deploy job not found"}), 404
        if job.get("created_by") != session.get("user_id"):
            return jsonify({"detail": "Access denied for this deploy job"}), 403
        return jsonify(job), 200

@app.route('/api/containers/nodes')
@bridge.login_required
@bridge.staff_required
def api_get_nodes():
    return jsonify({
        "nodes": [
            {
                "id": "nebula-core-local",
                "label": f"Nebula Core ({bridge.core_url.replace('http://', '')})",
                "status": "active"
            }
        ],
        "active_node": "nebula-core-local"
    })

@app.route('/api/containers/restart/<container_id>', methods=['POST'])
@bridge.login_required
def api_restart_container(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/restart/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/start/<container_id>', methods=['POST'])
@bridge.login_required
def api_start_container(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/start/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/stop/<container_id>', methods=['POST'])
@bridge.login_required
def api_stop_container(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/stop/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/logs/<container_id>')
@bridge.login_required
def api_container_logs(container_id):
    tail = request.args.get("tail", "200")
    res, code = bridge.proxy_request("GET", f"/containers/logs/{container_id}", params={"tail": tail})
    return jsonify(res), code

@app.route('/api/containers/detail/<container_id>')
@bridge.login_required
def api_container_detail(container_id):
    res, code = bridge.proxy_request("GET", f"/containers/detail/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/profile/<container_id>')
@bridge.login_required
def api_container_profile(container_id):
    res, code = bridge.proxy_request("GET", f"/containers/profile/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/exec/<container_id>', methods=['POST'])
@bridge.login_required
def api_container_exec(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/exec/{container_id}", json_data=request.json)
    return jsonify(res), code

@app.route('/api/containers/console-send/<container_id>', methods=['POST'])
@bridge.login_required
def api_container_console_send(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/console-send/{container_id}", json_data=request.json)
    return jsonify(res), code

@app.route('/api/containers/files/<container_id>')
@bridge.login_required
def api_container_files(container_id):
    path = request.args.get("path", "/")
    res, code = bridge.proxy_request("GET", f"/containers/files/{container_id}", params={"path": path})
    return jsonify(res), code

@app.route('/api/containers/workspace-roots/<container_id>')
@bridge.login_required
def api_container_workspace_roots(container_id):
    res, code = bridge.proxy_request("GET", f"/containers/workspace-roots/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/file-content/<container_id>')
@bridge.login_required
def api_container_file_content(container_id):
    path = request.args.get("path", "")
    max_bytes = request.args.get("max_bytes", "200000")
    params = {"path": path, "max_bytes": max_bytes}
    res, code = bridge.proxy_request("GET", f"/containers/file-content/{container_id}", params=params)
    return jsonify(res), code

@app.route('/api/containers/settings/<container_id>')
@bridge.login_required
def api_container_settings_get(container_id):
    res, code = bridge.proxy_request("GET", f"/containers/settings/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/settings/<container_id>', methods=['POST'])
@bridge.login_required
def api_container_settings_update(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/settings/{container_id}", json_data=request.json)
    return jsonify(res), code

@app.route('/api/containers/restart-policy/<container_id>')
@bridge.login_required
def api_container_restart_policy_get(container_id):
    res, code = bridge.proxy_request("GET", f"/containers/restart-policy/{container_id}")
    return jsonify(res), code

@app.route('/api/containers/restart-policy/<container_id>', methods=['POST'])
@bridge.login_required
def api_container_restart_policy_update(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/restart-policy/{container_id}", json_data=request.json)
    return jsonify(res), code

@app.route('/api/containers/delete/<container_id>', methods=['POST'])
@bridge.login_required
@bridge.staff_required
def api_delete_container(container_id):
    res, code = bridge.proxy_request("POST", f"/containers/delete/{container_id}")
    return jsonify(res), code

@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        otp = (request.form.get('otp') or '').strip()
        # Always drop previous GUI session before a new login attempt to
        # prevent stale identity reuse after failed authentication.
        session.clear()
        db_name, user_type = bridge.resolve_user_sector(username)
        if not db_name:
            return jsonify({"detail": "User not found"}), 401
        if user_type == 'staff':
            success, error = bridge.admin_auth(username, password, otp=otp)
        else:
            success, error = bridge.user_auth(username, password, db_name, otp=otp)
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
    username = request.form.get('username')
    password = request.form.get('password')
    otp = (request.form.get('otp') or '').strip()
    db_name = request.form.get('db_name', 'system.db')
    success, error = bridge.user_auth(username, password, db_name, otp=otp)
    if success:
        return jsonify({"status": "success", "redirect": url_for('dashboard')}), 200
    return jsonify({"detail": error or "INVALID_CREDENTIALS"}), 401


@app.route('/api/user/2fa/status')
@bridge.login_required
def api_user_2fa_status():
    res, code = bridge.proxy_request("GET", "/users/2fa/status")
    return jsonify(res), code


@app.route('/api/user/2fa/setup', methods=['POST'])
@bridge.login_required
def api_user_2fa_setup():
    res, code = bridge.proxy_request("POST", "/users/2fa/setup")
    return jsonify(res), code


@app.route('/api/user/2fa/confirm', methods=['POST'])
@bridge.login_required
def api_user_2fa_confirm():
    code_val = (request.json or {}).get("code", "")
    res, code = bridge.proxy_request("POST", "/users/2fa/confirm", form_data={"code": code_val})
    return jsonify(res), code


@app.route('/api/user/2fa/disable', methods=['POST'])
@bridge.login_required
def api_user_2fa_disable():
    code_val = (request.json or {}).get("code", "")
    res, code = bridge.proxy_request("POST", "/users/2fa/disable", form_data={"code": code_val})
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
    def _to_float(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace('%', '')
        try:
            return float(text)
        except ValueError:
            return None

    is_staff = bool(session.get('is_staff'))
    summary, summary_code = bridge.proxy_request("GET", "/containers/summary")
    if summary_code >= 400:
        summary = {}

    if not is_staff:
        cpu_percent = _to_float(summary.get("cpu_percent")) or 0.0
        ram_percent = _to_float(summary.get("memory_percent")) or 0.0
        network_sent = _to_float(summary.get("network_tx_mbps")) or 0.0
        network_recv = _to_float(summary.get("network_rx_mbps")) or 0.0
        ram_used_gb = (_to_float(summary.get("memory_used_mb")) or 0.0) / 1024.0
        ram_total_gb = (_to_float(summary.get("memory_limit_mb")) or 0.0) / 1024.0
        containers_count = int(summary.get("total_containers") or 0)
        active_containers = int(summary.get("running_containers") or 0)
        disk_percent = 0.0
        cpu_cores_total = psutil.cpu_count(logical=True) or 1
        cpu_cores_active = round((cpu_percent or 0.0) * cpu_cores_total / 100.0, 1)

        max_pressure = max(cpu_percent, ram_percent)
        if max_pressure >= 90:
            health_status = "critical"
        elif max_pressure >= 75:
            health_status = "elevated"
        elif max_pressure >= 55:
            health_status = "stable"
        else:
            health_status = "optimal"

        return jsonify({
            "scope": "user_containers",
            "cpu": f"{cpu_percent:.1f}%",
            "ram": f"{ram_percent:.1f}%",
            "disk": "—",
            "network": f"↑ {network_sent:.2f} MB/s  ↓ {network_recv:.2f} MB/s",
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "disk_percent": disk_percent,
            "network_sent_mb": network_sent,
            "network_recv_mb": network_recv,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "cpu_cores_total": cpu_cores_total,
            "cpu_cores_active": cpu_cores_active,
            "health_status": health_status,
            "containers": containers_count,
            "active_containers": active_containers,
            "servers": 0,
            "alerts": 0,
            "tasks": 0
        })

    data = bridge.fetch_metrics()
    if not data:
        return jsonify({"error": "Core offline", "status": "offline"}), 503

    cpu_raw = data.get("cpu") or data.get("cpu_percent") or data.get("cpu_usage")
    ram_raw = data.get("ram_percent") or data.get("memory") or data.get("mem_percent")
    disk_raw = data.get("disk_percent") or data.get("disk") or 0
    network_sent = _to_float(data.get("network_sent_mb") or data.get("sent")) or 0.0
    network_recv = _to_float(data.get("network_recv_mb") or data.get("recv")) or 0.0
    containers_count = int(summary.get("total_containers") or data.get("containers_count") or data.get("containers") or 0)
    active_containers = int(summary.get("running_containers") or data.get("active_containers") or containers_count)
    ram_used_gb = _to_float(data.get("ram_used_gb"))
    ram_total_gb = _to_float(data.get("ram_total_gb"))

    cpu_percent = _to_float(cpu_raw)
    ram_percent = _to_float(ram_raw)
    disk_percent = _to_float(disk_raw) or 0.0

    cpu_cores_total = psutil.cpu_count(logical=True) or 1
    cpu_cores_active = round((cpu_percent or 0.0) * cpu_cores_total / 100.0, 1)

    max_pressure = max(cpu_percent or 0.0, ram_percent or 0.0, disk_percent)
    if max_pressure >= 90:
        health_status = "critical"
    elif max_pressure >= 75:
        health_status = "elevated"
    elif max_pressure >= 55:
        health_status = "stable"
    else:
        health_status = "optimal"

    return jsonify({
        "scope": "server",
        "cpu": f"{cpu_percent:.1f}%" if cpu_percent is not None else "—",
        "ram": f"{ram_percent:.1f}%" if ram_percent is not None else "—",
        "disk": f"{disk_percent:.1f}%",
        "network": f"↑ {network_sent:.2f} MB/s  ↓ {network_recv:.2f} MB/s",
        "cpu_percent": cpu_percent,
        "ram_percent": ram_percent,
        "disk_percent": disk_percent,
        "network_sent_mb": network_sent,
        "network_recv_mb": network_recv,
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "cpu_cores_total": cpu_cores_total,
        "cpu_cores_active": cpu_cores_active,
        "health_status": health_status,
        "containers": containers_count,
        "active_containers": active_containers,
        "servers": 1,
        "alerts": 0,
        "tasks": 0
    })

@app.route('/api/userpanel/overview')
@bridge.login_required
def api_userpanel_overview():
    username = session.get('user_id', 'unknown')
    db_name = session.get('db_name', 'system.db')
    is_staff = bool(session.get('is_staff'))

    summary, summary_code = bridge.proxy_request("GET", "/containers/summary")
    if summary_code >= 400 or not isinstance(summary, dict):
        summary = {}

    containers, containers_code = bridge.proxy_request("GET", "/containers/list")
    if containers_code >= 400 or not isinstance(containers, list):
        containers = []

    dbs = [db_name]
    if is_staff:
        db_res, db_code = bridge.proxy_request("GET", "/users/databases")
        if db_code < 400 and isinstance(db_res, dict) and isinstance(db_res.get("databases"), list):
            dbs = db_res["databases"]

    running = int(summary.get("running_containers") or 0)
    total = int(summary.get("total_containers") or len(containers))
    cpu_percent = float(summary.get("cpu_percent") or 0.0)
    memory_percent = float(summary.get("memory_percent") or 0.0)

    activity = []
    if is_staff:
        logs, logs_code = bridge.proxy_request("GET", "/logs/history", params={"limit": 8})
        if logs_code < 400 and isinstance(logs, list):
            activity = logs

    if not activity:
        now_label = time.strftime("%Y-%m-%d %H:%M:%S")
        activity = [
            {"iso": now_label, "level": "INFO", "message": f"Session active for {username}"},
            {"iso": now_label, "level": "INFO", "message": f"Connected sector: {db_name}"},
            {"iso": now_label, "level": "INFO", "message": f"Visible containers: {len(containers)}"},
        ]

    return jsonify({
        "username": username,
        "db_name": db_name,
        "is_staff": is_staff,
        "stats": {
            "running_containers": running,
            "total_containers": total,
            "cpu_percent": round(cpu_percent, 2),
            "memory_percent": round(memory_percent, 2),
            "databases_count": len(dbs),
        },
        "containers": containers,
        "databases": dbs,
        "activity": activity,
    })

@app.route('/users/view/<username>')
@bridge.login_required
def view_user_page(username):
    if not session.get('is_staff') and session.get('user_id') != username:
        abort(403)
    db_name = request.args.get('db_name') or session.get('db_name') or 'system.db'
    if not session.get('is_staff'):
        db_name = session.get('db_name') or db_name
    user_data = {'username': username, 'db_name': db_name}
    return render_template('pages/userdata.html', user=user_data)

@app.route('/api/users/detail/<username>')
@bridge.login_required
def api_user_detail(username):
    if not session.get('is_staff') and session.get('user_id') != username:
        return jsonify({"detail": "Access Denied"}), 403
    db_name = request.args.get('db_name')
    params = {"db_name": db_name} if db_name else None
    res, code = bridge.proxy_request("GET", f"/users/detail/{username}", params=params)
    return jsonify(res), code

@app.route('/api/logs/history')
@bridge.login_required
@bridge.staff_required
def api_logs_history():
    res, code = bridge.proxy_request("GET", "/logs/history", params={"limit": 200})
    return jsonify(res), code

def core_log_listener():
    logger = logging.getLogger("CoreListener")
    while True:
        try:
            core_ws_url = bridge.core_url.replace("http", "ws") + "/logs/stream"
            logger.info(f"Attempting to connect to Core WebSocket: {core_ws_url}")
            headers = []
            if INTERNAL_AUTH_KEY:
                headers.append(f"X-Nebula-Token: {INTERNAL_AUTH_KEY}")

            def _on_message(ws, message):
                try:
                    data = json.loads(message)
                except Exception:
                    return
                if isinstance(data, list):
                    socketio.emit("log_history", data, to="staff")
                else:
                    socketio.emit("log_update", data, to="staff")

            def _on_error(ws, error):
                logger.error(f"Core WebSocket error: {error}")

            def _on_close(ws, close_status_code, close_msg):
                logger.warning("Core connection closed, reconnecting...")

            ws_app = WebSocketApp(core_ws_url,
                                  header=headers,
                                  on_message=_on_message,
                                  on_error=_on_error,
                                  on_close=_on_close)
            ws_app.run_forever()
        except Exception as e:
            logger.error(f"Error connecting to Core: {e}")
        time.sleep(5)
        
eventlet.spawn(core_log_listener)


@socketio.on('connect')
def handle_socket_connect():
    if session.get('is_staff'):
        join_room("staff")

if __name__ == '__main__':
    logging.getLogger("nebula_gui_flask").info("Starting Nebula GUI panel")
    socketio.run(app, host='127.0.0.1', port=5000, debug=False, use_reloader=False)
