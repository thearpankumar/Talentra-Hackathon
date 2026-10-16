#!/bin/sh
# Builds /tmp/alertmanager.yml = alertmanager.base.yml + receivers chosen by env, then execs alertmanager.
#   SLACK_WEBHOOK_URL    -> Slack incoming webhook (channel via SLACK_CHANNEL, default #alerts)
#   ALERT_WEBHOOK_URL    -> generic JSON webhook (PagerDuty/Opsgenie/Teams relay, ntfy, ...)
#   SMTP_SMARTHOST (host:port) + SMTP_FROM + ALERT_EMAIL_TO [+ SMTP_USER/SMTP_PASSWORD] -> email
#   WATCHDOG_WEBHOOK_URL -> dead-man's-switch URL (e.g. a healthchecks.io ping)
# With none set, notifications are dropped (alerts remain visible in Alertmanager/Grafana) and a warning is logged.
set -eu
OUT=/tmp/alertmanager.yml
cp /etc/alertmanager/alertmanager.base.yml "$OUT"
N=0
{
  echo
  echo "receivers:"
  echo "  - name: blackhole"
  echo "  - name: notify"
  if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
    N=1
    echo "    slack_configs:"
    echo "      - api_url: '${SLACK_WEBHOOK_URL}'"
    echo "        channel: '${SLACK_CHANNEL:-#alerts}'"
    echo "        send_resolved: true"
    echo "        title: '[{{ .Status | toUpper }}] {{ .CommonLabels.alertname }} ({{ .CommonLabels.severity }})'"
    echo "        text: '{{ range .Alerts }}{{ .Annotations.summary }} {{ end }}'"
  fi
  if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    N=1
    echo "    webhook_configs:"
    echo "      - url: '${ALERT_WEBHOOK_URL}'"
    echo "        send_resolved: true"
  fi
  if [ -n "${SMTP_SMARTHOST:-}" ] && [ -n "${ALERT_EMAIL_TO:-}" ]; then
    N=1
    echo "    email_configs:"
    echo "      - to: '${ALERT_EMAIL_TO}'"
    echo "        from: '${SMTP_FROM:-alertmanager@localhost}'"
    echo "        smarthost: '${SMTP_SMARTHOST}'"
    if [ -n "${SMTP_USER:-}" ]; then
      echo "        auth_username: '${SMTP_USER}'"
      echo "        auth_password: '${SMTP_PASSWORD:-}'"
    fi
    echo "        require_tls: ${SMTP_REQUIRE_TLS:-true}"
    echo "        send_resolved: true"
  fi
  echo "  - name: watchdog"
  if [ -n "${WATCHDOG_WEBHOOK_URL:-}" ]; then
    echo "    webhook_configs:"
    echo "      - url: '${WATCHDOG_WEBHOOK_URL}'"
    echo "        send_resolved: false"
  fi
} >> "$OUT"
[ "$N" = 0 ] && echo "WARNING: no notifier configured (set SLACK_WEBHOOK_URL / ALERT_WEBHOOK_URL / SMTP_*); alerts are only visible in the UI" >&2
/bin/amtool check-config "$OUT" >&2 || true
exec /bin/alertmanager --config.file="$OUT" --storage.path=/alertmanager "$@"
