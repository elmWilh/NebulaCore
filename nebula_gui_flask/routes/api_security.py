import requests

from flask import Response, jsonify, request, session


def register_security_api_routes(app, bridge):
    @app.route('/api/security/overview')
    @bridge.login_required
    @bridge.staff_required
    def api_security_overview():
        res, code = bridge.proxy_request("GET", "/security/overview", params={"db_name": request.args.get("db_name", "system.db")})
        return jsonify(res), code

    @app.route('/api/security/users')
    @bridge.login_required
    @bridge.staff_required
    def api_security_users():
        res, code = bridge.proxy_request("GET", "/security/users", params={"db_name": request.args.get("db_name", "system.db")})
        return jsonify(res), code

    @app.route('/api/security/users/<username>/history')
    @bridge.login_required
    @bridge.staff_required
    def api_security_user_history(username):
        res, code = bridge.proxy_request(
            "GET",
            f"/security/users/{username}/history",
            params={"db_name": request.args.get("db_name", "system.db")},
        )
        return jsonify(res), code

    @app.route('/api/security/permissions')
    @bridge.login_required
    @bridge.staff_required
    def api_security_permissions():
        res, code = bridge.proxy_request("GET", "/security/permissions")
        return jsonify(res), code

    @app.route('/api/security/permissions', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_security_permissions_upsert():
        res, code = bridge.proxy_request("POST", "/security/permissions", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/security/roles')
    @bridge.login_required
    @bridge.staff_required
    def api_security_roles():
        res, code = bridge.proxy_request("GET", "/security/roles")
        return jsonify(res), code

    @app.route('/api/security/roles/<role_name>/permissions', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_security_role_permissions(role_name):
        res, code = bridge.proxy_request("POST", f"/security/roles/{role_name}/permissions", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/security/groups')
    @bridge.login_required
    @bridge.staff_required
    def api_security_groups():
        res, code = bridge.proxy_request("GET", "/security/groups")
        return jsonify(res), code

    @app.route('/api/security/groups', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_security_groups_upsert():
        res, code = bridge.proxy_request("POST", "/security/groups", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/security/groups/<group_name>/members', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_security_group_members(group_name):
        res, code = bridge.proxy_request("POST", f"/security/groups/{group_name}/members", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/security/groups/<group_name>/containers', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_security_group_containers(group_name):
        res, code = bridge.proxy_request("POST", f"/security/groups/{group_name}/containers", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/security/audit/users')
    @bridge.login_required
    @bridge.staff_required
    def api_security_audit_users():
        params = {
            "limit": request.args.get("limit", "100"),
            "username": request.args.get("username", ""),
            "db_name": request.args.get("db_name", ""),
            "risk_level": request.args.get("risk_level", ""),
        }
        res, code = bridge.proxy_request("GET", "/security/audit/users", params=params)
        return jsonify(res), code

    @app.route('/api/security/audit/connections')
    @bridge.login_required
    @bridge.staff_required
    def api_security_audit_connections():
        params = {
            "limit": request.args.get("limit", "100"),
            "username": request.args.get("username", ""),
            "service_name": request.args.get("service_name", ""),
            "risk_level": request.args.get("risk_level", ""),
        }
        res, code = bridge.proxy_request("GET", "/security/audit/connections", params=params)
        return jsonify(res), code

    @app.route('/api/security/audit/export')
    @bridge.login_required
    @bridge.staff_required
    def api_security_audit_export():
        core_session = session.get("core_session")
        if not core_session:
            session.clear()
            return jsonify({"detail": "SESSION_EXPIRED"}), 401
        resp = requests.get(
            f"{bridge.core_url}/security/audit/export",
            params={
                "kind": request.args.get("kind", "users"),
                "limit": request.args.get("limit", "1000"),
            },
            cookies={"nebula_session": core_session},
            timeout=20,
        )
        if resp.status_code >= 400:
            try:
                return jsonify(resp.json()), resp.status_code
            except Exception:
                return jsonify({"detail": resp.text}), resp.status_code
        return Response(
            resp.text,
            status=resp.status_code,
            mimetype="text/csv",
            headers={"Content-Disposition": resp.headers.get("Content-Disposition", "attachment; filename=nebula-audit.csv")},
        )
