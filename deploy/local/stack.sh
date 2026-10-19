#!/usr/bin/env bash
# stack.sh up|down|status: run the whole local stack.
#   Tomcat 10 + Ledgerline (Java)  -> :8080
#   gateway (Node/Express)          -> :7070
#   agent API + collector (Python)  -> :7080 (also serves the dashboard if it is built)
# Prerequisites: deploy/local/setup.sh has run; ledgerline/target/ledgerline.war and
# gateway/dist exist (make build).
set -euo pipefail
# shellcheck source=env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

pids="$ONCALLIFY_RUN/pids"
logs="$ONCALLIFY_RUN/logs"
mkdir -p "$pids" "$logs"

token_file="$ONCALLIFY_VAULT/ingest-token"
[[ -s "$token_file" ]] || { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$token_file"; chmod 600 "$token_file"; }
ONCALLIFY_INGEST_TOKEN="$(cat "$token_file")"
export ONCALLIFY_INGEST_TOKEN
export ONCALLIFY_APPROVERS="${ONCALLIFY_APPROVERS:-$ONCALLIFY_RUN/approvers.json}"
# incidents, approvals and contracts live in PostgreSQL (database created by setup.sh)
if [[ -z "${ONCALLIFY_STORE:-}" && -s "$ONCALLIFY_VAULT/oncallify-db" ]]; then
  ONCALLIFY_STORE="postgresql://oncallify:$(cat "$ONCALLIFY_VAULT/oncallify-db")@$PG_HOST:$PG_PORT/oncallify"
fi
export ONCALLIFY_STORE="${ONCALLIFY_STORE:-memory}"
export ONCALLIFY_WEB_DIST="${ONCALLIFY_WEB_DIST:-$ONCALLIFY_ROOT/web/dist}"
export GATEWAY_URL="http://127.0.0.1:$GATEWAY_PORT"
export AGENT_URL="http://127.0.0.1:$AGENT_PORT"

start_bg() { # start_bg <name> <cmd...>
  local name="$1"; shift
  if [[ -f "$pids/$name" ]] && kill -0 "$(cat "$pids/$name")" 2>/dev/null; then
    echo "$name already running (pid $(cat "$pids/$name"))"; return
  fi
  nohup "$@" >>"$logs/$name.log" 2>&1 &
  echo $! > "$pids/$name"
  echo "$name started (pid $!, log $logs/$name.log)"
}

wait_http() { # wait_http <url> <seconds>
  local deadline=$((SECONDS + $2))
  until curl -fsS -o /dev/null "$1" 2>/dev/null; do
    (( SECONDS < deadline )) || { echo "timed out waiting for $1" >&2; return 1; }
    sleep 1
  done
}

stop_bg() {
  local name="$1"
  if [[ -f "$pids/$name" ]]; then
    kill "$(cat "$pids/$name")" 2>/dev/null || true
    rm -f "$pids/$name"
    echo "$name stopped"
  fi
}

case "${1:-}" in
  up)
    "$(dirname "${BASH_SOURCE[0]}")/tomcat.sh" start
    if ! curl -fsS -o /dev/null "$LEDGERLINE_URL/api/health" 2>/dev/null; then
      "$(dirname "${BASH_SOURCE[0]}")/tomcat.sh" deploy "$ONCALLIFY_ROOT/ledgerline/target/ledgerline.war"
    fi
    GATEWAY_RULES="$ONCALLIFY_ROOT/gateway/rules.json" start_bg gateway node "$ONCALLIFY_ROOT/gateway/dist/server.js"
    wait_http "$GATEWAY_URL/health" 30
    start_bg agent python3 -m oncallify.cli serve --collect
    wait_http "$AGENT_URL/api/health" 60
    echo "dashboard: $AGENT_URL   ledgerline: $LEDGERLINE_URL   gateway: $GATEWAY_URL"
    ;;
  down)
    stop_bg agent
    stop_bg gateway
    "$(dirname "${BASH_SOURCE[0]}")/tomcat.sh" stop
    ;;
  status)
    "$(dirname "${BASH_SOURCE[0]}")/tomcat.sh" status
    for n in gateway agent; do
      if [[ -f "$pids/$n" ]] && kill -0 "$(cat "$pids/$n")" 2>/dev/null; then echo "$n up"; else echo "$n down"; fi
    done
    ;;
  env)
    # print exports for an interactive shell: eval "$(deploy/local/stack.sh env)"
    printf 'export ONCALLIFY_INGEST_TOKEN=%q ONCALLIFY_APPROVERS=%q GATEWAY_URL=%q AGENT_URL=%q\n' \
      "$ONCALLIFY_INGEST_TOKEN" "$ONCALLIFY_APPROVERS" "$GATEWAY_URL" "$AGENT_URL"
    ;;
  *)
    echo "usage: $0 up|down|status|env" >&2; exit 2
    ;;
esac
