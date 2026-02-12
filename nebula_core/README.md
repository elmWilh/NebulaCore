# nebula_core

`nebula_core` — backend-ядро Nebula Panel на FastAPI.

## Что делает компонент

- API для пользователей, ролей, контейнеров и системных операций.
- Интеграция с Docker для deploy/start/stop/restart/logs/delete.
- Агрегация метрик сервера и пользовательских контейнеров.
- Внутренние сервисы (heartbeat, file service, metrics service).

## Точки входа

- Модульный запуск: `python -m nebula_core`
- ASGI entrypoint: `nebula_core.main:app`

## Важные каталоги

- `api/` — HTTP и WebSocket API.
- `services/` — сервисы бизнес-логики (docker, metrics, files, users).
- `core/` — runtime, event bus, lifecycle.
- `db.py` и `db/` — доступ к SQLite и схемы.

## Безопасность

- Для production рекомендуется запуск за reverse proxy.
- Ограничивайте `NEBULA_CORS_ORIGINS` и внешнюю доступность порта Core.
- Используйте `NEBULA_INSTALLER_TOKEN` и ротируйте его.
