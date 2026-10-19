#!/usr/bin/env bats
# Tests for the bash runbooks. Each test gets a fresh config git repo, vault and
# app secret in a temp dir; restart_app talks to a fake Tomcat manager.

RB="$BATS_TEST_DIRNAME/../linux"

setup() {
  T="$(mktemp -d)"
  export LEDGERLINE_CONF_DIR="$T/conf"
  export ONCALLIFY_VAULT="$T/vault"
  export LEDGERLINE_SECRET_FILE="$T/app-secret"
  mkdir -p "$LEDGERLINE_CONF_DIR" "$ONCALLIFY_VAULT"
  cat > "$LEDGERLINE_CONF_DIR/ledgerline.properties" <<'EOF'
db.url=jdbc:postgresql://127.0.0.1:5432/ledgerline
pool.max=10
pool.max.wait.ms=500
fx.url=http://127.0.0.1:8080/ledgerline/fx/rate
fx.timeout.ms=1000
fx.fallback.enabled=false
EOF
  git -C "$LEDGERLINE_CONF_DIR" init -q
  gitc() { git -C "$LEDGERLINE_CONF_DIR" -c user.name=dev -c user.email=dev@x "$@"; }
  gitc add ledgerline.properties
  gitc commit -qm initial
  printf 'old-password' > "$LEDGERLINE_SECRET_FILE"
  printf 'new-password' > "$ONCALLIFY_VAULT/ledgerline-db"
  printf 'manager-pw' > "$ONCALLIFY_VAULT/tomcat-manager"
}

teardown() {
  [[ -n "${FAKE_PID:-}" ]] && kill "$FAKE_PID" 2>/dev/null || true
  rm -rf "$T"
}

commit_change() { # commit_change <sed expr> <message>
  sed -i.bak "$1" "$LEDGERLINE_CONF_DIR/ledgerline.properties" && rm -f "$LEDGERLINE_CONF_DIR/ledgerline.properties.bak"
  git -C "$LEDGERLINE_CONF_DIR" -c user.name=dev -c user.email=dev@x commit -qam "$2"
  git -C "$LEDGERLINE_CONF_DIR" rev-parse --short=12 HEAD
}

json_field() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read().splitlines()[-1])[sys.argv[1]])' "$1"; }

# ---- contract -------------------------------------------------------------------------

@test "unknown argument is a usage error (exit 2) with JSON output" {
  run bash "$RB/resize-pool.sh" --app ledgerline --size 20 --force
  [ "$status" -eq 2 ]
  [ "$(echo "$output" | json_field ok)" = "False" ]
}

@test "unknown app is refused" {
  run bash "$RB/enable-fx-fallback.sh" --app payroll
  [ "$status" -eq 2 ]
}

@test "a flag the action does not take is refused" {
  run bash "$RB/enable-fx-fallback.sh" --app ledgerline --size 20
  [ "$status" -eq 2 ]
  [[ "$output" == *"not accepted"* ]]
}

# ---- resize_pool ------------------------------------------------------------------------

@test "resize_pool dry run changes nothing" {
  run bash "$RB/resize-pool.sh" --app ledgerline --size 20 --dry-run
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | json_field changed)" = "False" ]
  grep -q '^pool.max=10$' "$LEDGERLINE_CONF_DIR/ledgerline.properties"
}

@test "resize_pool commits the new size as the runbook actor" {
  run bash "$RB/resize-pool.sh" --app ledgerline --size 20
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | json_field changed)" = "True" ]
  grep -q '^pool.max=20$' "$LEDGERLINE_CONF_DIR/ledgerline.properties"
  [ "$(git -C "$LEDGERLINE_CONF_DIR" log -1 --format=%an)" = "oncallify-runbook" ]
}

@test "resize_pool rejects sizes outside 5..50" {
  run bash "$RB/resize-pool.sh" --app ledgerline --size 500
  [ "$status" -eq 2 ]
}

# ---- revert_config_change ---------------------------------------------------------------

@test "revert dry run validates and reports the keys" {
  sha="$(commit_change 's/^fx.url=.*/fx.url=http:\/\/127.0.0.1:8089\/fx\/rate/' 'point fx at new host')"
  run bash "$RB/revert-config-change.sh" --app ledgerline --commit "$sha" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"fx.url"* ]]
  grep -q '8089' "$LEDGERLINE_CONF_DIR/ledgerline.properties"
}

@test "revert undoes only the target commit and keeps a later unrelated one" {
  bad="$(commit_change 's/^pool.max=10/pool.max=2/' 'tune pool for load test')"
  commit_change 's/^fx.timeout.ms=1000/fx.timeout.ms=1200/' 'raise fx timeout' >/dev/null
  run bash "$RB/revert-config-change.sh" --app ledgerline --commit "$bad"
  [ "$status" -eq 0 ]
  grep -q '^pool.max=10$' "$LEDGERLINE_CONF_DIR/ledgerline.properties"
  grep -q '^fx.timeout.ms=1200$' "$LEDGERLINE_CONF_DIR/ledgerline.properties"
}

