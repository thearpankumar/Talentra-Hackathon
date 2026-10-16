#!/usr/bin/env bash
# Backup cluster bootstrap (idempotent): admin user for the restore job + the same roles as main (so restored grants resolve).
set -u
SECURE="${CRDB_SECURE:-0}"
if [ "$SECURE" = "1" ]; then A="--certs-dir=/certs"; else A="--insecure"; fi
sql() { cockroach sql $A --host=backup-crdb:26257 "$@"; }
mask() { sed -E "s/(PASSWORD|enterprise\.license|license) *=? *'[^']*'/\1 '***'/Ig"; }
try() { sql -e "$1" >/dev/null 2>&1 && echo "ok:   $1" | mask || { echo "WARN: failed: $1" | mask; sql -e "$1" 2>&1 | tail -2 | mask; }; }
for i in $(seq 1 60); do sql -e "SELECT 1" >/dev/null 2>&1 && break; sleep 2; done
# ---- license (Enterprise Free key; optional, idempotent, never fails the bootstrap) ----
# Without a key CockroachDB v26.x throttles SQL after the 7-day grace period (see README: licensing).
sqlq() { sql --format=csv -e "$1" 2>/dev/null | tail -n +2 | tr -d '"\r'; }
apply_license() {
  if [ -z "${CRDB_LICENSE_KEY:-}" ]; then
    echo "LICENSE: CRDB_LICENSE_KEY is empty -> skipping enterprise.license / cluster.organization (cluster runs unlicensed; throttling starts after the grace period)"
    return 0
  fi
  case "$CRDB_LICENSE_KEY" in *"'"*|*" "*) echo "WARN: CRDB_LICENSE_KEY contains quote/space characters -> not applied"; return 0 ;; esac
  if [ "$(sqlq "SHOW CLUSTER SETTING enterprise.license")" = "$CRDB_LICENSE_KEY" ]; then
    echo "LICENSE: enterprise.license already set (unchanged)"
  else
    if sql -e "SET CLUSTER SETTING enterprise.license = '${CRDB_LICENSE_KEY}'" >/dev/null 2>&1; then echo "LICENSE: enterprise.license applied"
    else echo "WARN: LICENSE: setting enterprise.license failed (invalid key?) - continuing unlicensed"; fi
  fi
  if [ -n "${CRDB_ORG:-}" ]; then
    local org="${CRDB_ORG//\'/\'\'}"
    if [ "$(sqlq "SHOW CLUSTER SETTING cluster.organization")" = "$CRDB_ORG" ]; then echo "LICENSE: cluster.organization already set"
    else try "SET CLUSTER SETTING cluster.organization = '${org}'"; fi
  else
    echo "LICENSE: CRDB_ORG empty -> cluster.organization not set (the Free license needs it to match the key's organization)"
  fi
}
apply_license

try "SET CLUSTER SETTING diagnostics.reporting.enabled = true"
try "SET CLUSTER SETTING sql.stats.automatic_collection.enabled = true"
if [ "$SECURE" = "1" ]; then
  try "SET CLUSTER SETTING server.user_login.password_hashes.default_cost.scram_sha_256 = 10610"
  try "SET CLUSTER SETTING server.user_login.min_password_length = 12"
fi
mkuser() {
  if [ "$SECURE" = "1" ]; then
    try "CREATE USER IF NOT EXISTS $1 WITH LOGIN PASSWORD '$2'"; try "ALTER USER $1 WITH LOGIN PASSWORD '$2'"
  else try "CREATE USER IF NOT EXISTS $1"; fi
}
mkuser backup_admin          "${BACKUP_ADMIN_PASSWORD:-}"
mkuser talentra_admin        "${TALENTRA_ADMIN_PASSWORD:-}"
mkuser talentra_app_rw       "${TALENTRA_APP_RW_PASSWORD:-}"
mkuser talentra_app_ro       "${TALENTRA_APP_RO_PASSWORD:-}"
mkuser talentra_analyst      "${TALENTRA_ANALYST_PASSWORD:-}"
mkuser talentra_audit_reader "${TALENTRA_AUDIT_READER_PASSWORD:-}"
try "GRANT admin TO backup_admin"
try "GRANT admin TO talentra_admin"
# ---- monitoring role: least privilege (no admin, no SELECT on any table; exporter only reads crdb_internal/SHOW JOBS) ----
if [ "$SECURE" = "1" ] && [ -z "${TALENTRA_MONITOR_PASSWORD:-}" ]; then
  echo "MONITOR: TALENTRA_MONITOR_PASSWORD empty -> talentra_monitor NOT created (set it in .env; monitoring needs it in prod)"
else
  mkuser talentra_monitor "${TALENTRA_MONITOR_PASSWORD:-}"
  try "GRANT SYSTEM VIEWACTIVITY, VIEWCLUSTERMETADATA, VIEWJOB, VIEWCLUSTERSETTING TO talentra_monitor"
  try "GRANT CONNECT ON DATABASE defaultdb TO talentra_monitor"
fi
echo "backup bootstrap done"
