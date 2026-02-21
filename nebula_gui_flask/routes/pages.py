# nebula_gui_flask/routes/pages.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from flask import render_template, session


def register_pages_routes(app, bridge):
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
