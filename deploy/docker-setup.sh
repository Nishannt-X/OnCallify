#!/usr/bin/env bash
# One-time directory and secrets setup for running OnCallify with Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/.run"

mkdir -p "$RUN/vault" "$RUN/ledgerline/secrets" "$RUN/ledgerline/logs" "$RUN/ledgerline/conf" "$RUN/logs" "$RUN/pids"
chmod 700 "$RUN/vault" "$RUN/ledgerline/secrets"

# 1. secrets
printf '%s' "ledgerline_pw" > "$RUN/vault/ledgerline-db"
printf '%s' "oncallify_pw" > "$RUN/vault/oncallify-db"
printf '%s' "manager_pw" > "$RUN/vault/tomcat-manager"
printf '%s' "admin-token-12345" > "$RUN/vault/ledgerline-admin-token"
printf '%s' "oncallify-ingest-token" > "$RUN/vault/ingest-token"

printf '%s' "ledgerline_pw" > "$RUN/ledgerline/secrets/ledgerline-db.password"
printf '%s' "admin-token-12345" > "$RUN/ledgerline/secrets/admin.token"
chmod 600 "$RUN/ledgerline/secrets/"* "$RUN/vault/"*

# 2. tomcat-users.xml for manager API access
cat > "$RUN/tomcat-users.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<tomcat-users xmlns="http://tomcat.apache.org/xml" version="1.0">
  <role rolename="manager-script"/>
  <role rolename="manager-gui"/>
  <user username="oncallify-runbook" password="manager_pw" roles="manager-script,manager-gui"/>
</tomcat-users>
EOF
chmod 644 "$RUN/tomcat-users.xml"

# 3. Ledgerline runtime config (git repository)
CONF_DIR="$RUN/ledgerline/conf"
if [[ ! -d "$CONF_DIR/.git" ]]; then
  cat > "$CONF_DIR/ledgerline.properties" <<'EOF'
# Ledgerline runtime config. Every change is a git commit in this directory.
db.url=jdbc:postgresql://postgres:5432/ledgerline
db.user=ledgerline
db.password.file=/ledgerline/secrets/ledgerline-db.password
pool.max=10
pool.max.wait.ms=500
pool.max.age.ms=5000
fx.url=http://127.0.0.1:8080/ledgerline/fx/rate
fx.timeout.ms=1000
fx.fallback.enabled=false
faults.enabled=true
admin.token.file=/ledgerline/secrets/admin.token
log.dir=/ledgerline/logs
EOF
  git -C "$CONF_DIR" init -q
  git -C "$CONF_DIR" -c user.name=setup -c user.email=setup@oncallify.local add ledgerline.properties
  git -C "$CONF_DIR" -c user.name=setup -c user.email=setup@oncallify.local commit -qm "initial config"
fi

echo "[setup] Docker files initialized in $RUN"
