#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${NEBULA_CORE_SERVICE:-nebula-core}"
LINES="${NEBULA_LOG_LINES:-120}"

usage() {
  cat <<USAGE
Usage: $0 <command> [service]

Commands:
  install      Install/update systemd service via installer
  start        Start core service
  stop         Stop core service
  restart      Restart core service
  status       Show service status
  logs         Show recent logs from journald

Examples:
  $0 install
  $0 restart
  $0 logs
  $0 status nebula-core
USAGE
}

cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  usage
  exit 1
fi

if [[ "${2:-}" != "" ]]; then
  SERVICE_NAME="$2"
fi

case "$cmd" in
  install)
    python3 install/main.py --core-service-install --core-service-name "$SERVICE_NAME"
    ;;
  start|stop|restart|status)
    python3 install/main.py --core-service-action "$cmd" --core-service-name "$SERVICE_NAME"
    ;;
  logs)
    python3 install/main.py --core-service-action logs --core-service-name "$SERVICE_NAME" --core-service-log-lines "$LINES"
    ;;
  *)
    usage
    exit 2
    ;;
esac
