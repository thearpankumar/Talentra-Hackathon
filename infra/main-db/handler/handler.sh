#!/usr/bin/env bash
# Talentra backup handler: scheduled BACKUP (full + incremental) of main, and sync (RESTORE) into the backup cluster.
# Usage: handler.sh loop | backup [full|incr] | sync | verify
set -u
SECURE="${CRDB_SECURE:-0}"
LOGDIR="${LOG_DIR:-/logs}"; STATE="${STATE_DIR:-/state}"
CSV="$LOGDIR/backup_timing.csv"
MAIN_HOST="${MAIN_SQL_HOST:-haproxy}"; MAIN_PORT="${MAIN_SQL_PORT:-26257}"
BK_HOST="${BACKUP_SQL_HOST:-backup-crdb}"; BK_PORT="${BACKUP_SQL_PORT:-26257}"
DB="${BACKUP_DB:-talentra}"
URI="${BACKUP_URI:-nodelocal://self/main}"
FULL_MIN="${FULL_EVERY_MIN:-1440}"; INCR_MIN="${INCR_EVERY_MIN:-60}"; SYNC_MIN="${SYNC_EVERY_MIN:-360}"
mkdir -p "$LOGDIR" "$STATE"
HDR="ts_utc,op,kind,status,start_utc,end_utc,duration_s,detail,size_bytes"
[ -f "$CSV" ] || echo "$HDR" > "$CSV"
# migrate an old 8-column CSV in place (header + empty size_bytes on existing rows)
if [ "$(head -1 "$CSV")" = "ts_utc,op,kind,status,start_utc,end_utc,duration_s,detail" ]; then
  sed -i -e '1s/$/,size_bytes/' -e '2,$s/$/,/' "$CSV"; echo "migrated $CSV to add size_bytes"
fi

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
url() { # user pass host port
  if [ "$SECURE" = "1" ]; then
    echo "postgresql://$1:$2@$3:$4/defaultdb?sslmode=verify-full&sslrootcert=$5"
  else
    echo "postgresql://root@$3:$4/defaultdb?sslmode=disable"
  fi
}
MAIN_URL="$(url "${MAIN_SQL_USER:-talentra_backup}" "${MAIN_SQL_PASSWORD:-}" "$MAIN_HOST" "$MAIN_PORT" ${MAIN_CA:-/certs/main-ca.crt})"
BK_URL="$(url "${BACKUP_SQL_USER:-backup_admin}" "${BACKUP_SQL_PASSWORD:-}" "$BK_HOST" "$BK_PORT" ${BACKUP_CA:-/certs/backup-ca.crt})"
msql() { cockroach sql --url "$MAIN_URL" "$@"; }
bsql() { cockroach sql --url "$BK_URL" "$@"; }

# record op kind status start_epoch detail [size_bytes]
rec() {
  local end_e; end_e=$(date +%s)
  local d=$((end_e - $4))
  echo "$(now),$1,$2,$3,$(date -u -d @$4 +%Y-%m-%dT%H:%M:%SZ),$(now),$d,\"${5//\"/\'}\",${6:-}" >> "$CSV"
  echo "[$(now)] $1 $2 $3 ${d}s $5"
}

wait_main() { for i in $(seq 1 90); do msql -e "SELECT 1 FROM [SHOW DATABASES] WHERE database_name='$DB'" --format=csv 2>/dev/null | grep -q 1 && return 0; sleep 3; done; return 1; }

# bytes in the latest backup layer of URI (empty on failure). SHOW BACKUP lists per-object sizes (cheap, reads manifests).
backup_size() { msql --format=csv -e "SELECT COALESCE(sum(size_bytes),0)::INT8 FROM [SHOW BACKUP LATEST IN '$1']" 2>/dev/null | tail -1 | grep -E '^[0-9]+$' || true; }

