# nebula_gui_flask/routes/api_containers.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import threading
import time
import uuid

from flask import jsonify, request, session


def register_container_api_routes(app, bridge, deploy_jobs, deploy_jobs_lock, run_deploy_job):
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

    @app.route('/api/containers/presets')
    @bridge.login_required
    def api_container_presets():
        res, code = bridge.proxy_request("GET", "/containers/presets")
        return jsonify(res), code

    @app.route('/api/containers/presets/<preset_name>')
    @bridge.login_required
    def api_container_preset_detail(preset_name):
        res, code = bridge.proxy_request("GET", f"/containers/presets/{preset_name}")
        return jsonify(res), code

    @app.route('/api/containers/presets', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_container_preset_save():
        res, code = bridge.proxy_request("POST", "/containers/presets", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/containers/permissions/<container_id>')
    @bridge.login_required
    def api_container_permissions_get(container_id):
        res, code = bridge.proxy_request("GET", f"/containers/permissions/{container_id}")
        return jsonify(res), code

    @app.route('/api/containers/permissions/<container_id>', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_container_permissions_update(container_id):
        res, code = bridge.proxy_request("POST", f"/containers/permissions/{container_id}", json_data=request.json)
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
            target=run_deploy_job,
            args=(job_id, payload, started_by, core_session),
            daemon=True,
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
                    "status": "active",
                }
            ],
            "active_node": "nebula-core-local",
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
