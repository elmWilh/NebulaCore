import requests
import socket
from flask import session, redirect, url_for, jsonify, abort
from functools import wraps

class NebulaBridge:
    def __init__(self, ports=[8000, 8080, 5000]):
        self.core_url = self._detect_core(ports)

    def _detect_core(self, ports):
        for port in ports:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    return f"http://127.0.0.1:{port}"
            except (OSError, ConnectionRefusedError):
                continue
        return "http://127.0.0.1:8000"

    def login_required(self, f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
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
            return r.json() if r.status_code == 200 else None
        except:
            return None

    def admin_auth(self, admin_id, secure_key):
        try:
            r = requests.post(
                f"{self.core_url}/system/internal/core/login",
                data={"admin_id": admin_id, "secure_key": secure_key},
                allow_redirects=False,
                timeout=5
            )
            if r.status_code in [200, 303]:
                session.permanent = True
                session['user_id'] = admin_id
                session['is_staff'] = True
                session['db_name'] = 'system.db'
                return True, None
            return False, "INVALID_ACCESS_KEY"
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

    def user_auth(self, username, password, db_name):
        try:
            r = requests.post(
                f"{self.core_url}/users/login",
                params={"db_name": db_name},
                data={"username": username, "password": password},
                timeout=5
            )
            if r.status_code == 200:
                session.permanent = True
                session['user_id'] = username
                session['is_staff'] = False
                session['db_name'] = db_name
                return True, None
            return False, "INVALID_CREDENTIALS"
        except Exception as e:
            return False, str(e)

    def proxy_request(self, method, endpoint, params=None, json_data=None, form_data=None):
        url = f"{self.core_url}{endpoint}"
        try:
            r = requests.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                data=form_data,
                timeout=5
            )
            return r.json(), r.status_code
        except Exception as e:
            return {"detail": str(e)}, 500