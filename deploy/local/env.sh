#!/usr/bin/env bash
# Shared paths and ports for the local stack. Source it; do not run it.
# Every value can be overridden from the environment.

ONCALLIFY_ROOT="${ONCALLIFY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
ONCALLIFY_RUN="${ONCALLIFY_RUN:-$ONCALLIFY_ROOT/.run}"

# Tomcat: CATALINA_HOME is the install, CATALINA_BASE is our private instance.
if [[ -z "${CATALINA_HOME:-}" ]]; then
  if [[ -d "/usr/share/tomcat10" ]]; then
    CATALINA_HOME="/usr/share/tomcat10"
  elif command -v brew >/dev/null 2>&1 && brew --prefix tomcat@10 >/dev/null 2>&1 && [[ -d "$(brew --prefix tomcat@10 2>/dev/null)/libexec" ]]; then
    CATALINA_HOME="$(brew --prefix tomcat@10)/libexec"
  elif command -v brew >/dev/null 2>&1 && brew --prefix tomcat >/dev/null 2>&1 && [[ -d "$(brew --prefix tomcat 2>/dev/null)/libexec" ]]; then
    CATALINA_HOME="$(brew --prefix tomcat)/libexec"
  else
    CATALINA_HOME="/usr/share/tomcat10"
  fi
fi

CATALINA_BASE="${CATALINA_BASE:-$ONCALLIFY_RUN/tomcat}"
TOMCAT_PORT="${TOMCAT_PORT:-8080}"
TOMCAT_SHUTDOWN_PORT="${TOMCAT_SHUTDOWN_PORT:-8005}"

if [[ -z "${TOMCAT_MANAGER_APPS:-}" ]]; then
  if [[ -d "/usr/share/tomcat10-admin/manager" ]]; then
    TOMCAT_MANAGER_APPS="/usr/share/tomcat10-admin/manager"
  elif [[ -d "$CATALINA_HOME/webapps/manager" ]]; then
    TOMCAT_MANAGER_APPS="$CATALINA_HOME/webapps/manager"
  else
    TOMCAT_MANAGER_APPS="/usr/share/tomcat10-admin/manager"
  fi
fi

# Ledgerline's config is a git repository: every change is a commit (the change ledger).
LEDGERLINE_HOME="${LEDGERLINE_HOME:-$ONCALLIFY_RUN/ledgerline}"
LEDGERLINE_CONF_DIR="$LEDGERLINE_HOME/conf"
LEDGERLINE_CONFIG="$LEDGERLINE_CONF_DIR/ledgerline.properties"
LEDGERLINE_LOG_DIR="$LEDGERLINE_HOME/logs"
LEDGERLINE_URL="${LEDGERLINE_URL:-http://127.0.0.1:$TOMCAT_PORT/ledgerline}"

# A file-backed stand-in for a secret manager: the CURRENT database password lives here.
ONCALLIFY_VAULT="${ONCALLIFY_VAULT:-$ONCALLIFY_RUN/vault}"

# PostgreSQL superuser connection, used only to create roles/databases and by the
# credential-rotation scenario. Example (Homebrew): postgresql://localhost/postgres
PG_ADMIN_URL="${PG_ADMIN_URL:-postgresql://postgres@127.0.0.1:5432/postgres}"
PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"

GATEWAY_PORT="${GATEWAY_PORT:-7070}"
AGENT_PORT="${AGENT_PORT:-7080}"

export ONCALLIFY_ROOT ONCALLIFY_RUN CATALINA_HOME CATALINA_BASE TOMCAT_PORT TOMCAT_SHUTDOWN_PORT \
  TOMCAT_MANAGER_APPS LEDGERLINE_HOME LEDGERLINE_CONF_DIR LEDGERLINE_CONFIG LEDGERLINE_LOG_DIR \
  LEDGERLINE_URL ONCALLIFY_VAULT PG_ADMIN_URL PG_HOST PG_PORT GATEWAY_PORT AGENT_PORT
