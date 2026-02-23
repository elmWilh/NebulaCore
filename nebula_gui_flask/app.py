# nebula_gui_flask/app.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort, g
from flask_socketio import SocketIO, join_room
import socketio as socketio_client 
from websocket import WebSocketApp
import json
import random
import time
import logging
import psutil
import threading
import requests
import os
import secrets
import re
import glob
import hashlib
import base64
from urllib.parse import urlparse
from datetime import timedelta
from werkzeug.exceptions import HTTPException

from core.bridge import NebulaBridge
from routes.api_containers import register_container_api_routes
from routes.api_projects import register_projects_api_routes, link_container_to_projects
from routes.api_users import register_user_api_routes
from routes.pages import register_pages_routes

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')

app = Flask(__name__)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_FILE_CANDIDATES = (
    os.path.join(PROJECT_ROOT, ".env"),
    os.path.join(PROJECT_ROOT, "install", ".env"),
)
ERROR_QUOTES_PATH = os.path.join(os.path.dirname(__file__), "data", "error_quotes.json")


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


def _resolve_env_value(key: str, default: str | None = None) -> str | None:
    env_key = os.getenv(key)
    if env_key not in (None, ""):
        return env_key
    for candidate in ENV_FILE_CANDIDATES:
        value = _read_env_value(candidate, key)
        if value not in (None, ""):
            return value
    return default


def _resolve_bool_env(key: str, default: bool = False) -> bool:
    default_str = "true" if default else "false"
    value = (_resolve_env_value(key, default_str) or default_str).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _resolve_int_env(key: str, default: int, min_value: int = 1) -> int:
    raw_value = _resolve_env_value(key, str(default))
    try:
        resolved = int(raw_value)
    except (TypeError, ValueError):
        logging.getLogger("nebula_gui_flask").warning(
            "%s has invalid value %r; using default %s", key, raw_value, default
        )
        return default
    if resolved < min_value:
        logging.getLogger("nebula_gui_flask").warning(
            "%s must be >= %s; got %s. Using default %s", key, min_value, resolved, default
        )
        return default
    return resolved


def _resolve_gui_secret_key():
    env_key = _resolve_env_value("NEBULA_GUI_SECRET_KEY")
    if env_key:
        return env_key
    return secrets.token_urlsafe(32)


def _resolve_gui_allowed_origins():
    raw = _resolve_env_value(
        "NEBULA_GUI_CORS_ORIGINS",
        "http://127.0.0.1:5000,http://localhost:5000",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


GUI_COOKIE_SECURE = _resolve_bool_env("NEBULA_GUI_COOKIE_SECURE", default=False)
GUI_ALLOWED_ORIGINS = _resolve_gui_allowed_origins()


def _resolve_template_inline_handler_hashes() -> list[str]:
    template_root = os.path.join(os.path.dirname(__file__), "templates")
    inline_handler_pattern = re.compile(r"\son[a-zA-Z0-9_-]*\s*=\s*(\"([^\"]*)\"|'([^']*)')")
    handlers = set()
    for template_path in glob.glob(os.path.join(template_root, "**", "*.html"), recursive=True):
        try:
            with open(template_path, "r", encoding="utf-8") as template_file:
                content = template_file.read()
        except OSError:
            continue
        for match in inline_handler_pattern.finditer(content):
            value = match.group(2) if match.group(2) is not None else match.group(3)
            if value:
                handlers.add(value.strip())

    hashes = []
    for handler in sorted(handlers):
        digest = hashlib.sha256(handler.encode("utf-8")).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return hashes


INLINE_HANDLER_HASHES = _resolve_template_inline_handler_hashes()

app.config['SECRET_KEY'] = _resolve_gui_secret_key()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = "Lax"
app.config['SESSION_COOKIE_SECURE'] = GUI_COOKIE_SECURE
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)
socketio = SocketIO(app, cors_allowed_origins=GUI_ALLOWED_ORIGINS, async_mode="eventlet")

bridge = NebulaBridge()
def _resolve_internal_auth_key():
    return _resolve_env_value("NEBULA_INSTALLER_TOKEN", "") or ""

INTERNAL_AUTH_KEY = _resolve_internal_auth_key()
if not INTERNAL_AUTH_KEY:
    logging.getLogger("nebula_gui_flask").warning(
        "NEBULA_INSTALLER_TOKEN is not configured; Core logs stream may return 403."
    )
