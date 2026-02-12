# Nebula Panel

Nebula Panel — это платформа управления инфраструктурой и контейнерами с разделением прав между администраторами и обычными пользователями.

## Архитектура

- `nebula_core` (FastAPI): API, управление пользователями и ролями, оркестрация Docker, системные и контейнерные метрики.
- `nebula_gui_flask` (Flask): веб-интерфейс для авторизации, управления пользователями, контейнерами и мониторинга.

## Ключевые возможности

- Авторизация администраторов и пользователей.
- Разделение доступа к контейнерам:
  - админ видит весь сервер и весь пул контейнеров;
  - пользователь видит только назначенные ему контейнеры.
- Управление контейнерами:
  - deploy c прогрессом и логом развёртки;
  - start/stop/restart;
  - просмотр логов контейнера;
  - удаление контейнера с подтверждением.
- Система ролей и пользователей (SQLite).
- Метрики:
  - для админа — метрики всего сервера;
  - для пользователя — агрегированные метрики только его контейнеров.
- Базовые security-ограничения на критичные API (users/roles/files/logs).

## Хранилище данных

- `storage/databases/system.db` — системная БД (администраторы, системные права).
- `storage/databases/clients/*.db` — клиентские БД пользователей.

## Быстрый старт (Ubuntu)

### 1. Зависимости

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl
```

### 2. Подготовка проекта

```bash
export PROJECT_DIR=/opt/NebulaCore
cd "$PROJECT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r nebula_gui_flask/requirements.txt
```

### 3. Docker

Вариант A (через инсталлятор проекта):

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python install/main.py
```

Вариант B (ручной):

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
rm get-docker.sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
newgrp docker
docker info
```

### 4. Запуск Core

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python -m nebula_core
```

### 5. Первичная инициализация администратора

```bash
cd "$PROJECT_DIR"
source .venv/bin/activate
python install/main.py
```

В меню выберите:

- `Run First-Time Setup / Create Admin`

### 6. Запуск GUI

В отдельном терминале:

```bash
cd "$PROJECT_DIR/nebula_gui_flask"
source ../.venv/bin/activate
python app.py
```

Открыть в браузере:

- `http://127.0.0.1:5000`

## Основные URL

- GUI: `http://127.0.0.1:5000`
- Core API: `http://127.0.0.1:8000`

## Безопасность (рекомендации для production)

- Не держите Core открытым наружу без reverse proxy и firewall.
- Задайте окружение и секреты через `.env`:
  - `NEBULA_INSTALLER_TOKEN`
  - `NEBULA_CORS_ORIGINS`
  - `NEBULA_CORE_HOST`
  - `NEBULA_CORE_PORT`
  - `NEBULA_CORE_RELOAD=false`
- Используйте HTTPS на внешнем периметре.
- Регулярно ротируйте токены и пароли администраторов.

## Описание возможностей по ролям

### Администратор

- Управление пользователями, ролями и назначениями контейнеров.
- Полный доступ к операциям контейнеров.
- Просмотр системных метрик всего узла.
- Доступ к административным логам.

### Пользователь

- Просмотр только назначенных контейнеров.
- Операции с назначенными контейнерами (в рамках выданных прав интерфейса).
- Просмотр только своих агрегированных метрик.

## Будущее проекта

Планируемые направления:

- Полноценная модель авторизации с подписанными server-side сессиями/JWT.
- Расширенная RBAC/ABAC-модель с тонкими правами на операции контейнеров.
- Аудит-трассировка действий (кто/когда/что изменил).
- Встроенные лимиты и политики (quota) по CPU/RAM/Storage на пользователя и группу.
- Поддержка нескольких узлов/кластеров с real-time статусами.
- Улучшенный observability-слой (алерты, графики, retention, экспорт в Prometheus).
- Миграция на отказоустойчивый backend-хранилище конфигурации.

## Лицензия

См. файл `LICENSE`.
