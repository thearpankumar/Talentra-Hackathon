#!/usr/bin/env bash
# stack.sh <local|prod> <up|down|logs|ps|restart|pull> [stack ...]
# Order on up: network+volume -> backup-db -> main-db -> monitoring -> caddy.  Reverse on down.
# prod runs the same command over SSH on the EC2 host in /opt/talentra/infra (use deploy.sh first).
set -euo pipefail
INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVN="${1:?local|prod}"; ACT="${2:?action}"; shift 2
ALL=(backup-db main-db monitoring caddy)
STACKS=("$@"); [ ${#STACKS[@]} -eq 0 ] && STACKS=("${ALL[@]}")
if [ "$ENVN" = prod ]; then FILE=docker-compose.prod.yml; else FILE=docker-compose.yml; fi

run() { # run <shell cmd> in context (local or remote)
  if [ "$ENVN" = prod ]; then bash "$INFRA/aws-scripts/ssh.sh" "$1"; else bash -c "$1"; fi
}
dir() { if [ "$ENVN" = prod ]; then echo "/opt/talentra/infra/$1"; else echo "$INFRA/$1"; fi; }
# certs-public/ca.crt must be a FILE (Docker turns a missing single-file bind mount into a directory): repair local leftovers.
fix_certs() { local d; for d in main-db backup-db; do [ -d "$INFRA/$d/certs-public/ca.crt" ] && { echo "repairing $d/certs-public/ca.crt (was a directory)"; rm -rf "$INFRA/$d/certs-public/ca.crt"; }; mkdir -p "$INFRA/$d/certs-public"; done; true; }
prep() {
  [ "$ENVN" = local ] && fix_certs
  run "docker network inspect talentra-edge >/dev/null 2>&1 || docker network create talentra-edge >/dev/null"
  run "docker volume inspect talentra_backup_share >/dev/null 2>&1 || docker volume create talentra_backup_share >/dev/null"
}
dc() { echo "cd '$(dir "$1")' && docker compose -f $FILE ${@:2}"; }
has() { if [ -f "$INFRA/$1/$FILE" ]; then return 0; fi; echo "(skip $1: no $FILE locally)"; return 1; }
reverse() { local i; for ((i=${#STACKS[@]}-1;i>=0;i--)); do echo "${STACKS[$i]}"; done; }

case "$ACT" in
  up)      prep; for s in "${STACKS[@]}"; do has "$s" || continue; echo "== up $ENVN $s"; run "$(dc "$s" up -d --build)"; done ;;
  down)    for s in $(reverse); do has "$s" || continue; echo "== down $ENVN $s"; run "$(dc "$s" down)"; done ;;
  restart) for s in "${STACKS[@]}"; do run "$(dc "$s" restart)"; done ;;
  pull)    for s in "${STACKS[@]}"; do run "$(dc "$s" pull)"; done ;;
  ps)      for s in "${STACKS[@]}"; do echo "== $s"; run "$(dc "$s" ps)"; done ;;
  logs)    for s in "${STACKS[@]}"; do echo "== $s"; run "$(dc "$s" logs --tail=${TAIL:-80})"; done ;;
  *) echo "unknown action $ACT"; exit 2 ;;
esac
