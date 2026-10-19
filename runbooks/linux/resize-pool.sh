#!/usr/bin/env bash
# resize_pool: set pool.max (committed to the config repository).
RB_ACTION=resize_pool
# shellcheck source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
parse_args --app --size -- "$@"
require_env LEDGERLINE_CONF_DIR
[[ "$RB_SIZE" =~ ^[0-9]+$ ]] || usage_error "--size must be an integer"
(( RB_SIZE >= 5 && RB_SIZE <= 50 )) || usage_error "--size must be between 5 and 50"
[[ -f "$(config_file)" ]] || precondition_failed "config file $(config_file) not found"

current="$(prop_get pool.max)"
before="$(json_obj pool.max "${current:-unset}")"
if [[ "$current" == "$RB_SIZE" ]]; then
  result 0 true false "pool.max is already $RB_SIZE" "$before" "$before"
fi
if $RB_DRY_RUN; then
  result 0 true false "would set pool.max ${current:-unset} -> $RB_SIZE" "$before"
fi
sha="$(prop_set_commit pool.max "$RB_SIZE" "resize pool.max ${current:-unset} -> $RB_SIZE (oncallify)")" \
  || execution_failed "could not commit config change"
result 0 true true "pool.max ${current:-unset} -> $RB_SIZE in $sha" "$before" "$(json_obj pool.max "$RB_SIZE" commit "$sha")"