deploy_jobs = {}
deploy_jobs_lock = threading.Lock()
metrics_cache = {}
metrics_cache_lock = threading.Lock()
METRICS_CACHE_TTL = 2.5
error_quotes_lock = threading.Lock()
error_quotes_cache = []
error_quotes_mtime = None
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_TRUSTED_ORIGINS = set(GUI_ALLOWED_ORIGINS)
LOGIN_ATTEMPT_WINDOW_SECONDS = _resolve_int_env("NEBULA_LOGIN_ATTEMPT_WINDOW_SECONDS", default=300)
LOGIN_MAX_ATTEMPTS = _resolve_int_env("NEBULA_LOGIN_MAX_ATTEMPTS", default=5)
LOGIN_LOCKOUT_SECONDS = _resolve_int_env("NEBULA_LOGIN_LOCKOUT_SECONDS", default=900)
login_rate_limiter = {}
login_rate_limiter_lock = threading.Lock()

ERROR_PAGE_META = {
    303: {
        "title": "See Other",
        "subtitle": "The destination changed orbit. Follow the updated trajectory.",
        "hint": "Use the link below to continue to a new location.",
    },
    400: {
        "title": "Bad Request",
        "subtitle": "The request payload broke protocol alignment.",
        "hint": "Check fields, formats, and required values.",
    },
    401: {
        "title": "Unauthorized",
        "subtitle": "Authentication token was not accepted by this gate.",
        "hint": "Sign in again and verify your credentials.",
    },
    403: {
        "title": "Forbidden",
        "subtitle": "Access policy denied this operation.",
        "hint": "Request elevated permissions or switch account context.",
    },
    404: {
        "title": "Not Found",
        "subtitle": "This route dissolved into cosmic dust.",
        "hint": "Double-check the URL or return to dashboard.",
    },
    405: {
        "title": "Method Not Allowed",
        "subtitle": "This endpoint rejects the selected HTTP method.",
        "hint": "Use the method declared by API documentation.",
    },
    408: {
        "title": "Request Timeout",
        "subtitle": "The request took too long and the channel closed.",
        "hint": "Retry and check network latency.",
    },
    409: {
        "title": "Conflict",
        "subtitle": "Operation collided with existing system state.",
        "hint": "Refresh data and repeat after conflict resolution.",
    },
    410: {
        "title": "Gone",
        "subtitle": "This resource was decommissioned and removed.",
        "hint": "Find the replacement endpoint or recreate resource.",
    },
    418: {
        "title": "Teapot Mode",
        "subtitle": "Control plane is brewing instead of serving.",
        "hint": "Try a different endpoint while tea cools down.",
    },
    422: {
        "title": "Unprocessable Entity",
        "subtitle": "Request syntax is valid but business rules failed.",
        "hint": "Validate domain constraints before retry.",
    },
    429: {
        "title": "Too Many Requests",
        "subtitle": "Rate limiter engaged to protect the control core.",
        "hint": "Pause requests and retry after cooldown.",
    },
    500: {
        "title": "Internal Server Error",
        "subtitle": "Unexpected failure inside the Nebula control stack.",
        "hint": "Review server logs and retry the operation.",
    },
    501: {
        "title": "Not Implemented",
        "subtitle": "This feature is charted but not shipped yet.",
        "hint": "Use available routes or wait for next update.",
    },
    502: {
        "title": "Bad Gateway",
        "subtitle": "Upstream node returned an invalid response.",
        "hint": "Check bridge and upstream service health.",
    },
    503: {
        "title": "Service Unavailable",
        "subtitle": "Control services are temporarily offline.",
        "hint": "Retry later after service recovery.",
    },
    504: {
        "title": "Gateway Timeout",
        "subtitle": "Upstream service did not answer in time.",
        "hint": "Inspect upstream performance and timeouts.",
    },
}

FALLBACK_ERROR_QUOTES = [
    {"text": "The obstacle is the way.", "author": "Marcus Aurelius"},
    {"text": "Simplicity is the ultimate sophistication.", "author": "Leonardo da Vinci"},
    {"text": "A smooth sea never made a skilled sailor.", "author": "English proverb"},
    {"text": "In the middle of difficulty lies opportunity.", "author": "Albert Einstein"},
    {"text": "Do not fear mistakes. You will know failure. Continue to reach out.", "author": "Benjamin Franklin"},
    {"text": "The best systems are built by iteration, not illusion.", "author": "Nebula Notes"},
]


class SeeOtherDisplayException(HTTPException):
    code = 303
    description = "See Other"