@test "reverting the same commit twice is a precondition failure" {
  bad="$(commit_change 's/^pool.max=10/pool.max=2/' 'shrink')"
  run bash "$RB/revert-config-change.sh" --app ledgerline --commit "$bad"
  [ "$status" -eq 0 ]
  run bash "$RB/revert-config-change.sh" --app ledgerline --commit "$bad"
  [ "$status" -eq 3 ]
  [[ "$output" == *"already reverted"* ]]
}

@test "reverting a commit that does not exist is a precondition failure" {
  run bash "$RB/revert-config-change.sh" --app ledgerline --commit deadbeefdead
  [ "$status" -eq 3 ]
}

@test "revert refuses when there are uncommitted edits" {
  bad="$(commit_change 's/^pool.max=10/pool.max=2/' 'shrink')"
  echo "local.hack=1" >> "$LEDGERLINE_CONF_DIR/ledgerline.properties"
  run bash "$RB/revert-config-change.sh" --app ledgerline --commit "$bad"
  [ "$status" -eq 3 ]
  [[ "$output" == *"uncommitted"* ]]
}

# ---- enable_fx_fallback -----------------------------------------------------------------

@test "enable_fx_fallback is idempotent" {
  run bash "$RB/enable-fx-fallback.sh" --app ledgerline
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | json_field changed)" = "True" ]
  run bash "$RB/enable-fx-fallback.sh" --app ledgerline
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | json_field changed)" = "False" ]
}

# ---- sync_db_credential -----------------------------------------------------------------

@test "sync_db_credential copies the vault secret atomically and never prints it" {
  run bash "$RB/sync-db-credential.sh" --app ledgerline
  [ "$status" -eq 0 ]
  [ "$(cat "$LEDGERLINE_SECRET_FILE")" = "new-password" ]
  [[ "$output" != *"new-password"* ]]
  [[ "$output" != *"old-password"* ]]
  [ "$(stat -c %a "$LEDGERLINE_SECRET_FILE" 2>/dev/null || stat -f %Lp "$LEDGERLINE_SECRET_FILE")" = "600" ]
}

@test "sync_db_credential is a no-op when already in sync" {
  printf 'new-password' > "$LEDGERLINE_SECRET_FILE"
  run bash "$RB/sync-db-credential.sh" --app ledgerline
  [ "$status" -eq 0 ]
  [ "$(echo "$output" | json_field changed)" = "False" ]
}

@test "sync_db_credential fails the precondition when the vault secret is missing" {
  rm "$ONCALLIFY_VAULT/ledgerline-db"
  run bash "$RB/sync-db-credential.sh" --app ledgerline
  [ "$status" -eq 3 ]
}

# ---- restart_app ------------------------------------------------------------------------

start_fake_manager() {
  PORT=$(( 20000 + RANDOM % 20000 ))
  python3 "$BATS_TEST_DIRNAME/fake_manager.py" "$PORT" "$ONCALLIFY_VAULT/tomcat-manager" &
  FAKE_PID=$!
  export TOMCAT_URL="http://127.0.0.1:$PORT"
  for _ in $(seq 1 50); do curl -s -o /dev/null "$TOMCAT_URL/" && return 0; sleep 0.1; done
  return 1
}

@test "restart_app dry run lists the context but does not reload" {
  start_fake_manager
  run bash "$RB/restart-app.sh" --app ledgerline --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"running"* ]]
  [ ! -s "$ONCALLIFY_VAULT/tomcat-manager.reloads" ]
}

@test "restart_app reloads exactly the named context" {
  start_fake_manager
  run bash "$RB/restart-app.sh" --app ledgerline
  [ "$status" -eq 0 ]
  [ "$(cat "$ONCALLIFY_VAULT/tomcat-manager.reloads")" = "/ledgerline" ]
}

@test "restart_app fails the precondition when the manager is unreachable" {
  export TOMCAT_URL="http://127.0.0.1:1"
  run bash "$RB/restart-app.sh" --app ledgerline
  [ "$status" -eq 3 ]
}

@test "revert refuses when a later commit changed the same line" {
  bad="$(commit_change 's/^pool.max=10/pool.max=2/' 'shrink')"
  commit_change 's/^pool.max=2/pool.max=3/' 'tweak again' >/dev/null
  run bash "$RB/revert-config-change.sh" --app ledgerline --commit "$bad" --dry-run
  [ "$status" -eq 3 ]
  [[ "$output" == *"no longer reverts cleanly"* ]]
}
