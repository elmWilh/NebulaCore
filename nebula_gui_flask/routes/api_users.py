from flask import jsonify, request, session


def register_user_api_routes(app, bridge):
    @app.route('/api/users/databases')
    @bridge.login_required
    @bridge.staff_required
    def api_proxy_databases():
        res, code = bridge.proxy_request("GET", "/users/databases")
        return jsonify(res), code

    @app.route('/api/users/list')
    @bridge.login_required
    @bridge.staff_required
    def api_proxy_user_list():
        res, code = bridge.proxy_request("GET", "/users/list", params={"db_name": request.args.get('db_name')})
        return jsonify(res), code

    @app.route('/api/users/identity-tag')
    @bridge.login_required
    def api_user_identity_tag_get():
        params = {
            "username": request.args.get("username"),
            "db_name": request.args.get("db_name"),
        }
        res, code = bridge.proxy_request("GET", "/users/identity-tag", params=params)
        return jsonify(res), code

    @app.route('/api/users/identity-tag', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_user_identity_tag_set():
        res, code = bridge.proxy_request("POST", "/users/identity-tag", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/users/create', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_proxy_create_user():
        res, code = bridge.proxy_request(
            "POST",
            "/users/create",
            params={"db_name": request.args.get('db_name')},
            json_data=request.json,
        )
        return jsonify(res), code

    @app.route('/api/users/update', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_proxy_update_user():
        res, code = bridge.proxy_request("POST", "/users/update", json_data=request.json)
        return jsonify(res), code

    @app.route('/api/users/delete', methods=['POST'])
    @bridge.login_required
    @bridge.staff_required
    def api_proxy_delete_user():
        params = {
            "db_name": request.args.get('db_name'),
            "username": request.args.get('username'),
        }
        res, code = bridge.proxy_request("POST", "/users/delete", params=params)
        return jsonify(res), code

    @app.route('/api/users/detail/<username>')
    @bridge.login_required
    def api_user_detail(username):
        if not session.get('is_staff') and session.get('user_id') != username:
            return jsonify({"detail": "Access Denied"}), 403
        db_name = request.args.get('db_name')
        params = {"db_name": db_name} if db_name else None
        res, code = bridge.proxy_request("GET", f"/users/detail/{username}", params=params)
        return jsonify(res), code
