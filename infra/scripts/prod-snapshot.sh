#!/usr/bin/env bash
# Pull non-secret live state from the host into infra/prod-snapshot/ (gitignored) for diffing against the repo:
# git working tree status/diff, host config files, docker/ufw/fail2ban/sysctl state, container list. .env files are NEVER copied.
set -euo pipefail
INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$INFRA/prod-snapshot"; mkdir -p "$OUT"
run() { bash "$INFRA/aws-scripts/ssh.sh" "$1"; }
run 'cd /opt/talentra/repo && git log --oneline -1 && git status --short && git diff' > "$OUT/server-git.txt" 2>&1 || true
run 'cat /etc/docker/daemon.json' > "$OUT/daemon.json" 2>&1 || true
run 'sudo ufw status numbered' > "$OUT/ufw.txt" 2>&1 || true
run 'sudo cat /etc/fail2ban/jail.d/sshd.local' > "$OUT/fail2ban-sshd.local" 2>&1 || true
run 'cat /etc/sysctl.d/99-talentra.conf /etc/security/limits.d/99-talentra.conf' > "$OUT/sysctl-limits.txt" 2>&1 || true
run 'cat /etc/apt/sources.list.d/docker.sources; docker --version; docker compose version; lsb_release -ds; uname -r' > "$OUT/versions.txt" 2>&1 || true
run 'docker ps -a --format "{{.Names}}\t{{.Image}}\t{{.Status}}" | sort' > "$OUT/containers.tsv" 2>&1 || true
run 'crontab -l 2>&1; systemctl list-timers --no-pager 2>&1 | head -20' > "$OUT/cron-timers.txt" 2>&1 || true
run 'ls -la /opt/talentra /opt/talentra/repo/infra/*/ | grep -v "^total"' > "$OUT/layout.txt" 2>&1 || true
echo "snapshot in $OUT (gitignored). Diff e.g.: diff <(bash aws-scripts/ssh.sh 'cat /etc/docker/daemon.json') <(sed -n '/daemon.json/,/JSON/p' aws-scripts/user-data.sh)"