do_backup() { # kind: full|incr
  local kind="$1" s out rc; s=$(date +%s)
  if [ "$kind" = "full" ]; then
    out=$(msql --format=csv -e "BACKUP DATABASE $DB INTO '$URI'" 2>&1); rc=$?
  else
    out=$(msql --format=csv -e "BACKUP DATABASE $DB INTO LATEST IN '$URI'" 2>&1); rc=$?
    if [ $rc -ne 0 ] && echo "$out" | grep -qiE "no backup|not found|does not exist|no such"; then
      echo "no base full backup; promoting to full"; kind=full; s=$(date +%s)
      out=$(msql --format=csv -e "BACKUP DATABASE $DB INTO '$URI'" 2>&1); rc=$?
    fi
  fi
  if [ $rc -eq 0 ]; then
    [ "$kind" = "full" ] && date +%s > "$STATE/last_full"
    date +%s > "$STATE/last_backup"
    rec backup "$kind-local" ok "$s" "job: $(echo "$out" | grep -E '^[0-9]+,' | tail -1)" "$(backup_size "$URI")"
  else
    rec backup "$kind-local" fail "$s" "$(echo "$out" | tail -1)"; return 1
  fi
  if [ -n "${BACKUP_S3_URI:-}" ]; then
    s=$(date +%s)
    if [ "$kind" = "full" ]; then q="BACKUP DATABASE $DB INTO '$BACKUP_S3_URI'"; else q="BACKUP DATABASE $DB INTO LATEST IN '$BACKUP_S3_URI'"; fi
    out=$(msql --format=csv -e "$q" 2>&1) && rec backup "$kind-s3" ok "$s" "$(echo "$out" | grep -E '^[0-9]+,' | tail -1)" "$(backup_size "$BACKUP_S3_URI")" || rec backup "$kind-s3" fail "$s" "$(echo "$out" | tail -1)"
  fi
}

cmd_backup() {
  local kind="${1:-auto}"
  wait_main || { echo "main db not ready"; return 1; }
  if [ "$kind" = "auto" ]; then
    local lf=0; [ -f "$STATE/last_full" ] && lf=$(cat "$STATE/last_full")
    if [ $(( $(date +%s) - lf )) -ge $((FULL_MIN*60)) ]; then kind=full; else kind=incr; fi
  fi
  do_backup "$kind"
}

cmd_verify() {
  local tables t m b bad=0 s; s=$(date +%s)
  tables=$(msql --format=csv -e "SELECT table_name FROM $DB.information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" 2>/dev/null | tail -n +2)
  local detail=""
  for t in $tables; do
    m=$(msql --format=csv -e "SELECT count(*) FROM $DB.public.\"$t\"" 2>/dev/null | tail -1)
    b=$(bsql --format=csv -e "SELECT count(*) FROM $DB.public.\"$t\"" 2>/dev/null | tail -1)
    detail="$detail $t main=$m backup=$b;"; [ "$m" = "$b" ] || bad=1
  done
  [ $bad -eq 0 ] && rec verify counts ok "$s" "$detail" || rec verify counts drift "$s" "$detail"
  echo "$detail"; return $bad
}

# Mirror roles, memberships, owners and grants from main onto the backup cluster. RESTORE does NOT bring them back (verified:
# explicit grants, non-root owners and default privileges are lost). Generated from main's catalog so it needs no schema files.
acl_sql() { # prints SQL statements (one per line) for database $DB, run against main
  msql -d "$DB" --format=tsv -e "
SELECT 'CREATE ROLE IF NOT EXISTS ' || quote_ident(username) FROM [SHOW USERS] WHERE username NOT IN ('root','admin','node');
SELECT 'GRANT ' || quote_ident(r) || ' TO ' || quote_ident(username) FROM [SHOW USERS], unnest(member_of) AS r(r) WHERE username <> 'root';
SELECT 'ALTER DATABASE ' || quote_ident(database_name) || ' OWNER TO ' || quote_ident(owner) FROM [SHOW DATABASES] WHERE database_name = '$DB' AND owner <> 'root';
SELECT 'ALTER TABLE ' || quote_ident(schemaname) || '.' || quote_ident(tablename) || ' OWNER TO ' || quote_ident(tableowner) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema','crdb_internal','pg_extension') AND tableowner <> 'root';
SELECT 'ALTER VIEW ' || quote_ident(schemaname) || '.' || quote_ident(viewname) || ' OWNER TO ' || quote_ident(viewowner) FROM pg_catalog.pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema','crdb_internal','pg_extension') AND viewowner <> 'root';
SELECT 'GRANT ' || string_agg(privilege_type, ',') || ' ON DATABASE ' || quote_ident(database_name) || ' TO ' || quote_ident(grantee) FROM [SHOW GRANTS ON DATABASE $DB] WHERE grantee NOT IN ('root','admin','public') GROUP BY database_name, grantee;
SELECT 'GRANT ' || string_agg(privilege_type, ',') || ' ON SCHEMA ' || quote_ident(schema_name) || ' TO ' || quote_ident(grantee) FROM [SHOW GRANTS ON SCHEMA public] WHERE grantee NOT IN ('root','admin','public') GROUP BY schema_name, grantee;
SELECT 'GRANT ' || string_agg(privilege_type, ',') || ' ON TABLE ' || quote_ident(table_schema) || '.' || quote_ident(table_name) || ' TO ' || quote_ident(grantee) FROM information_schema.table_privileges WHERE grantee NOT IN ('root','admin','public') AND table_schema NOT IN ('pg_catalog','information_schema','crdb_internal','pg_extension') GROUP BY table_schema, table_name, grantee;
" 2>/dev/null | grep -E '^(CREATE|GRANT|ALTER) ' || true
}

