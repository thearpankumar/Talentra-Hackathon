#!/usr/bin/env bash
# Validates Prometheus config + rules for BOTH environments with promtool (no running stack needed).
# Usage: bash scripts/promtool-check.sh        (run from infra/monitoring)
set -euo pipefail
cd "$(dirname "$0")/.."
IMG=prom/prometheus:v3.5.0
for env in local prod; do
  echo "=== promtool: $env ==="
  tmp="$PWD/.test/promtool-$env"; rm -rf "$tmp"; mkdir -p "$tmp"
  mkdir -p "$tmp/etc/scrape.d" "$tmp/certs/main" "$tmp/certs/backup"
  cp prometheus/prometheus.yml "$tmp/etc/"
  cp -r prometheus/scrape.d/common "$tmp/etc/scrape.d/common"
  cp -r "prometheus/scrape.d/$env" "$tmp/etc/scrape.d/env"
  cp -r prometheus/targets/$env "$tmp/etc/targets"
  cp -r prometheus/rules "$tmp/etc/rules"
  # promtool validates ca_file existence: provide placeholder certs (content is not parsed as a chain by `check config`)
  for c in main backup; do cp "tools/vendor/placeholder-ca.crt" "$tmp/certs/$c/ca.crt" 2>/dev/null || : > "$tmp/certs/$c/ca.crt"; done
  docker run --rm --entrypoint sh -v "$tmp/etc:/etc/prometheus:ro" -v "$tmp/certs:/certs:ro" "$IMG" -c \
    "promtool check config /etc/prometheus/prometheus.yml && promtool check rules /etc/prometheus/rules/*.yml"
  rm -rf "$tmp"
done
