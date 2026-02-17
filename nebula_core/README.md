# nebula_core

`nebula_core` is the FastAPI backend engine of Nebula Panel.

## What This Component Does

- Provides API endpoints for users, roles, containers, and system operations.
- Integrates with Docker for deploy/start/stop/restart/logs/delete.
- Aggregates host metrics and user container metrics.
- Runs internal services (heartbeat, file service, metrics service).

## Entry Points

- Module launch: `python -m nebula_core`
- ASGI entrypoint: `nebula_core.main:app`

## Important Directories

- `api/` - HTTP and WebSocket API.
- `services/` - business-logic services (docker, metrics, files, users).
- `core/` - runtime, event bus, lifecycle.
- `db.py` and `db/` - SQLite access and schema handling.

## Security

- For production, run behind a reverse proxy.
- Restrict `NEBULA_CORS_ORIGINS` and external Core port exposure.
- Use and rotate `NEBULA_INSTALLER_TOKEN`.
