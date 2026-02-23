# nebula_core/api/projects.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import json
import os
import re
import sqlite3
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..db import SYSTEM_DB, get_connection
from ..services.docker_service import DockerService
from .security import require_session

router = APIRouter(prefix="/projects", tags=["Projects"])
docker_service = DockerService()

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LEGACY_PROJECTS_JSON_PATH = os.path.join(PROJECT_ROOT, "storage", "projects", "projects.json")

PROJECTS_DB_INIT_LOCK = threading.Lock()
PROJECTS_DB_READY = False
PROJECTS_HEALTH_LOCK = threading.Lock()
PROJECTS_STORAGE_HEALTH = {
    "status": "unknown",
    "ready": False,
    "writable": False,
    "db_path": SYSTEM_DB,
    "last_checked_at": 0,
    "last_error": "",
}

CONTAINER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,190}$")


def _now_ts() -> int:
    return int(time.time())


def _session_from_request(request: Request):
    return require_session(request)


def _require_staff(is_staff: bool) -> None:
    if not is_staff:
        raise HTTPException(status_code=403, detail="Staff clearance required")


def _ensure_projects_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            archived INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_containers (
            project_id TEXT NOT NULL,
            container_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system',
            PRIMARY KEY(project_id, container_id),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_archived ON projects(archived)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_containers_container_id ON project_containers(container_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_audit_project ON project_audit_log(project_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_audit_actor ON project_audit_log(actor, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_audit_action ON project_audit_log(action, created_at DESC)")


def _read_legacy_projects_json() -> list[dict]:
    if not os.path.exists(LEGACY_PROJECTS_JSON_PATH):
        return []
    try:
        with open(LEGACY_PROJECTS_JSON_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return []
    return projects


def _normalize_legacy_project(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    project_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not project_id or not name:
        return None

    tags_raw = raw.get("tags")
    tags = []
    if isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]

    container_ids_raw = raw.get("container_ids")
    container_ids = []
    if isinstance(container_ids_raw, list):
        for item in container_ids_raw:
            cid = str(item or "").strip()
            if cid and cid not in container_ids:
                container_ids.append(cid)

    created_at = int(raw.get("created_at") or _now_ts())
    updated_at = int(raw.get("updated_at") or created_at)

    return {
        "id": project_id,
        "name": name,
        "description": str(raw.get("description") or "").strip(),
        "tags": tags,
        "container_ids": container_ids,
        "archived": 1 if bool(raw.get("archived")) else 0,
        "created_at": created_at,
        "updated_at": updated_at,
        "created_by": str(raw.get("created_by") or "system"),
    }


def _migrate_legacy_json_if_needed(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS c FROM projects").fetchone()
    has_projects = bool(row and int(row["c"] or 0) > 0)
    if has_projects:
        return

    legacy_projects = _read_legacy_projects_json()
    if not legacy_projects:
        return

    for raw in legacy_projects:
        project = _normalize_legacy_project(raw)
        if not project:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO projects (id, name, description, tags_json, archived, created_at, updated_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["id"],
                project["name"],
                project["description"],
                json.dumps(project["tags"], ensure_ascii=True),
                int(project["archived"]),
                int(project["created_at"]),
                int(project["updated_at"]),
                project["created_by"],
            ),
        )
        for container_id in project["container_ids"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO project_containers (project_id, container_id, created_at, created_by)
                VALUES (?, ?, ?, ?)
                """,
                (
                    project["id"],
                    container_id,
                    int(project["updated_at"]),
                    project["created_by"],
                ),
            )


def _append_audit_log(conn: sqlite3.Connection, project_id: str, action: str, actor: str, details: dict | None = None) -> None:
    payload = details if isinstance(details, dict) else {}
    conn.execute(
        """
        INSERT INTO project_audit_log (project_id, action, actor, details_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(project_id or ""),
            str(action or "unknown"),
            str(actor or "system"),
            json.dumps(payload, ensure_ascii=True),
            _now_ts(),
        ),
    )


def _set_projects_storage_health(status: str, ready: bool, writable: bool, error: str = "") -> None:
    with PROJECTS_HEALTH_LOCK:
        PROJECTS_STORAGE_HEALTH["status"] = status
        PROJECTS_STORAGE_HEALTH["ready"] = bool(ready)
        PROJECTS_STORAGE_HEALTH["writable"] = bool(writable)
        PROJECTS_STORAGE_HEALTH["last_checked_at"] = _now_ts()
        PROJECTS_STORAGE_HEALTH["last_error"] = str(error or "")


def _db_error_payload(exc: Exception) -> tuple[dict, int]:
    message = str(exc).lower()
    if "readonly" in message:
        return {"detail": "Projects storage is read-only", "code": "projects_storage_readonly"}, 503
    if "locked" in message or "busy" in message:
        return {"detail": "Projects storage is temporarily locked", "code": "projects_storage_locked"}, 503
    return {"detail": "Projects storage failure", "code": "projects_storage_error"}, 500


def _raise_db_error(exc: Exception):
    payload, status = _db_error_payload(exc)
    _set_projects_storage_health(
        status="degraded",
        ready=False,
        writable=False,
        error=str(exc),
    )
    raise HTTPException(status_code=status, detail=payload.get("detail") or "Projects storage failure")


def _refresh_projects_storage_health() -> None:
    try:
        _ensure_projects_ready()
        with get_connection(SYSTEM_DB) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _projects_write_probe (id INTEGER PRIMARY KEY, touched_at INTEGER NOT NULL)"
            )
            conn.execute("INSERT INTO _projects_write_probe (touched_at) VALUES (?)", (_now_ts(),))
            conn.execute("ROLLBACK")
        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
    except sqlite3.Error as exc:
        payload, _ = _db_error_payload(exc)
        _set_projects_storage_health(
            status="degraded",
            ready=False,
            writable=False,
            error=payload.get("detail") or str(exc),
        )
    except Exception as exc:
        _set_projects_storage_health(
            status="degraded",
            ready=False,
            writable=False,
            error=str(exc),
        )


def _ensure_projects_ready() -> None:
    global PROJECTS_DB_READY
    if PROJECTS_DB_READY:
        return
    with PROJECTS_DB_INIT_LOCK:
        if PROJECTS_DB_READY:
            return
        with get_connection(SYSTEM_DB) as conn:
            _ensure_projects_schema(conn)
            _migrate_legacy_json_if_needed(conn)
        PROJECTS_DB_READY = True
        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")


def _validate_project_name(name: str) -> str | None:
    if not name:
        return "Project name is required"
    if len(name) < 2:
        return "Project name must be at least 2 characters"
    if len(name) > 96:
        return "Project name is too long"
    return None


def _normalize_tags(raw_tags) -> list[str]:
    if isinstance(raw_tags, str):
        parts = [part.strip() for part in raw_tags.split(",")]
        return [p for p in parts if p]
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags]
        return [t for t in tags if t]
    return []


def _normalize_project_ids(raw) -> list[str]:
    values = raw if isinstance(raw, list) else []
    out = []
    for item in values:
        pid = str(item or "").strip()
        if not pid:
            continue
        if pid not in out:
            out.append(pid)
    return out


def _normalize_container_ids(raw) -> list[str]:
    values = raw if isinstance(raw, list) else []
    out = []
    for item in values:
        cid = str(item or "").strip()
        if not cid:
            continue
        if not CONTAINER_ID_PATTERN.match(cid):
            continue
        if cid not in out:
            out.append(cid)
    return out


def _parse_tags_json(raw_tags_json) -> list[str]:
    if not raw_tags_json:
        return []
    try:
        parsed = json.loads(raw_tags_json)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(t).strip() for t in parsed if str(t).strip()]


def _container_label(container: dict) -> str:
    return str(container.get("name") or container.get("id") or container.get("full_id") or "unknown")


def _container_status(container: dict) -> str:
    return str(container.get("status") or "unknown").strip().lower() or "unknown"


def _container_identity_keys(container: dict) -> set[str]:
    keys = set()
    for key in ["id", "full_id", "name"]:
        value = str(container.get(key) or "").strip()
        if value:
            keys.add(value)
    return keys


def _match_project_containers(project_container_ids: list[str], visible_containers: list[dict]) -> list[dict]:
    if not project_container_ids:
        return []

    matched = []
    pending_ids = [str(cid).strip() for cid in project_container_ids if str(cid).strip()]
    for container in visible_containers:
        identity_keys = _container_identity_keys(container)
        if not identity_keys:
            continue
        if any(cid in identity_keys for cid in pending_ids):
            matched.append(container)
    return matched


def _collect_users_from_containers(containers: list[dict], assignments_by_container: dict[str, list[dict]] | None = None) -> list[dict]:
    bucket = {}
    for container in containers:
        container_key = str(container.get("full_id") or container.get("id") or "").strip()
        assigned_users = []
        if assignments_by_container and container_key:
            assigned_users = assignments_by_container.get(container_key) or []

        if assigned_users:
            for item in assigned_users:
                username = str(item.get("username") or "").strip()
                db_name = str(item.get("db_name") or item.get("db") or "system.db").strip() or "system.db"
                if not username:
                    continue
                key = f"{username.lower()}::{db_name.lower()}"
                if key not in bucket:
                    bucket[key] = {"username": username, "db_name": db_name}
            continue

        users = container.get("users")
        if not isinstance(users, list):
            continue
        for raw_user in users:
            username = str(raw_user or "").strip()
            if not username:
                continue
            key = f"{username.lower()}::system.db"
            if key not in bucket:
                bucket[key] = {"username": username, "db_name": "system.db"}

    return sorted(bucket.values(), key=lambda x: (x["username"].lower(), x["db_name"].lower()))


def _compute_project_load(containers: list[dict]) -> dict:
    total_cpu = 0.0
    total_ram_mb = 0.0
    running_total = 0
    containers_total = len(containers)
    for container in containers:
        if str(container.get("status") or "").strip().lower() == "running":
            running_total += 1
        try:
            total_cpu += float(container.get("cpu_percent") or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            total_ram_mb += float(container.get("memory_used_mb") or 0.0)
        except (TypeError, ValueError):
            pass

    return {
        "cpu_percent": round(total_cpu, 2),
        "memory_mb": round(total_ram_mb, 2),
        "running_containers": running_total,
        "total_containers": containers_total,
    }


def _serialize_project_for_client(
    project: dict,
    visible_containers: list[dict],
    is_staff: bool,
    assignments_by_container: dict[str, list[dict]] | None = None,
) -> dict | None:
    linked_containers = _match_project_containers(project.get("container_ids") or [], visible_containers)

    if not is_staff and not linked_containers:
        return None

    users = _collect_users_from_containers(linked_containers, assignments_by_container=assignments_by_container)
    load = _compute_project_load(linked_containers)

    container_payload = []
    for cont in linked_containers:
        container_payload.append({
            "id": str(cont.get("id") or ""),
            "full_id": str(cont.get("full_id") or cont.get("id") or ""),
            "name": _container_label(cont),
            "status": _container_status(cont),
        })

    return {
        "id": project["id"],
        "name": project["name"],
        "description": project.get("description") or "",
        "tags": project.get("tags") or [],
        "archived": bool(project.get("archived")),
        "created_at": int(project.get("created_at") or 0),
        "updated_at": int(project.get("updated_at") or 0),
        "created_by": str(project.get("created_by") or "system"),
        "team": users,
        "containers": container_payload,
        "containers_total": len(container_payload),
        "load": load,
        "can_manage": bool(is_staff),
    }


def _build_container_assignments_map(visible_containers: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    container_keys = []
    for container in visible_containers:
        container_key = str(container.get("full_id") or container.get("id") or "").strip()
        if container_key and container_key not in container_keys:
            container_keys.append(container_key)

    if not container_keys:
        return out

    placeholders = ", ".join("?" for _ in container_keys)
    with get_connection(SYSTEM_DB) as conn:
        rows = conn.execute(
            f"""
            SELECT container_id, username, db_name
            FROM container_permissions
            WHERE container_id IN ({placeholders})
            ORDER BY username ASC
            """,
            tuple(container_keys),
        ).fetchall()

    for row in rows:
        container_id = str(row["container_id"] or "").strip()
        username = str(row["username"] or "").strip()
        if not container_id or not username:
            continue
        out.setdefault(container_id, []).append({
            "username": username,
            "db_name": str(row["db_name"] or "system.db").strip() or "system.db",
        })
    return out


def _fetch_visible_containers(username: str, db_name: str, is_staff: bool) -> list[dict]:
    try:
        containers = docker_service.list_containers(username, db_name, is_staff)
        return containers if isinstance(containers, list) else []
    except Exception:
        return []


def _fetch_projects_from_db(include_archived: bool) -> list[dict]:
    _ensure_projects_ready()
    archived = 1 if include_archived else 0

    with get_connection(SYSTEM_DB) as conn:
        rows = conn.execute(
            """
            SELECT id, name, description, tags_json, archived, created_at, updated_at, created_by
            FROM projects
            WHERE archived = ?
            ORDER BY LOWER(name), id
            """,
            (archived,),
        ).fetchall()
        projects = [
            {
                "id": str(r["id"]),
                "name": str(r["name"]),
                "description": str(r["description"] or ""),
                "tags": _parse_tags_json(r["tags_json"]),
                "archived": int(r["archived"] or 0),
                "created_at": int(r["created_at"] or 0),
                "updated_at": int(r["updated_at"] or 0),
                "created_by": str(r["created_by"] or "system"),
                "container_ids": [],
            }
            for r in rows
        ]

        if not projects:
            return []

        placeholders = ", ".join("?" for _ in projects)
        links = conn.execute(
            f"SELECT project_id, container_id FROM project_containers WHERE project_id IN ({placeholders})",
            tuple(p["id"] for p in projects),
        ).fetchall()

        by_project_id = {p["id"]: p for p in projects}
        for link in links:
            proj = by_project_id.get(str(link["project_id"]))
            if proj is None:
                continue
            cid = str(link["container_id"] or "").strip()
            if cid and cid not in proj["container_ids"]:
                proj["container_ids"].append(cid)

        return projects


def link_container_to_projects(container_id: str, project_ids: list[str], actor: str) -> dict:
    canonical_container_id = str(container_id or "").strip()
    if not canonical_container_id:
        return {"linked": [], "skipped": project_ids, "missing": [], "archived": []}
    normalized_project_ids = _normalize_project_ids(project_ids)
    if not normalized_project_ids:
        return {"linked": [], "skipped": [], "missing": [], "archived": []}

    _ensure_projects_ready()
    linked = []
    skipped = []
    missing = []
    archived = []

    with get_connection(SYSTEM_DB) as conn:
        for project_id in normalized_project_ids:
            row = conn.execute(
                "SELECT id, archived FROM projects WHERE id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                missing.append(project_id)
                continue
            if int(row["archived"] or 0) == 1:
                archived.append(project_id)
                continue
            before = conn.execute(
                "SELECT 1 FROM project_containers WHERE project_id = ? AND container_id = ? LIMIT 1",
                (project_id, canonical_container_id),
            ).fetchone()
            conn.execute(
                """
                INSERT OR IGNORE INTO project_containers (project_id, container_id, created_at, created_by)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, canonical_container_id, _now_ts(), actor),
            )
            conn.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (_now_ts(), project_id),
            )
            if before:
                skipped.append(project_id)
            else:
                linked.append(project_id)
                _append_audit_log(
                    conn,
                    project_id=project_id,
                    action="project.container.link",
                    actor=actor,
                    details={"container_id": canonical_container_id, "source": "container_deploy"},
                )

    _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
    return {
        "linked": linked,
        "skipped": skipped,
        "missing": missing,
        "archived": archived,
    }


@router.get("/health")
def projects_health(request: Request):
    _, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)
    _refresh_projects_storage_health()
    with PROJECTS_HEALTH_LOCK:
        payload = dict(PROJECTS_STORAGE_HEALTH)
    return JSONResponse(payload, status_code=200 if payload.get("status") == "ok" else 503)


@router.get("")
def projects_list(request: Request, tab: str = Query("active")):
    username, db_name, is_staff = _session_from_request(request)

    include_archived = str(tab or "active").strip().lower() == "archived"
    try:
        projects = _fetch_projects_from_db(include_archived=include_archived)
        visible_containers = _fetch_visible_containers(username, db_name, is_staff)
        assignments_by_container = _build_container_assignments_map(visible_containers)

        out = []
        for project in projects:
            serialized = _serialize_project_for_client(
                project,
                visible_containers,
                is_staff,
                assignments_by_container=assignments_by_container,
            )
            if serialized is None:
                continue
            out.append(serialized)

        out.sort(key=lambda item: (str(item.get("name", "")).lower(), item.get("id", "")))
        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
        return {"projects": out, "tab": "archived" if include_archived else "active"}
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.get("/active")
def projects_active(request: Request):
    _, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    try:
        projects = _fetch_projects_from_db(include_archived=False)
        payload = [
            {
                "id": p["id"],
                "name": p["name"],
                "tags": p.get("tags") or [],
                "containers_total": len(p.get("container_ids") or []),
            }
            for p in projects
        ]
        return {"projects": payload}
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.get("/containers/available")
def projects_available_containers(request: Request):
    username, db_name, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    containers = _fetch_visible_containers(username, db_name, is_staff)

    payload = []
    for cont in containers:
        full_id = str(cont.get("full_id") or cont.get("id") or "").strip()
        if not full_id:
            continue
        payload.append({
            "id": str(cont.get("id") or "").strip(),
            "full_id": full_id,
            "name": _container_label(cont),
            "status": _container_status(cont),
        })

    payload.sort(key=lambda c: c["name"].lower())
    return {"containers": payload}


@router.post("")
def projects_create(data: dict, request: Request):
    actor, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    payload = data or {}
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    tags = _normalize_tags(payload.get("tags"))
    initial_container_ids = _normalize_container_ids(payload.get("container_ids"))

    name_error = _validate_project_name(name)
    if name_error:
        raise HTTPException(status_code=400, detail=name_error)

    try:
        _ensure_projects_ready()
        now = _now_ts()

        with get_connection(SYSTEM_DB) as conn:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE archived = 0 AND LOWER(name) = LOWER(?) LIMIT 1",
                (name,),
            ).fetchone()
            if exists:
                raise HTTPException(status_code=409, detail="Project with this name already exists")

            project_id = f"prj_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO projects (id, name, description, tags_json, archived, created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    project_id,
                    name,
                    description,
                    json.dumps(tags, ensure_ascii=True),
                    now,
                    now,
                    actor,
                ),
            )
            for container_id in initial_container_ids:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO project_containers (project_id, container_id, created_at, created_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_id, container_id, now, actor),
                )
            _append_audit_log(
                conn,
                project_id=project_id,
                action="project.create",
                actor=actor,
                details={
                    "name": name,
                    "description": description,
                    "tags": tags,
                    "initial_container_ids": initial_container_ids,
                },
            )

        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
        return JSONResponse(
            {
                "status": "ok",
                "project": {
                    "id": project_id,
                    "name": name,
                    "description": description,
                    "tags": tags,
                    "container_ids": initial_container_ids,
                    "archived": False,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": actor,
                },
            },
            status_code=201,
        )
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.post("/{project_id}")
def projects_update(project_id: str, data: dict, request: Request):
    actor, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    payload = data or {}
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    tags = _normalize_tags(payload.get("tags"))

    name_error = _validate_project_name(name)
    if name_error:
        raise HTTPException(status_code=400, detail=name_error)

    try:
        _ensure_projects_ready()
        now = _now_ts()

        with get_connection(SYSTEM_DB) as conn:
            current = conn.execute(
                "SELECT id, name, description, tags_json, archived FROM projects WHERE id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Project not found")
            if int(current["archived"] or 0) == 1:
                raise HTTPException(status_code=409, detail="Archived project cannot be modified")

            exists = conn.execute(
                """
                SELECT 1 FROM projects
                WHERE id <> ? AND archived = 0 AND LOWER(name) = LOWER(?)
                LIMIT 1
                """,
                (project_id, name),
            ).fetchone()
            if exists:
                raise HTTPException(status_code=409, detail="Project with this name already exists")

            conn.execute(
                """
                UPDATE projects
                SET name = ?, description = ?, tags_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, description, json.dumps(tags, ensure_ascii=True), now, project_id),
            )
            _append_audit_log(
                conn,
                project_id=project_id,
                action="project.update",
                actor=actor,
                details={
                    "before": {
                        "name": str(current["name"] or ""),
                        "description": str(current["description"] or ""),
                        "tags": _parse_tags_json(current["tags_json"]),
                    },
                    "after": {
                        "name": name,
                        "description": description,
                        "tags": tags,
                    },
                },
            )

        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
        return {"status": "ok"}
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.post("/{project_id}/archive")
def projects_archive(project_id: str, request: Request):
    actor, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    try:
        _ensure_projects_ready()
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute("SELECT id, archived FROM projects WHERE id = ? LIMIT 1", (project_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Project not found")
            conn.execute(
                "UPDATE projects SET archived = 1, updated_at = ? WHERE id = ?",
                (_now_ts(), project_id),
            )
            _append_audit_log(
                conn,
                project_id=project_id,
                action="project.archive",
                actor=actor,
                details={"archived_before": bool(row["archived"])},
            )
        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
        return {"status": "ok"}
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.post("/{project_id}/restore")
def projects_restore(project_id: str, request: Request):
    actor, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    try:
        _ensure_projects_ready()
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT id, name, archived FROM projects WHERE id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Project not found")

            conflict = conn.execute(
                """
                SELECT 1 FROM projects
                WHERE id <> ? AND archived = 0 AND LOWER(name) = LOWER(?)
                LIMIT 1
                """,
                (project_id, str(row["name"] or "")),
            ).fetchone()
            if conflict:
                raise HTTPException(status_code=409, detail="Cannot restore: active project with same name exists")

            conn.execute(
                "UPDATE projects SET archived = 0, updated_at = ? WHERE id = ?",
                (_now_ts(), project_id),
            )
            _append_audit_log(
                conn,
                project_id=project_id,
                action="project.restore",
                actor=actor,
                details={"archived_before": bool(row["archived"])},
            )
        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
        return {"status": "ok"}
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.post("/{project_id}/containers/link")
def projects_link_container(project_id: str, data: dict, request: Request):
    actor, db_name, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    payload = data or {}
    container_id = str(payload.get("container_id") or "").strip()
    if not container_id:
        raise HTTPException(status_code=400, detail="container_id is required")
    if not CONTAINER_ID_PATTERN.match(container_id):
        raise HTTPException(status_code=400, detail="Invalid container_id")

    containers = _fetch_visible_containers(actor, db_name, True)
    valid_container_keys = set()
    canonical_map = {}
    for cont in containers:
        full_id = str(cont.get("full_id") or cont.get("id") or "").strip()
        short_id = str(cont.get("id") or "").strip()
        name = str(cont.get("name") or "").strip()
        if not full_id:
            continue
        valid_container_keys.add(full_id)
        canonical_map[full_id] = full_id
        if short_id:
            valid_container_keys.add(short_id)
            canonical_map[short_id] = full_id
        if name:
            valid_container_keys.add(name)
            canonical_map[name] = full_id

    if container_id not in valid_container_keys:
        raise HTTPException(status_code=404, detail="Container not found among available containers")

    canonical_id = canonical_map.get(container_id, container_id)

    try:
        _ensure_projects_ready()
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT id, archived FROM projects WHERE id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Project not found")
            if int(row["archived"] or 0) == 1:
                raise HTTPException(status_code=409, detail="Cannot link container to archived project")

            conn.execute(
                """
                INSERT OR IGNORE INTO project_containers (project_id, container_id, created_at, created_by)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, canonical_id, _now_ts(), actor),
            )
            conn.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (_now_ts(), project_id),
            )
            _append_audit_log(
                conn,
                project_id=project_id,
                action="project.container.link",
                actor=actor,
                details={"container_id": canonical_id},
            )

        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
        return {"status": "ok", "container_id": canonical_id}
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.post("/{project_id}/containers/unlink")
def projects_unlink_container(project_id: str, data: dict, request: Request):
    actor, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    payload = data or {}
    container_id = str(payload.get("container_id") or "").strip()
    if not container_id:
        raise HTTPException(status_code=400, detail="container_id is required")

    try:
        _ensure_projects_ready()
        with get_connection(SYSTEM_DB) as conn:
            row = conn.execute(
                "SELECT id, archived FROM projects WHERE id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Project not found")
            if int(row["archived"] or 0) == 1:
                raise HTTPException(status_code=409, detail="Cannot unlink container from archived project")

            conn.execute(
                "DELETE FROM project_containers WHERE project_id = ? AND container_id = ?",
                (project_id, container_id),
            )
            conn.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (_now_ts(), project_id),
            )
            _append_audit_log(
                conn,
                project_id=project_id,
                action="project.container.unlink",
                actor=actor,
                details={"container_id": container_id},
            )

        _set_projects_storage_health(status="ok", ready=True, writable=True, error="")
        return {"status": "ok"}
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        _raise_db_error(exc)


@router.post("/link-container-bulk")
def projects_link_container_bulk(data: dict, request: Request):
    actor, _, is_staff = _session_from_request(request)
    _require_staff(is_staff)

    payload = data or {}
    container_id = str(payload.get("container_id") or "").strip()
    project_ids = payload.get("project_ids")
    if not isinstance(project_ids, list):
        raise HTTPException(status_code=400, detail="project_ids must be an array")

    try:
        return link_container_to_projects(container_id=container_id, project_ids=project_ids, actor=actor)
    except sqlite3.Error as exc:
        _raise_db_error(exc)
