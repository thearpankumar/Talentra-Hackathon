#!/usr/bin/env bash
# Deploy = git on the EC2 host (public repo, https, no credentials): clone or fast-forward /opt/talentra/repo, then optionally bring stacks up.
# Generated-on-server files stay out of git and survive pulls: infra/*/.env (gitignored), infra/*/certs-public, infra/*/logs.
# Usage: scripts/deploy.sh [--up] [--env]
#   --env  also copy the local gitignored infra/*/.env files to the host over ssh (tar, mode 600; never printed or committed)
set -euo pipefail
INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$INFRA/aws-scripts/common.sh"; load_state
REPO_URL="${REPO_URL:-https://github.com/thearpankumar/Talentra-Hackathon.git}"
BRANCH="${BRANCH:-master}"
SSH=(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")
"${SSH[@]}" "sudo mkdir -p /opt/talentra && sudo chown ubuntu:ubuntu /opt/talentra && cd /opt/talentra && \
  if [ -d repo/.git ]; then git -C repo fetch --depth 1 origin $BRANCH && git -C repo reset --hard FETCH_HEAD; \
  else git clone --depth 1 --branch $BRANCH $REPO_URL repo; fi && \
  chmod +x repo/infra/*/scripts/*.sh repo/infra/*/handler/*.sh repo/infra/scripts/*.sh 2>/dev/null; git -C repo log --oneline -1"
if [[ " $* " == *" --env "* ]]; then
  for s in backup-db main-db caddy monitoring; do [ -f "$INFRA/$s/.env" ] || echo "WARN: $s/.env missing locally"; done
  tar -C "$INFRA" -cf - $(cd "$INFRA" && ls */.env 2>/dev/null) | "${SSH[@]}" "tar -C /opt/talentra/repo/infra -xf - && chmod 600 /opt/talentra/repo/infra/*/.env && echo env copied"
fi
for s in backup-db main-db caddy; do
  "${SSH[@]}" "[ -f /opt/talentra/repo/infra/$s/.env ]" || echo "WARN: $s/.env missing on host - prod up will fail without it (use --env)"
done
[[ " $* " == *" --up "* ]] && bash "$INFRA/scripts/stack.sh" prod up
exit 0
