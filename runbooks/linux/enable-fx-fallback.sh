#!/usr/bin/env bash
# enable_fx_fallback: turn on the cached-rate fallback flag (committed to config).
RB_ACTION=enable_fx_fallback
# shellcheck source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
parse_args --app -- "$@"
require_env LEDGERLINE_CONF_DIR
[[ -f "$(config_file)" ]] || precondition_failed "config file $(config_file) not found"

current="$(prop_get fx.fallback.enabled)"
before="$(json_obj fx.fallback.enabled "${current:-false}")"
if [[ "$current" == "true" ]]; then
  result 0 true false "fx fallback is already enabled" "$before" "$before"
fi
if $RB_DRY_RUN; then
  result 0 true false "would set fx.fallback.enabled ${current:-false} -> true" "$before"
fi
sha="$(prop_set_commit fx.fallback.enabled true "enable fx fallback (oncallify)")" \
  || execution_failed "could not commit config change"
result 0 true true "fx.fallback.enabled -> true in $sha" "$before" "$(json_obj fx.fallback.enabled true commit "$sha")"
