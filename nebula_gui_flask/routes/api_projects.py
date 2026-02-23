# nebula_gui_flask/routes/api_projects.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)

from flask import jsonify, request

_PROJECTS_BRIDGE = None


def _proxy(bridge, method: str, endpoint: str, *, params=None, json_data=None):
    res, code = bridge.proxy_request(method, endpoint, params=params, json_data=json_data)
    return jsonify(res), code


def link_container_to_projects(container_id: str, project_ids: list[str], actor: str) -> dict:
    bridge = _PROJECTS_BRIDGE
    if bridge is None:
        raise RuntimeError("Projects bridge is not initialized")

    payload = {
        "container_id": str(container_id or "").strip(),
        "project_ids": project_ids if isinstance(project_ids, list) else [],
        "actor": str(actor or "").strip(),
    }
    res, code = bridge.proxy_request("POST", "/projects/link-container-bulk", json_data=payload)
    if code >= 400:
        detail = res.get("detail") if isinstance(res, dict) else str(res)
        raise RuntimeError(str(detail or "Project linking failed"))
    if not isinstance(res, dict):
        raise RuntimeError("Invalid response from core projects API")
    return res


def register_projects_api_routes(app, bridge):
    global _PROJECTS_BRIDGE
    _PROJECTS_BRIDGE = bridge

    @app.route('/api/projects/health')
    @bridge.login_required
    @bridge.staff_required
    def api_projects_health():
        return _proxy(bridge, "GET", "/projects/health")

    @app.route('/api/projects')
    @bridge.login_required
    def api_projects_list():
        tab = str(request.args.get("tab") or "active").strip().lower()
        return _proxy(bridge, "GET", "/projects", params={"tab": tab})

    @app.route('/api/projects/active')
    @bridge.login_required
    @bridge.staff_required
    def api_projects_active():
        return _proxy(bridge, "GET", "/projects/active")

    @app.route('/api/projects/containers/available')
    @bridge.login_required
    @bridge.staff_required
    def api_projects_available_containers():
        return _proxy(bridge, "GET", "/projects/containers/available")

    @app.route('/api/projects', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_projects_create():
        return _proxy(bridge, "POST", "/projects", json_data=request.json)

    @app.route('/api/projects/<project_id>', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_projects_update(project_id):
        return _proxy(bridge, "POST", f"/projects/{project_id}", json_data=request.json)

    @app.route('/api/projects/<project_id>/archive', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_projects_archive(project_id):
        return _proxy(bridge, "POST", f"/projects/{project_id}/archive")

    @app.route('/api/projects/<project_id>/restore', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_projects_restore(project_id):
        return _proxy(bridge, "POST", f"/projects/{project_id}/restore")

    @app.route('/api/projects/<project_id>/containers/link', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_projects_link_container(project_id):
        return _proxy(bridge, "POST", f"/projects/{project_id}/containers/link", json_data=request.json)

    @app.route('/api/projects/<project_id>/containers/unlink', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_projects_unlink_container(project_id):
        return _proxy(bridge, "POST", f"/projects/{project_id}/containers/unlink", json_data=request.json)