def _resolve_client_ip() -> str:
    forwarded_for = (request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    if forwarded_for:
        return forwarded_for
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip
    return request.remote_addr or "unknown"


def _check_login_block(ip_addr: str) -> int:
    now = time.time()
    window_start = now - LOGIN_ATTEMPT_WINDOW_SECONDS
    with login_rate_limiter_lock:
        state = login_rate_limiter.get(ip_addr)
        if not state:
            return 0
        failures = [ts for ts in state.get("failures", []) if ts >= window_start]
        state["failures"] = failures
        lock_until = float(state.get("lock_until", 0) or 0)
        if lock_until > now:
            state["lock_until"] = lock_until
            login_rate_limiter[ip_addr] = state
            return int(lock_until - now)
        state["lock_until"] = 0
        if not failures:
            login_rate_limiter.pop(ip_addr, None)
        else:
            login_rate_limiter[ip_addr] = state
        return 0


def _register_failed_login_attempt(ip_addr: str):
    now = time.time()
    window_start = now - LOGIN_ATTEMPT_WINDOW_SECONDS
    with login_rate_limiter_lock:
        state = login_rate_limiter.get(ip_addr, {"failures": [], "lock_until": 0})
        failures = [ts for ts in state.get("failures", []) if ts >= window_start]
        failures.append(now)
        lock_until = float(state.get("lock_until", 0) or 0)
        if len(failures) >= LOGIN_MAX_ATTEMPTS:
            lock_until = now + LOGIN_LOCKOUT_SECONDS
            failures = []
        login_rate_limiter[ip_addr] = {"failures": failures, "lock_until": lock_until}


def _clear_login_attempts(ip_addr: str):
    with login_rate_limiter_lock:
        login_rate_limiter.pop(ip_addr, None)


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


@app.before_request
def prepare_csp_nonce():
    g.csp_nonce = secrets.token_urlsafe(16)


@app.context_processor
def inject_csp_nonce():
    return {"csp_nonce": getattr(g, "csp_nonce", "")}


def _build_csp_header() -> str:
    nonce = getattr(g, "csp_nonce", "")
    trusted_connect = sorted({origin.rstrip("/") for origin in GUI_ALLOWED_ORIGINS if origin})
    connect_src = ["'self'", "ws:", "wss:"] + trusted_connect

    script_src = [
        "'self'",
        f"'nonce-{nonce}'",
        "'strict-dynamic'",
        "'unsafe-hashes'",
        "https://cdn.jsdelivr.net",
        "https://cdn.socket.io",
    ] + INLINE_HANDLER_HASHES

    directives = {
        "default-src": ["'self'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
        "frame-ancestors": ["'none'"],
        "frame-src": ["'none'"],
        "form-action": ["'self'"],
        "script-src": script_src,
        "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdn.jsdelivr.net"],
        "font-src": ["'self'", "https://fonts.gstatic.com", "https://cdn.jsdelivr.net", "data:"],
        "img-src": ["'self'", "data:"],
        "connect-src": connect_src,
    }
    return "; ".join(f"{directive} {' '.join(values)}" for directive, values in directives.items())


@app.after_request
def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if response.mimetype == "text/html":
        response.headers["Content-Security-Policy"] = _build_csp_header()
    return response


def _load_error_quotes() -> list[dict]:
    global error_quotes_mtime, error_quotes_cache
    try:
        current_mtime = os.path.getmtime(ERROR_QUOTES_PATH)
    except OSError:
        return FALLBACK_ERROR_QUOTES

    with error_quotes_lock:
        if error_quotes_cache and error_quotes_mtime == current_mtime:
            return error_quotes_cache
        try:
            with open(ERROR_QUOTES_PATH, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            error_quotes_cache = FALLBACK_ERROR_QUOTES
            error_quotes_mtime = current_mtime
            return error_quotes_cache

        loaded_quotes = []
        raw_quotes = payload.get("quotes") if isinstance(payload, dict) else payload
        if isinstance(raw_quotes, list):
            for item in raw_quotes:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                author = str(item.get("author") or "Unknown").strip()
                loaded_quotes.append({"text": text, "author": author})

        error_quotes_cache = loaded_quotes or FALLBACK_ERROR_QUOTES
        error_quotes_mtime = current_mtime
        return error_quotes_cache


def _pick_error_quote() -> dict:
    quotes = _load_error_quotes()
    if not quotes:
        return {"text": "System error channel is active.", "author": "Nebula Core"}
    return random.choice(quotes)


def _wants_json_error() -> bool:
    if request.path.startswith("/api/"):
        return True
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    if best == "application/json":
        return request.accept_mimetypes[best] >= request.accept_mimetypes["text/html"]
    return False


def _error_meta(code: int) -> dict:
    base = ERROR_PAGE_META.get(code)
    if base:
        return base
    return {
        "title": "Unexpected Error",
        "subtitle": "The request entered an unclassified failure state.",
        "hint": "Return to dashboard and retry.",
    }


def _render_error_page(status_code: int, description: str | None = None):
    meta = _error_meta(status_code)
    detail = (description or "").strip() or meta["subtitle"]
    if _wants_json_error():
        return jsonify({
            "detail": detail,
            "code": f"http_{status_code}",
            "status": status_code,
        }), status_code

    quote = _pick_error_quote()
    return render_template(
        "error.html",
        error_status=status_code,
        error_title=meta["title"],
        error_subtitle=meta["subtitle"],
        error_hint=meta["hint"],
        error_detail=detail,
        error_quote=quote.get("text", ""),
        error_quote_author=quote.get("author", "Unknown"),
        request_path=request.path,
    ), status_code


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
        deploy_payload = dict(payload or {})
        requested_project_ids = deploy_payload.pop("project_ids", [])
        if not isinstance(requested_project_ids, list):
            requested_project_ids = []

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
            json_data=deploy_payload
        )

        if code >= 400:
            detail = res.get("detail") if isinstance(res, dict) else str(res)
            if isinstance(detail, dict):
                summary = detail.get("summary") or detail.get("title") or "Deployment failed"
                hint = detail.get("hint") or ""
                raw = detail.get("raw_error") or str(detail)
                _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Deployment failed: {summary}")
                if hint:
                    _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Hint: {hint}")
                _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Raw error: {raw}")
                error_payload = {
                    "title": detail.get("title") or "Deployment Error",
                    "summary": summary,
                    "hint": hint,
                    "code": detail.get("code") or "deploy_failed",
                    "raw_error": raw,
                }
            else:
                error_text = str(detail or "Deployment failed")
                _append_deploy_log(job_id, f"[{time.strftime('%H:%M:%S')}] Deployment failed: {error_text}")
                error_payload = {
                    "title": "Deployment Error",
                    "summary": error_text,
                    "hint": "",
                    "code": "deploy_failed",
                    "raw_error": error_text,
                }
            _update_deploy_job(
                job_id,
                status="failed",
                stage="Deployment failed",
                progress=100,
                error=error_payload,
                result=None
            )
            return

        deployed_id = res.get("id") if isinstance(res, dict) else None
        if deployed_id and requested_project_ids:
            try:
                link_result = link_container_to_projects(
                    container_id=str(deployed_id),
                    project_ids=requested_project_ids,
                    actor=started_by,
                )
                linked = len(link_result.get("linked") or [])
                skipped = len(link_result.get("skipped") or [])
                missing = len(link_result.get("missing") or [])
                archived = len(link_result.get("archived") or [])
                _append_deploy_log(
                    job_id,
                    f"[{time.strftime('%H:%M:%S')}] Project linking: linked={linked}, skipped={skipped}, missing={missing}, archived={archived}",
                )
            except Exception as link_exc:
                _append_deploy_log(
                    job_id,
                    f"[{time.strftime('%H:%M:%S')}] Warning: project linking failed: {str(link_exc)}",
                )
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
            error={
                "title": "Fatal Deploy Error",
                "summary": str(e),
                "hint": "See raw error log for details.",
                "code": "deploy_fatal",
                "raw_error": str(e),
            },
            result=None
        )

