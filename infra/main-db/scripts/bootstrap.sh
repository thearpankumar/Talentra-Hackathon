#!/usr/bin/env bash
# Phase 2: runs once all 3 nodes are up. Idempotent. Cluster settings, zone config (node1 leaseholder), db, roles.
set -u
SECURE="${CRDB_SECURE:-0}"
if [ "$SECURE" = "1" ]; then A="--certs-dir=/certs"; else A="--insecure"; fi
sql() { cockroach sql $A --host=crdb1:26257 "$@"; }
mask() { sed -E "s/(PASSWORD|enterprise\.license|license) *=? *'[^']*'/\1 '***'/Ig"; }
try() { sql -e "$1" >/dev/null 2>&1 && echo "ok:   $1" | mask || { echo "WARN: failed: $1" | mask; sql -e "$1" 2>&1 | tail -2 | mask; }; }

# wait until 3 live nodes
for i in $(seq 1 60); do
  n=$(cockroach node status $A --host=crdb1:26257 --format=csv 2>/dev/null | tail -n +2 | awk -F, '$0 ~ /true,true$/ {c++} END{print c+0}')
  [ "$n" = "3" ] && break; sleep 2
done
echo "live nodes: $n"

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

# ---- cluster settings ----
try "SET CLUSTER SETTING kv.rangefeed.enabled = true"
try "SET CLUSTER SETTING sql.stats.automatic_collection.enabled = true"
try "SET CLUSTER SETTING diagnostics.reporting.enabled = true"
try "SET CLUSTER SETTING sql.log.admin_audit.enabled = true"
try "SET CLUSTER SETTING sql.defaults.idle_in_transaction_session_timeout = '5min'"
if [ "$SECURE" = "1" ]; then
  try "SET CLUSTER SETTING server.user_login.password_encryption = 'scram-sha-256'"
  try "SET CLUSTER SETTING server.user_login.password_hashes.default_cost.scram_sha_256 = 10610"
  try "SET CLUSTER SETTING server.user_login.min_password_length = 12"
fi

# ---- database + locality: node1 (zone=a) holds leases ----
try "CREATE DATABASE IF NOT EXISTS talentra"
try "ALTER RANGE default CONFIGURE ZONE USING num_replicas = 3, constraints = '[]', lease_preferences = '[[+zone=a]]'"
try "ALTER DATABASE talentra CONFIGURE ZONE USING num_replicas = 3, constraints = '[]', lease_preferences = '[[+zone=a]]'"
for r in meta liveness system; do try "ALTER RANGE $r CONFIGURE ZONE USING num_replicas = 3, constraints = '[]', lease_preferences = '[[+zone=a]]'"; done

# ---- roles / users ----
mkuser() { # name password
  if [ "$SECURE" = "1" ]; then
    try "CREATE USER IF NOT EXISTS $1 WITH LOGIN PASSWORD '$2'"
    try "ALTER USER $1 WITH LOGIN PASSWORD '$2'"
  else
    try "CREATE USER IF NOT EXISTS $1"
  fi
}
mkuser talentra_admin        "${TALENTRA_ADMIN_PASSWORD:-}"
mkuser talentra_app_rw       "${TALENTRA_APP_RW_PASSWORD:-}"
mkuser talentra_app_ro       "${TALENTRA_APP_RO_PASSWORD:-}"
mkuser talentra_analyst      "${TALENTRA_ANALYST_PASSWORD:-}"
mkuser talentra_audit_reader "${TALENTRA_AUDIT_READER_PASSWORD:-}"
mkuser talentra_backup       "${TALENTRA_BACKUP_PASSWORD:-}"
try "GRANT admin TO talentra_admin"
try "GRANT admin TO talentra_backup"
# ---- monitoring role: least privilege (no admin, no SELECT on any table; exporter only reads crdb_internal/SHOW JOBS) ----
if [ "$SECURE" = "1" ] && [ -z "${TALENTRA_MONITOR_PASSWORD:-}" ]; then
  echo "MONITOR: TALENTRA_MONITOR_PASSWORD empty -> talentra_monitor NOT created (set it in .env; monitoring needs it in prod)"
else
  mkuser talentra_monitor "${TALENTRA_MONITOR_PASSWORD:-}"
  try "GRANT SYSTEM VIEWACTIVITY, VIEWCLUSTERMETADATA, VIEWJOB, VIEWCLUSTERSETTING TO talentra_monitor"
  try "GRANT CONNECT ON DATABASE talentra TO talentra_monitor"
fi
try "GRANT ALL ON DATABASE talentra TO talentra_app_rw"
try "GRANT CONNECT ON DATABASE talentra TO talentra_app_ro, talentra_analyst, talentra_audit_reader"
try "GRANT SELECT ON ALL TABLES IN SCHEMA talentra.public TO talentra_app_ro, talentra_analyst"
for owner in root talentra_admin talentra_app_rw; do
  try "SET database = talentra; ALTER DEFAULT PRIVILEGES FOR ROLE $owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO talentra_app_rw"
  try "SET database = talentra; ALTER DEFAULT PRIVILEGES FOR ROLE $owner IN SCHEMA public GRANT SELECT ON TABLES TO talentra_app_ro, talentra_analyst"
done
# read-only roles: read-only sessions on follower reads (use via the read port)
for u in talentra_app_ro talentra_analyst talentra_audit_reader; do
  try "ALTER ROLE $u SET default_transaction_read_only = on"
  try "ALTER ROLE $u SET default_transaction_use_follower_reads = on"
done
echo "bootstrap done"
