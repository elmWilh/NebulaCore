# nebula_gui_flask/routes/pages.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import json
from pathlib import Path

from flask import jsonify, render_template, session


def register_pages_routes(app, bridge):
    def _discover_locales():
        locales_dir = Path(app.static_folder) / "locales"
        catalog = []
        if not locales_dir.exists():
            return catalog

        for locale_path in sorted(locales_dir.glob("*.json")):
            try:
                payload = json.loads(locale_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            meta = payload.get("_meta") if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}

            code = str(meta.get("code") or locale_path.stem).strip().lower()
            if not code:
                continue

            catalog.append(
                {
                    "code": code,
                    "name": str(meta.get("name") or code.upper()),
                    "native_name": str(meta.get("native_name") or meta.get("name") or code.upper()),
                    "crowdin_locale": str(meta.get("crowdin_locale") or code),
                    "is_default": bool(meta.get("is_default")),
                    "path": f"/static/locales/{locale_path.name}",
                }
            )

        return catalog

    def _render_module_page(title: str, description: str, icon: str):
        return render_template(
            'pages/module_stub.html',
            module_title=title,
            module_description=description,
            module_icon=icon,
        )

    @app.route('/api/i18n/catalog')
    def i18n_catalog():
        locales = _discover_locales()
        return jsonify(
            {
                "default_locale": next((item["code"] for item in locales if item.get("is_default")), "en"),
                "locales": locales,
            }
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
            is_staff=bool(session.get('is_staff')),
        )

    @app.route('/logs')
    @bridge.login_required
    @bridge.staff_required
    def logs_page():
        return render_template('pages/logs.html')

    @app.route('/projects')
    @bridge.login_required
    def projects_page():
        return render_template('pages/projects.html')

    @app.route('/settings')
    @bridge.login_required
    def settings_page():
        return render_template(
            'pages/settings.html',
            settings_user_id=session.get('user_id'),
            settings_is_staff=bool(session.get('is_staff')),
            settings_role_tag=session.get('role_tag') or 'user',
        )

    @app.route('/databases')
    @bridge.login_required
    def databases_page():
        return _render_module_page(
            title='Databases',
            description='Overview of database instances, health, and storage usage.',
            icon='bi-database',
        )

    @app.route('/task-scheduler')
    @bridge.login_required
    @bridge.staff_required
    def task_scheduler_page():
        return _render_module_page(
            title='Task Scheduler',
            description='Configure recurring jobs and monitor execution status.',
            icon='bi-calendar2-check',
        )

    @app.route('/backups')
    @bridge.login_required
    @bridge.staff_required
    def backups_page():
        return _render_module_page(
            title='Backups',
            description='Manage backup plans, retention policies, and restore points.',
            icon='bi-hdd-stack',
        )

    @app.route('/plugins')
    @bridge.login_required
    @bridge.staff_required
    def plugins_page():
        return render_template('pages/plugins.html')

    @app.route('/audit-log')
    @bridge.login_required
    @bridge.staff_required
    def audit_log_page():
        return render_template('pages/audit_log.html')

    @app.route('/access-control')
    @bridge.login_required
    @bridge.staff_required
    def access_control_page():
        return render_template('pages/access_control.html')

    @app.route('/fault-sentinel')
    @bridge.login_required
    @bridge.staff_required
    def fault_sentinel_page():
        return _render_module_page(
            title='Fault Sentinel',
            description='Future module for automated anomaly and failure detection.',
            icon='bi-activity',
        )
