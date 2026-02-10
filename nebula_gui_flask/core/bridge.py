# nebula_gui_flask/core/bridge.py
import requests
import socket
from flask import session, redirect, url_for, jsonify
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
            if 'user_id' not in session or not session.get('is_staff'):
                return redirect(url_for('admin_login'))
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
                return True, None
            return False, "INVALID_ACCESS_KEY"
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