register_pages_routes(app, bridge)
register_container_api_routes(
    app,
    bridge,
    deploy_jobs=deploy_jobs,
    deploy_jobs_lock=deploy_jobs_lock,
    run_deploy_job=_run_deploy_job,
)
register_user_api_routes(app, bridge)
register_projects_api_routes(app, bridge)

@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        client_ip = _resolve_client_ip()
        retry_after = _check_login_block(client_ip)
        if retry_after > 0:
            response = jsonify({"detail": f"Too many login attempts. Try again in {retry_after}s"})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429

        username = request.form.get('username')
        password = request.form.get('password')
        otp = (request.form.get('otp') or '').strip()
        # Always drop previous GUI session before a new login attempt to
        # prevent stale identity reuse after failed authentication.
        session.clear()
        db_name, user_type = bridge.resolve_user_sector(username)
        if not db_name:
            _register_failed_login_attempt(client_ip)
            return jsonify({"detail": "User not found"}), 401
        if user_type == 'staff':
            success, error = bridge.admin_auth(username, password, otp=otp)
        else:
            success, error = bridge.user_auth(username, password, db_name, otp=otp)
        if success:
            _clear_login_attempts(client_ip)
            return jsonify({"status": "success", "redirect": url_for('dashboard')})
        _register_failed_login_attempt(client_ip)
        return jsonify({"detail": error}), 401
    return render_template('userlogin.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/api/auth/login', methods=['POST'])
