#!/usr/bin/env bash
# sync_db_credential: copy the vault's CURRENT database password into the app's secret
# file (temp file + atomic rename, mode 600). The app notices the change and rebuilds
# its pool. Secrets are never printed: only SHA-256 fingerprints (first 12 chars).
RB_ACTION=sync_db_credential
# shellcheck source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
parse_args --app -- "$@"
require_env ONCALLIFY_VAULT LEDGERLINE_SECRET_FILE

vault_file="$ONCALLIFY_VAULT/$RB_APP-db"
[[ -r "$vault_file" ]] || precondition_failed "vault secret $vault_file is not readable"
[[ -s "$vault_file" ]] || precondition_failed "vault secret $vault_file is empty"
target="$LEDGERLINE_SECRET_FILE"
[[ -e "$target" ]] || precondition_failed "app secret file $target does not exist"

vault_fp="$(sha256_of "$vault_file" | cut -c1-12)"
app_fp="$(sha256_of "$target" | cut -c1-12)"
before="$(json_obj vault "$vault_fp" app "$app_fp")"
if [[ "$vault_fp" == "$app_fp" ]]; then
  result 0 true false "app credential already matches the vault" "$before" "$before"
fi
if $RB_DRY_RUN; then
  result 0 true false "would replace app credential $app_fp with vault credential $vault_fp" "$before"
fi

tmp="$(mktemp "$(dirname "$target")/.secret.XXXXXX")"
chmod 600 "$tmp"
cat "$vault_file" > "$tmp"
mv "$tmp" "$target" || execution_failed "could not replace $target"
new_fp="$(sha256_of "$target" | cut -c1-12)"
[[ "$new_fp" == "$vault_fp" ]] || execution_failed "credential did not match after write"
result 0 true true "app credential now matches vault ($vault_fp)" "$before" "$(json_obj vault "$vault_fp" app "$new_fp")"
