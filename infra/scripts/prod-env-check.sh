#!/usr/bin/env bash
# Verifies on the EC2 host that each stack's .env defines every variable listed in its .env.example (non-comment, KEY=...),
# and that required ones are non-empty. Prints variable NAMES only, never values.
set -uo pipefail
INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$INFRA/aws-scripts/ssh.sh" 'cd /opt/talentra/repo/infra; rc=0
OPTIONAL="BACKUP_S3_URI SLACK_WEBHOOK_URL ALERT_WEBHOOK_URL SMTP_SMARTHOST SMTP_USER SMTP_PASSWORD ALERT_EMAIL_TO WATCHDOG_WEBHOOK_URL AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN GRAFANA_SECRET_KEY"
for s in backup-db main-db caddy monitoring; do
  [ -f $s/.env ] || { echo "MISSING FILE $s/.env"; rc=1; continue; }
  for k in $(grep -E "^[A-Z][A-Z0-9_]*=" $s/.env.example | cut -d= -f1); do
    line=$(grep -E "^$k=" $s/.env | head -1)
    if [ -z "$line" ]; then echo "$s: $k not defined"; rc=1
    elif [ -z "${line#*=}" ] && ! echo " $OPTIONAL " | grep -q " $k "; then echo "$s: $k is empty"; rc=1; fi
  done
  echo "$s: checked, perms $(stat -c %a $s/.env)"
done
[ $rc = 0 ] && echo "ALL REQUIRED VARIABLES SET"; exit $rc'