apply_acl() {
  local s f n; s=$(date +%s); f=$(mktemp)
  acl_sql | sed 's/$/;/' > "$f"; n=$(wc -l < "$f")
  if [ "$n" -eq 0 ]; then rm -f "$f"; rec sync acl fail "$s" "no ACL statements generated from main"; return 1; fi
  if bsql -d "$DB" < "$f" >/dev/null 2>&1; then rec sync acl ok "$s" "applied $n statements"
  else # slow path: one statement at a time so one bad statement does not skip the rest
    local bad=0 l; while IFS= read -r l; do bsql -d "$DB" -e "$l" >/dev/null 2>&1 || bad=$((bad+1)); done < "$f"
    [ $bad -eq 0 ] && rec sync acl ok "$s" "applied $n statements (slow path)" || rec sync acl fail "$s" "$bad of $n statements failed"
  fi
  rm -f "$f"
}

# Restore into the STABLE name. A restore under another name (new_db_name) and ALTER DATABASE RENAME does not work:
# views store db-qualified names (RESTORE rewrites them to the new name, and RENAME fails with 'cannot rename database
# because relation depends on it' - verified). So: pre-flight the backup chain, drop the old copy, restore under $DB.
cmd_sync() {
  wait_main || { echo "main db not ready"; return 1; }
  cmd_backup auto || { date +%s > "$STATE/last_sync"; return 1; }   # make the chain current first (backoff on failure)
  local s out rc; s=$(date +%s)
  if ! out=$(bsql --format=csv -e "SELECT count(*) FROM [SHOW BACKUP LATEST IN '$URI']" 2>&1); then
    date +%s > "$STATE/last_sync"; rec sync restore fail "$s" "preflight: backup not readable from backup cluster: $(echo "$out" | tail -1)"; return 1
  fi
  bsql -e "DROP DATABASE IF EXISTS $DB CASCADE" >/dev/null 2>&1
  for attempt in 1 2; do
    out=$(bsql --format=csv -e "RESTORE DATABASE $DB FROM LATEST IN '$URI'" 2>&1); rc=$?
    [ $rc -eq 0 ] && break
    bsql -e "DROP DATABASE IF EXISTS $DB CASCADE" >/dev/null 2>&1; sleep 5
  done
  date +%s > "$STATE/last_sync"
  if [ $rc -ne 0 ]; then rec sync restore fail "$s" "$(echo "$out" | tail -2 | tr '
' ' ')"; return 1; fi
  rec sync restore ok "$s" "job: $(echo "$out" | grep -E '^[0-9]+,' | tail -1)"
  apply_acl
  cmd_verify
}

due() { # file minutes
  local l=0; [ -f "$STATE/$1" ] && l=$(cat "$STATE/$1")
  [ $(( $(date +%s) - l )) -ge $(($2*60)) ]
}

case "${1:-loop}" in
  backup) shift; cmd_backup "${1:-auto}" ;;
  sync)   cmd_sync ;;
  verify) cmd_verify ;;
  loop)
    echo "handler loop: full=${FULL_MIN}m incr=${INCR_MIN}m sync=${SYNC_MIN}m"
    wait_main
    while true; do
      if due last_sync "$SYNC_MIN"; then cmd_sync
      elif due last_backup "$INCR_MIN"; then cmd_backup auto; fi
      sleep 30
    done ;;
  *) echo "usage: $0 loop|backup [full|incr]|sync|verify"; exit 2 ;;
esac
