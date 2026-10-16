#!/usr/bin/env bash
# Copy infra/ to /opt/talentra/infra on the EC2 host (tar over ssh; no rsync needed), then optionally bring stacks up.
# Excludes logs/, certs-public/ (generated on the host) and local.env. Keeps remote .env files (we only overwrite what we send;
# .env files ARE sent if present locally: create them from .env.example first).
# Usage: scripts/deploy.sh [--up]
set -euo pipefail
INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$INFRA/aws-scripts/common.sh"; load_state
SSH=(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
for s in backup-db main-db caddy; do
  [ -f "$INFRA/$s/.env" ] || echo "WARN: $s/.env missing locally (copy $s/.env.example and fill it in) - prod up will fail without it"
done
"${SSH[@]}" "sudo mkdir -p /opt/talentra/infra && sudo chown -R ubuntu:ubuntu /opt/talentra"
tar -C "$INFRA" --exclude=logs --exclude=certs-public --exclude=local.env --exclude='aws-scripts' --exclude=.git -czf - . \
  | "${SSH[@]}" "tar -C /opt/talentra/infra -xzf - && chmod 600 /opt/talentra/infra/*/.env 2>/dev/null; chmod +x /opt/talentra/infra/*/scripts/*.sh /opt/talentra/infra/*/handler/*.sh 2>/dev/null; echo deployed"
[ "${1:-}" = "--up" ] && bash "$INFRA/scripts/stack.sh" prod up
exit 0