def user_login_api():
    client_ip = _resolve_client_ip()
    retry_after = _check_login_block(client_ip)
    if retry_after > 0:
        response = jsonify({"detail": f"Too many login attempts. Try again in {retry_after}s"})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    username = request.form.get('username')
    password = request.form.get('password')
    otp = (request.form.get('otp') or '').strip()
    db_name = request.form.get('db_name', 'system.db')
    success, error = bridge.user_auth(username, password, db_name, otp=otp)
    if success:
        _clear_login_attempts(client_ip)
        return jsonify({"status": "success", "redirect": url_for('dashboard')}), 200
    _register_failed_login_attempt(client_ip)
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

@app.route('/api/roles/list')
@bridge.login_required
def api_roles_list():
    res, code = bridge.proxy_request("GET", "/roles/list")
    return jsonify(res), code

@app.route('/api/roles/create', methods=['POST'])
@bridge.login_required
@bridge.staff_required
def api_roles_create():
    res, code = bridge.proxy_request("POST", "/roles/create", json_data=request.json)
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

    cache_key = f"{session.get('core_session', '')}:{session.get('user_id', '')}:{int(bool(session.get('is_staff')))}"
    now = time.time()
    with metrics_cache_lock:
        cached = metrics_cache.get(cache_key)
        if cached and (now - cached["ts"]) <= METRICS_CACHE_TTL:
            return jsonify(cached["payload"])

    is_staff = bool(session.get('is_staff'))
    summary, summary_code = bridge.proxy_request("GET", "/containers/summary", timeout=2.5)
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

        payload = {
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
        }
        with metrics_cache_lock:
            metrics_cache[cache_key] = {"ts": now, "payload": payload}
        return jsonify(payload)

    data = None
    core_metrics, core_metrics_code = bridge.proxy_request("GET", "/metrics/current", timeout=2.5)
    if core_metrics_code < 400 and isinstance(core_metrics, dict):
        data = core_metrics
    if not isinstance(data, dict) or not data:
        data = bridge.fetch_metrics(grpc_timeout=0.6, http_timeout=2.5)
    if isinstance(data, dict) and isinstance(data.get("system"), dict):
        data = data.get("system")
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

    payload = {
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
    }
    with metrics_cache_lock:
        metrics_cache[cache_key] = {"ts": now, "payload": payload}
    return jsonify(payload)

@app.route('/api/admin/dashboard-metrics')
@bridge.login_required
@bridge.staff_required
def api_admin_dashboard_metrics():
    res, code = bridge.proxy_request("GET", "/metrics/admin/dashboard", timeout=3.5)
    return jsonify(res), code

@app.route('/api/userpanel/overview')
@bridge.login_required
def api_userpanel_overview():
    username = session.get('user_id', 'unknown')
    db_name = session.get('db_name', 'system.db')
    is_staff = bool(session.get('is_staff'))
    role_tag = session.get('role_tag') or ('admin' if is_staff else 'user')

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

    if not is_staff:
        role_res, role_code = bridge.proxy_request(
            "GET",
            "/users/identity-tag",
            params={"username": username, "db_name": db_name},
        )
        if role_code < 400 and isinstance(role_res, dict):
            fetched = str(role_res.get("role_tag") or "").strip().lower()
            if fetched:
                role_tag = fetched
                session['role_tag'] = fetched

    return jsonify({
        "username": username,
        "db_name": db_name,
        "is_staff": is_staff,
        "role_tag": role_tag,
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
                if isinstance(data, dict) and data.get("type") == "history":
                    socketio.emit("log_update", data, to="staff")
                elif isinstance(data, list):
                    socketio.emit("log_update", {"type": "history", "data": data}, to="staff")
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


@app.errorhandler(HTTPException)
def handle_http_exception(exc):
    return _render_error_page(exc.code or 500, getattr(exc, "description", None))


@app.errorhandler(Exception)
def handle_unexpected_exception(exc):
    logging.getLogger("nebula_gui_flask").exception("Unhandled exception: %s", exc)
    return _render_error_page(500, "Unexpected server exception")


app.register_error_handler(
    SeeOtherDisplayException,
    lambda exc: _render_error_page(303, getattr(exc, "description", None)),
)

if __name__ == '__main__':
    logging.getLogger("nebula_gui_flask").info("Starting Nebula GUI panel")
    socketio.run(app, host='127.0.0.1', port=5000, debug=False, use_reloader=False)
