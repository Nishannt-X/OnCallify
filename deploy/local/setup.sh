#!/usr/bin/env bash
# Idempotent one-time setup of the local stack:
#   1. PostgreSQL roles and databases (ledgerline for the app, oncallify for the agent)
#   2. secrets (database passwords, Tomcat manager password, admin token)
#   3. Ledgerline config as a git repository (the change ledger)
#   4. a private Tomcat instance (CATALINA_BASE) with the manager app for runbooks
set -euo pipefail
# shellcheck source=env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

log() { printf '[setup] %s\n' "$*"; }

rand() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c "${1:-24}"; }

mkdir -p "$ONCALLIFY_RUN" "$ONCALLIFY_VAULT" "$LEDGERLINE_HOME/secrets" "$LEDGERLINE_LOG_DIR"
chmod 700 "$ONCALLIFY_VAULT" "$LEDGERLINE_HOME/secrets"

secret() { # secret <file>: create once, keep forever
  local f="$1"
  [[ -s "$f" ]] || { rand 24 > "$f"; chmod 600 "$f"; }
  cat "$f"
}

# --- 1. secrets -----------------------------------------------------------------------
db_pw="$(secret "$ONCALLIFY_VAULT/ledgerline-db")"
agent_db_pw="$(secret "$ONCALLIFY_VAULT/oncallify-db")"
manager_pw="$(secret "$ONCALLIFY_VAULT/tomcat-manager")"
secret "$ONCALLIFY_VAULT/ledgerline-admin-token" >/dev/null
# the app reads its own copy; drift between this copy and the vault is a real failure mode
[[ -s "$LEDGERLINE_HOME/secrets/ledgerline-db.password" ]] || {
  printf '%s' "$db_pw" > "$LEDGERLINE_HOME/secrets/ledgerline-db.password"
  chmod 600 "$LEDGERLINE_HOME/secrets/ledgerline-db.password"
}
cp "$ONCALLIFY_VAULT/ledgerline-admin-token" "$LEDGERLINE_HOME/secrets/admin.token"
chmod 600 "$LEDGERLINE_HOME/secrets/admin.token"

# --- 2. postgres ----------------------------------------------------------------------
log "postgres roles and databases"
psql "$PG_ADMIN_URL" -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledgerline') THEN
    CREATE ROLE ledgerline LOGIN PASSWORD '$db_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'oncallify') THEN
    CREATE ROLE oncallify LOGIN PASSWORD '$agent_db_pw';
  END IF;
END
\$\$;
SQL
for db in ledgerline oncallify; do
  if ! psql "$PG_ADMIN_URL" -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1; then
    psql "$PG_ADMIN_URL" -q -c "CREATE DATABASE $db OWNER $db"
  fi
done

# --- 3. config repository -------------------------------------------------------------
if [[ ! -d "$LEDGERLINE_CONF_DIR/.git" ]]; then
  log "config repository at $LEDGERLINE_CONF_DIR"
  mkdir -p "$LEDGERLINE_CONF_DIR"
  cat > "$LEDGERLINE_CONFIG" <<EOF
# Ledgerline runtime config. Every change is a git commit in this directory.
db.url=jdbc:postgresql://$PG_HOST:$PG_PORT/ledgerline
db.user=ledgerline
db.password.file=../secrets/ledgerline-db.password
pool.max=10
pool.max.wait.ms=500
pool.max.age.ms=5000
fx.url=$LEDGERLINE_URL/fx/rate
fx.timeout.ms=1000
fx.fallback.enabled=false
faults.enabled=true
admin.token.file=../secrets/admin.token
log.dir=$LEDGERLINE_LOG_DIR
EOF
  git -C "$LEDGERLINE_CONF_DIR" init -q
  git -C "$LEDGERLINE_CONF_DIR" -c user.name=setup -c user.email=setup@oncallify.local add ledgerline.properties
  git -C "$LEDGERLINE_CONF_DIR" -c user.name=setup -c user.email=setup@oncallify.local commit -qm "initial config"
fi

# --- 4. tomcat base -------------------------------------------------------------------
log "tomcat base at $CATALINA_BASE"
mkdir -p "$CATALINA_BASE"/{conf,logs,temp,webapps,work,bin}
conf_src="$CATALINA_HOME/conf"
[[ -f "$conf_src/server.xml" ]] || conf_src=/etc/tomcat10
for f in web.xml context.xml logging.properties catalina.properties; do
  [[ -f "$CATALINA_BASE/conf/$f" ]] || cp "$conf_src/$f" "$CATALINA_BASE/conf/$f"
done
cat > "$CATALINA_BASE/conf/server.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<Server port="$TOMCAT_SHUTDOWN_PORT" shutdown="SHUTDOWN">
  <Listener className="org.apache.catalina.startup.VersionLoggerListener" />
  <Listener className="org.apache.catalina.core.JreMemoryLeakPreventionListener" />
  <Listener className="org.apache.catalina.mbeans.GlobalResourcesLifecycleListener" />
  <Listener className="org.apache.catalina.core.ThreadLocalLeakPreventionListener" />
  <GlobalNamingResources>
    <Resource name="UserDatabase" auth="Container" type="org.apache.catalina.UserDatabase"
              factory="org.apache.catalina.users.MemoryUserDatabaseFactory" pathname="conf/tomcat-users.xml" />
  </GlobalNamingResources>
  <Service name="Catalina">
    <Connector port="$TOMCAT_PORT" address="127.0.0.1" protocol="HTTP/1.1" connectionTimeout="20000"
               maxThreads="40" acceptCount="50" />
    <Engine name="Catalina" defaultHost="localhost">
      <Realm className="org.apache.catalina.realm.LockOutRealm">
        <Realm className="org.apache.catalina.realm.UserDatabaseRealm" resourceName="UserDatabase"/>
      </Realm>
      <Host name="localhost" appBase="webapps" unpackWARs="true" autoDeploy="false" deployOnStartup="true">
        <Valve className="org.apache.catalina.valves.AccessLogValve" directory="logs"
               prefix="access" suffix=".log" pattern="%h %t &quot;%r&quot; %s %b %D" />
      </Host>
    </Engine>
  </Service>
</Server>
EOF
cat > "$CATALINA_BASE/conf/tomcat-users.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tomcat-users xmlns="http://tomcat.apache.org/xml" version="1.0">
  <!-- the runbook user can only use the text API (reload/list), not the HTML console -->
  <role rolename="manager-script"/>
  <user username="oncallify-runbook" password="$manager_pw" roles="manager-script"/>
</tomcat-users>
EOF
chmod 600 "$CATALINA_BASE/conf/tomcat-users.xml"
if [[ -d "$TOMCAT_MANAGER_APPS" && ! -d "$CATALINA_BASE/webapps/manager" ]]; then
  cp -r "$TOMCAT_MANAGER_APPS" "$CATALINA_BASE/webapps/manager"
  # the packaged context.xml points at a build path; drop it so Tomcat uses defaults
  cat > "$CATALINA_BASE/webapps/manager/META-INF/context.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<Context privileged="true">
  <Valve className="org.apache.catalina.valves.RemoteCIDRValve" allow="127.0.0.0/8, ::1/128"/>
</Context>
EOF
fi
cat > "$CATALINA_BASE/bin/setenv.sh" <<EOF
# small heap on purpose: a memory leak should hurt within a minute, not an hour
export CATALINA_OPTS="\${CATALINA_OPTS:-} -Xms128m -Xmx384m -Duser.timezone=UTC"
export LEDGERLINE_CONFIG="$LEDGERLINE_CONFIG"
EOF
log "done"
