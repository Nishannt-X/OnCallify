#!/usr/bin/env bash
# tomcat.sh start|stop|status|deploy <war>
# Runs our private Tomcat instance (CATALINA_BASE) on top of the system install.
set -euo pipefail
# shellcheck source=env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

wait_http() { # wait_http <url> <seconds>
  local url="$1" deadline=$((SECONDS + $2))
  until curl -fsS -o /dev/null "$url" 2>/dev/null; do
    (( SECONDS < deadline )) || return 1
    sleep 1
  done
}

case "${1:-}" in
  start)
    # any HTTP answer (even 404) means something already listens on the port
    if curl -sS -o /dev/null "http://127.0.0.1:$TOMCAT_PORT/" 2>/dev/null; then
      echo "tomcat already listening on $TOMCAT_PORT"; exit 0
    fi
    CATALINA_PID="$CATALINA_BASE/tomcat.pid" "$CATALINA_HOME/bin/catalina.sh" start >/dev/null
    for _ in $(seq 1 60); do
      curl -sS -o /dev/null "http://127.0.0.1:$TOMCAT_PORT/" 2>/dev/null && { echo "tomcat up on $TOMCAT_PORT"; exit 0; }
      sleep 1
    done
    echo "tomcat did not come up; see $CATALINA_BASE/logs/catalina.out" >&2; exit 1
    ;;
  stop)
    CATALINA_PID="$CATALINA_BASE/tomcat.pid" "$CATALINA_HOME/bin/catalina.sh" stop 20 -force >/dev/null 2>&1 || true
    echo "tomcat stopped"
    ;;
  status)
    curl -sS -o /dev/null -w "tomcat http %{http_code}\n" "http://127.0.0.1:$TOMCAT_PORT/" || echo "tomcat down"
    ;;
  deploy)
    war="${2:?usage: tomcat.sh deploy <war>}"
    manager_pw="$(cat "$ONCALLIFY_VAULT/tomcat-manager")"
    # the manager text API deploys synchronously and answers "OK - ..." or "FAIL - ..."
    out="$(curl -sS -u "oncallify-runbook:$manager_pw" -T "$war" \
      "http://127.0.0.1:$TOMCAT_PORT/manager/text/deploy?path=/ledgerline&update=true")"
    echo "$out"
    [[ "$out" == OK* ]] || exit 1
    if wait_http "$LEDGERLINE_URL/api/health" 60; then
      echo "ledgerline deployed: $LEDGERLINE_URL"
    else
      echo "ledgerline deployed but not healthy; see $LEDGERLINE_LOG_DIR" >&2; exit 1
    fi
    ;;
  *)
    echo "usage: $0 start|stop|status|deploy <war>" >&2; exit 2
    ;;
esac
