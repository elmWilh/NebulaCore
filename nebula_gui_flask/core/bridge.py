import requests
import socket
from flask import session, redirect, url_for, jsonify, abort
from functools import wraps

class NebulaBridge:
    def __init__(self, ports=[8000, 8080]):
        self.core_url = self._detect_core(ports)

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

    def fetch_metrics(self):
        try:
            r = requests.get(f"{self.core_url}/metrics/current", timeout=5)
            if r.status_code == 200:
                return r.json()

            fallback = requests.get(f"{self.core_url}/system/status", timeout=5)
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

    def proxy_request(self, method, endpoint, params=None, json_data=None, form_data=None):
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
                timeout=10
            )
            try:
                return r.json(), r.status_code
            except:
                return {"detail": r.text}, r.status_code
        except Exception as e:
            return {"detail": f"Core Connection Error: {str(e)}"}, 500
