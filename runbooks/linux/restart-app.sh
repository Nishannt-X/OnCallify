#!/usr/bin/env bash
# restart_app: reload the application context through the Tomcat manager text API.
# Frees leaked connections and leaked heap; in-flight requests fail for a few seconds.
RB_ACTION=restart_app
# shellcheck source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
parse_args --app -- "$@"
require_env TOMCAT_URL ONCALLIFY_VAULT

list="$(manager_get list)" || precondition_failed "Tomcat manager is not reachable at $TOMCAT_URL"
line="$(grep -E "^/$RB_APP:" <<<"$list" || true)"
[[ -n "$line" ]] || precondition_failed "context /$RB_APP is not deployed"
state="$(cut -d: -f2 <<<"$line")"
before="$(json_obj context "/$RB_APP" state "$state")"

if $RB_DRY_RUN; then
  result 0 true false "would reload /$RB_APP (currently $state) via the Tomcat manager" "$before"
fi

out="$(manager_get "reload?path=/$RB_APP")" || execution_failed "manager call failed"
[[ "$out" == OK* ]] || execution_failed "$out"
after_state="$(manager_get list | grep -E "^/$RB_APP:" | cut -d: -f2 || true)"
result 0 true true "$out" "$before" "$(json_obj context "/$RB_APP" state "$after_state")"
