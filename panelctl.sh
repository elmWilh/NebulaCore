#!/usr/bin/env bash
set -euo pipefail

CORE_SERVICE="${NEBULA_CORE_SERVICE:-nebula-core}"
GUI_SERVICE="${NEBULA_GUI_SERVICE:-nebula-gui}"
CORE_LOG_LINES="${NEBULA_CORE_LOG_LINES:-120}"
GUI_LOG_LINES="${NEBULA_GUI_LOG_LINES:-120}"

usage() {
  cat <<USAGE
Usage: $0 <command>

Commands:
  install      Run the guided installer
  start        Start both Core and GUI services
  stop         Stop both Core and GUI services
  restart      Restart both Core and GUI services
  status       Show status for both Core and GUI services
  logs         Show recent logs for both Core and GUI services

Environment overrides:
  NEBULA_CORE_SERVICE
  NEBULA_GUI_SERVICE
USAGE
}

run_py() {
  python3 install/main.py "$@"
}

run_for_both() {
  local action="$1"
  run_py --core-service-action "$action" --core-service-name "$CORE_SERVICE"
  run_py --gui-service-action "$action" --gui-service-name "$GUI_SERVICE"
}

cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  usage
  exit 1
fi

case "$cmd" in
  install)
    run_py --easy-install --core-service-name "$CORE_SERVICE" --gui-service-name "$GUI_SERVICE"
    ;;
  start|stop|restart|status)
    run_for_both "$cmd"
    ;;
  logs)
    run_py --core-service-action logs --core-service-name "$CORE_SERVICE" --core-service-log-lines "$CORE_LOG_LINES"
    run_py --gui-service-action logs --gui-service-name "$GUI_SERVICE" --gui-service-log-lines "$GUI_LOG_LINES"
    ;;
  *)
    usage
    exit 2
    ;;
esac
