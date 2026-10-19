#!/usr/bin/env bash
# RB_* values are set here and read by the runbooks that source this file.
# shellcheck disable=SC2034
#
# Shared helpers for OnCallify runbooks. Sourced, never executed.
#
# Contract (identical for the PowerShell runbooks in ../windows):
#   args    --app NAME [--commit SHA] [--size N] [--dry-run]
#   stdout  one JSON object on the last line: ok, action, dry_run, changed, detail, before, after
#   exit    0 ok | 2 usage | 3 precondition failed | 4 execution failed
#
# Environment:
#   LEDGERLINE_CONF_DIR           config git repository (holds ledgerline.properties)
#   LEDGERLINE_SECRET_FILE        the app's copy of its database password
#   ONCALLIFY_VAULT               directory holding the CURRENT secrets
#   TOMCAT_URL                    e.g. http://127.0.0.1:8080
#   TOMCAT_MANAGER_USER           default oncallify-runbook
#   TOMCAT_MANAGER_PASSWORD_FILE  default $ONCALLIFY_VAULT/tomcat-manager
#   RUNBOOK_ACTOR                 commit author for config changes, default oncallify-runbook

set -euo pipefail

RB_ACTION="${RB_ACTION:-unknown}"
RB_APP=""
RB_COMMIT=""
RB_SIZE=""
RB_DRY_RUN=false
RB_KNOWN_APPS="ledgerline"
RB_CONFIG_FILE_NAME="ledgerline.properties"
RB_ACTOR="${RUNBOOK_ACTOR:-oncallify-runbook}"

json_str() { # json_str <text>: JSON string literal with escaping
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '"%s"' "$s"
}

json_obj() { # json_obj key value [key value ...]: flat object of string values
  local out="{" first=true
  while (( $# >= 2 )); do
    $first || out+=","
    first=false
    out+="$(json_str "$1"):$(json_str "$2")"
    shift 2
  done
  printf '%s}' "$out"
}

# result <exit_code> <ok> <changed> <detail> [before_json] [after_json]
result() {
  local code="$1" ok="$2" changed="$3" detail="$4" before="${5:-{\}}" after="${6:-{\}}"
  printf '{"ok":%s,"action":%s,"dry_run":%s,"changed":%s,"detail":%s,"before":%s,"after":%s}\n' \
    "$ok" "$(json_str "$RB_ACTION")" "$RB_DRY_RUN" "$changed" "$(json_str "$detail")" "$before" "$after"
  exit "$code"
}

usage_error() { result 2 false false "usage: $1"; }
precondition_failed() { result 3 false false "precondition failed: $1"; }
execution_failed() { result 4 false false "execution failed: $1"; }

parse_args() { # parse_args <allowed flags...> -- "$@"
  local allowed=()
  while (( $# )); do
    if [[ "$1" == "--" ]]; then shift; break; fi
    allowed+=("$1"); shift
  done
  while (( $# )); do
    local flag="$1"
    case "$flag" in
      --dry-run) RB_DRY_RUN=true; shift; continue ;;
      --app|--commit|--size) ;;
      *) usage_error "unknown argument $flag" ;;
    esac
    local ok=false
    for a in "${allowed[@]}"; do [[ "$a" == "$flag" ]] && ok=true; done
    $ok || usage_error "$flag is not accepted by $RB_ACTION"
    (( $# >= 2 )) || usage_error "$flag needs a value"
    case "$flag" in
      --app) RB_APP="$2" ;;
      --commit) RB_COMMIT="$2" ;;
      --size) RB_SIZE="$2" ;;
    esac
    shift 2
  done
  [[ -n "$RB_APP" ]] || usage_error "--app is required"
  [[ " $RB_KNOWN_APPS " == *" $RB_APP "* ]] || usage_error "unknown app $RB_APP"
}

require_env() {
  local name
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || precondition_failed "environment variable $name is not set"
  done
}

config_file() { printf '%s/%s' "$LEDGERLINE_CONF_DIR" "$RB_CONFIG_FILE_NAME"; }

prop_get() { # prop_get <key>: value from the config file, empty if absent
  local key="$1" file
  file="$(config_file)"
  awk -v k="$key" -F= '
    /^[[:space:]]*[#!]/ { next }
    { kk=$1; gsub(/^[[:space:]]+|[[:space:]]+$/, "", kk) }
    kk == k { v=substr($0, index($0, "=") + 1); gsub(/^[[:space:]]+|[[:space:]]+$/, "", v); print v; exit }
  ' "$file"
}

# prop_set_commit <key> <value> <message>: atomic edit (temp file + rename) then git commit
prop_set_commit() {
  local key="$1" value="$2" message="$3" file tmp
  file="$(config_file)"
  tmp="$(mktemp "$LEDGERLINE_CONF_DIR/.ledgerline.XXXXXX")"
  awk -v k="$key" -v v="$value" -F= '
    BEGIN { done=0 }
    /^[[:space:]]*[#!]/ { print; next }
    { kk=$1; gsub(/^[[:space:]]+|[[:space:]]+$/, "", kk) }
    kk == k { print k "=" v; done=1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file" > "$tmp"
  chmod --reference="$file" "$tmp" 2>/dev/null || chmod 644 "$tmp"
  mv "$tmp" "$file"
  git_commit "$message"
}

git_cfg() { git -C "$LEDGERLINE_CONF_DIR" -c "user.name=$RB_ACTOR" -c "user.email=$RB_ACTOR@oncallify.local" "$@"; }

git_commit() {
  git_cfg add "$RB_CONFIG_FILE_NAME" >/dev/null
  git_cfg commit -q -m "$1" >/dev/null
  git -C "$LEDGERLINE_CONF_DIR" rev-parse --short=12 HEAD
}

sha256_of() { # portable: sha256sum (Linux) or shasum (macOS)
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

manager_get() { # manager_get <path?query>: GET the Tomcat manager text API
  local pw_file="${TOMCAT_MANAGER_PASSWORD_FILE:-$ONCALLIFY_VAULT/tomcat-manager}"
  [[ -r "$pw_file" ]] || precondition_failed "manager password file $pw_file is not readable"
  curl -sS --max-time 60 -u "${TOMCAT_MANAGER_USER:-oncallify-runbook}:$(cat "$pw_file")" \
    "$TOMCAT_URL/manager/text/$1"
}
