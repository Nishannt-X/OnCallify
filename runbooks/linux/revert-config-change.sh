#!/usr/bin/env bash
# revert_config_change: git-revert ONE commit in the config repository.
# Later, unrelated commits are kept. The app hot-reloads the file.
RB_ACTION=revert_config_change
# shellcheck source=_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
parse_args --app --commit -- "$@"
require_env LEDGERLINE_CONF_DIR
[[ "$RB_COMMIT" =~ ^[0-9a-f]{7,40}$ ]] || usage_error "--commit must be 7-40 hex characters"

repo="$LEDGERLINE_CONF_DIR"
git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || precondition_failed "$repo is not a git repository"
full="$(git -C "$repo" rev-parse --verify --quiet "$RB_COMMIT^{commit}")" \
  || precondition_failed "commit $RB_COMMIT does not exist"
git -C "$repo" merge-base --is-ancestor "$full" HEAD || precondition_failed "commit $RB_COMMIT is not on the current branch"
git -C "$repo" rev-parse --verify --quiet "$full^" >/dev/null || precondition_failed "cannot revert the initial commit"
touched="$(git -C "$repo" diff --name-only "$full^" "$full")"
[[ "$touched" == "$RB_CONFIG_FILE_NAME" ]] || precondition_failed "commit $RB_COMMIT touches files other than $RB_CONFIG_FILE_NAME"
if git -C "$repo" log --format=%B HEAD | grep -q "This reverts commit $full"; then
  precondition_failed "commit $RB_COMMIT was already reverted"
fi
[[ -z "$(git -C "$repo" status --porcelain -- "$RB_CONFIG_FILE_NAME")" ]] \
  || precondition_failed "config has uncommitted local edits; refusing to revert on top of them"

# keys the commit changed, as "key=old->new" (shown to the approver)
keys="$(git -C "$repo" diff -U0 "$full^" "$full" -- "$RB_CONFIG_FILE_NAME" \
  | grep -E '^[+-][^+-]' | sed -E 's/^([+-])([^=]*)=.*/\2/' | sort -u | paste -sd, -)"
head_before="$(git -C "$repo" rev-parse --short=12 HEAD)"
before="$(json_obj head "$head_before" commit "${full:0:12}" keys "$keys")"

# Rehearse the exact revert in a throwaway clone: the dry run must give the same answer
# the real run will (a context-based patch check is stricter than git's merge).
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
git clone -q "$repo" "$scratch/repo" 2>/dev/null || precondition_failed "could not clone config repo for rehearsal"
if ! git -C "$scratch/repo" -c user.name=rehearsal -c user.email=r@x revert --no-edit "$full" >/dev/null 2>&1; then
  precondition_failed "commit $RB_COMMIT no longer reverts cleanly (later commits touch the same lines)"
fi

if $RB_DRY_RUN; then
  result 0 true false "would revert ${full:0:12} (keys: $keys)" "$before"
fi

git_cfg revert --no-edit "$full" >/dev/null 2>&1 || {
  git -C "$repo" revert --abort >/dev/null 2>&1 || true
  execution_failed "git revert failed"
}
head_after="$(git -C "$repo" rev-parse --short=12 HEAD)"
result 0 true true "reverted ${full:0:12} (keys: $keys)" "$before" "$(json_obj head "$head_after" keys "$keys")"
