# NebulaCore Optimization Audit (2026-02-12)

## Scope
- Backend runtime and event model (`nebula_core/core`)
- Metrics and arithmetic paths (`nebula_core/api/metrics.py`, `nebula_core/core/system_info.py`)
- Docker orchestration hot paths (`nebula_core/services/docker_service.py`)
- GUI/Core bridge (`nebula_gui_flask/core/bridge.py`)

## Implemented In This Pass

### 1) Runtime config loading fixed
- `nebula_core/utils/config.py`
  - `load_yaml_config()` now accepts an explicit path and safely returns `{}` for empty YAML.
- `nebula_core/core/runtime.py`
  - Added robust config path resolution (CWD and module-relative fallback).
  - Runtime now correctly reads `services.*` section from `serviceconfig.yaml`.
  - Service `enabled` flags are now respected.

Impact:
- Service intervals and toggles from YAML are actually applied.
- Runtime behavior is deterministic across different launch directories.

### 2) EventBus execution semantics fixed
- `nebula_core/core/events.py`
  - `emit()` now awaits listener execution directly via `asyncio.gather(...)`.
  - Removed extra `create_task(...)` layer to reduce overhead and race conditions.
  - Added compatibility alias `on(...) -> subscribe(...)`.

Impact:
- `await event_bus.emit(...)` now guarantees listener completion.
- Lower task churn and fewer timing bugs.

### 3) Metrics event subscription fixed
- `nebula_core/api/metrics.py`
  - Removed broken direct `event_bus.on(...)` binding at import time.
  - Added lazy one-time async subscription via `subscribe(...)` on first request.

Impact:
- Metrics listener binding is now valid and safe with current runtime lifecycle.

### 4) Non-blocking system CPU metric
- `nebula_core/core/system_info.py`
  - Replaced `psutil.cpu_percent(interval=0.5)` with `interval=None`.

Impact:
- `/system/status` no longer blocks ~500ms per request.

### 5) Docker summary short TTL cache
- `nebula_core/services/docker_service.py`
  - Added per-user/per-role cache for usage summary with `1.0s` TTL.

Impact:
- Frequent UI polling significantly reduces repeated expensive Docker stats calls.

### 6) Core auto-detection hardened in GUI bridge
- `nebula_gui_flask/core/bridge.py`
  - Removed GUI port `5000` from Core probe list.
  - Added `/system/status` validation to confirm endpoint is Nebula Core.

Impact:
- Avoids false-positive connection to non-Core service.

## Remaining High-Priority Items

1. Remove double Docker lookup in container ACL path.
- Today `_can_access_container()` resolves full ID, then service methods fetch container again.
- Candidate files: `nebula_core/api/containers.py`, `nebula_core/services/docker_service.py`.

2. Build indexed user-location map instead of DB directory scan.
- Today `/system/lookup` linearly scans all client DB files.
- Candidate file: `nebula_core/api/system.py`.

3. Normalize metric units and rounding policy.
- Use consistent units (MiB/s vs MB/s).
- Keep raw floats in backend, round only in presentation layer.
- Candidate files: `nebula_core/services/docker_service.py`, `nebula_core/api/metrics.py`, `nebula_gui_flask/app.py`.

4. Replace polling loop in logs websocket with push-based dispatch.
- Current `/logs/stream` loop sleeps continuously.
- Candidate file: `nebula_core/api/logs.py`.

## Validation Checklist
- Start Core and verify runtime loads `nebula_core/serviceconfig.yaml`.
- Toggle `services.metrics.enabled` and confirm service startup behavior.
- Hit `/metrics/current` and confirm no subscription errors.
- Load GUI dashboard and verify metrics still render.
- Check repeated `/containers/summary` requests for lower response time after warm-up.
