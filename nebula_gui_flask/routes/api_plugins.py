# nebula_gui_flask/routes/api_plugins.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from flask import jsonify, request


def register_plugins_api_routes(app, bridge):
    @app.route('/api/plugins/list')
    @bridge.login_required
    @bridge.staff_required
    def api_plugins_list():
        res, code = bridge.proxy_request("GET", "/system/plugins")
        plugins = (res or {}).get("plugins", []) if isinstance(res, dict) else []
        return jsonify({"plugins": plugins}), code

    @app.route('/api/plugins/rescan', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_plugins_rescan():
        res, code = bridge.proxy_request("POST", "/system/plugins/rescan")
        return jsonify(res), code

    @app.route('/api/plugins/<plugin_name>/health')
    @bridge.login_required
    @bridge.staff_required
    def api_plugins_health(plugin_name):
        res, code = bridge.proxy_request("GET", f"/system/plugins/{plugin_name}/health")
        return jsonify(res), code

    @app.route('/api/plugins/<plugin_name>/stats')
    @bridge.login_required
    @bridge.staff_required
    def api_plugins_stats(plugin_name):
        res, code = bridge.proxy_request("GET", f"/system/plugins/{plugin_name}/stats")
        return jsonify(res), code

    @app.route('/api/plugins/<plugin_name>/logs')
    @bridge.login_required
    @bridge.staff_required
    def api_plugins_logs(plugin_name):
        tail = request.args.get("tail", "200")
        res, code = bridge.proxy_request("GET", f"/system/plugins/{plugin_name}/logs", params={"tail": tail})
        return jsonify(res), code

    @app.route('/api/plugins/<plugin_name>/action', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_plugins_action(plugin_name):
        action = str((request.json or {}).get("action") or "").strip().lower()
        res, code = bridge.proxy_request(
            "POST",
            f"/system/plugins/{plugin_name}/action",
            json_data={"action": action},
        )
        return jsonify(res), code
