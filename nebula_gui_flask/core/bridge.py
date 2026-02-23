# nebula_gui_flask/core/bridge.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

import requests
import socket
import os
from urllib.parse import urlparse
from flask import session, redirect, url_for, jsonify, abort
from functools import wraps
from .internal_grpc_client import InternalGrpcClient


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
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for candidate in [
        os.path.join(project_root, ".env"),
        os.path.join(project_root, "install", ".env"),
    ]:
        val = _read_env_value(candidate, "NEBULA_INSTALLER_TOKEN")
        if val:
            return val
    return ""


class NebulaBridge:
    def __init__(self, ports=[8000, 8080]):
        self.core_url = self._detect_core(ports)
        self.grpc_target = self._resolve_grpc_target()
        self.grpc_client = InternalGrpcClient(
            target=self.grpc_target,
            token=_resolve_internal_auth_key(),
        )

    def _detect_core(self, ports):
        for port in ports:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    candidate = f"http://127.0.0.1:{port}"
                    # Avoid false-positive socket matches; confirm target looks like Nebula Core.
                    probe = requests.get(f"{candidate}/system/status", timeout=1.5)
                    if probe.status_code == 200:
                        data = probe.json()
                        if isinstance(data, dict) and "status" in data and "system" in data:
                            return candidate
            except (OSError, ConnectionRefusedError):
                continue
            except Exception:
                continue
        return "http://127.0.0.1:8000"

    def _resolve_grpc_target(self):
        explicit = os.getenv("NEBULA_CORE_GRPC_TARGET")
        if explicit:
            return explicit
        grpc_port = int(os.getenv("NEBULA_CORE_GRPC_PORT", "50051"))
        parsed = urlparse(self.core_url)
        host = parsed.hostname or "127.0.0.1"
        return f"{host}:{grpc_port}"

    def login_required(self, f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session or not session.get("core_session"):
                return redirect(url_for('admin_login'))
            return f(*args, **kwargs)
        return decorated_function

    def staff_required(self, f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('is_staff'):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function

    def fetch_metrics(self, grpc_timeout: float = 1.0, http_timeout: float = 5.0):
        grpc_data = self.grpc_client.get_current_metrics(timeout=grpc_timeout)
        if grpc_data:
            return grpc_data
        try:
            r = requests.get(f"{self.core_url}/metrics/current", timeout=http_timeout)
            if r.status_code == 200:
                return r.json()

            fallback = requests.get(f"{self.core_url}/system/status", timeout=http_timeout)
            if fallback.status_code == 200:
                return fallback.json().get('system')
            return None
        except Exception:
            return None

    def admin_auth(self, admin_id, secure_key, otp=None):
        try:
            payload = {"admin_id": admin_id, "secure_key": secure_key}
            if otp:
                payload["otp"] = otp
            r = requests.post(
                f"{self.core_url}/system/internal/core/login",
                data=payload,
                allow_redirects=False,
                timeout=5
            )
            if r.status_code in [200, 303]:
                core_session = r.cookies.get("nebula_session")
                if not core_session:
                    return False, "Core session not established"
                session.permanent = True
                session['user_id'] = admin_id
                session['is_staff'] = True
                session['db_name'] = 'system.db'
                session['role_tag'] = 'admin'
                session['core_session'] = core_session
                return True, None
            try:
                detail = r.json().get("detail", "INVALID_ACCESS_KEY")
            except Exception:
                detail = "INVALID_ACCESS_KEY"
            return False, detail
        except Exception as e:
            return False, str(e)

    def resolve_user_sector(self, username):
        try:
            r = requests.get(
                f"{self.core_url}/system/lookup", 
                params={"username": username}, 
                timeout=3
            )
            if r.status_code == 200:
                data = r.json()
                return data.get('db_name'), data.get('type') 
            return None, None
        except:
            return None, None

    def user_auth(self, username, password, db_name, otp=None):
        try:
            payload = {"username": username, "password": password}
            if otp:
                payload["otp"] = otp
            r = requests.post(
                f"{self.core_url}/users/login",
                params={"db_name": db_name},
                data=payload,
                timeout=5
            )
            if r.status_code == 200:
                core_session = r.cookies.get("nebula_session")
                if not core_session:
                    return False, "Core session not established"
                role_tag = self._resolve_role_tag(core_session, username, db_name) or "user"
                session.permanent = True
                session['user_id'] = username
                session['is_staff'] = False
                session['db_name'] = db_name
                session['role_tag'] = role_tag
                session['core_session'] = core_session
                return True, None
            try:
                detail = r.json().get("detail", "INVALID_CREDENTIALS")
            except Exception:
                detail = "INVALID_CREDENTIALS"
            return False, detail
        except Exception as e:
            return False, str(e)

    def _resolve_role_tag(self, core_session, username, db_name):
        try:
            r = requests.get(
                f"{self.core_url}/users/identity-tag",
                params={"username": username, "db_name": db_name},
                cookies={"nebula_session": core_session},
                timeout=4,
            )
            if r.status_code == 200:
                tag = str((r.json() or {}).get("role_tag") or "").strip().lower()
                if tag:
                    return tag
        except Exception:
            pass
        return None

    def proxy_request(self, method, endpoint, params=None, json_data=None, form_data=None, timeout=10):
        if method == "GET" and endpoint == "/logs/history":
            limit = 200
            try:
                if params and params.get("limit") is not None:
                    limit = int(params.get("limit"))
            except Exception:
                limit = 200
            grpc_data = self.grpc_client.get_log_history(limit=limit, timeout=1.0)
            if isinstance(grpc_data, list):
                return grpc_data, 200

        if method == "GET" and endpoint == "/metrics/current":
            grpc_data = self.grpc_client.get_current_metrics(timeout=1.0)
            if isinstance(grpc_data, dict):
                return grpc_data, 200

        url = f"{self.core_url}{endpoint}"
        core_session = session.get("core_session")
        if not core_session:
            return {"detail": "No active core session"}, 401
        cookies = {"nebula_session": core_session}
        
        try:
            r = requests.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                data=form_data,
                cookies=cookies,
                timeout=timeout
            )
            try:
                return r.json(), r.status_code
            except:
                return {"detail": r.text}, r.status_code
        except Exception as e:
            return {"detail": f"Core Connection Error: {str(e)}"}, 500
