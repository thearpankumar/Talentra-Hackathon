#!/usr/bin/env bash
# Git Bash ssh can fail to parse ed25519 keys (libcrypto); prefer the Windows OpenSSH client when present
[ -d /c/Windows/System32/OpenSSH ] && export PATH="/c/Windows/System32/OpenSSH:$PATH"
# Shared helpers. Source me; do not run.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'   # stop Git Bash mangling /aws/service/... paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CREDS_DIR="$REPO_ROOT/.aws-creds"
STATE_FILE="$CREDS_DIR/state.env"
KEY_NAME="talentra-key"
KEY_FILE="$CREDS_DIR/talentra-key.pem"
SG_NAME="talentra-db-sg"
AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-arpan-aws}"
AWS_REGION_NAME="${AWS_REGION_NAME:-us-east-1}"
aws_() { aws --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" --no-cli-pager "$@"; }
load_state() {
  [ -f "$STATE_FILE" ] || { echo "No $STATE_FILE - run provision.sh first." >&2; exit 1; }
  # shellcheck disable=SC1090
  . "$STATE_FILE"
  if [ -d /c/Windows/System32/OpenSSH ] && command -v cygpath >/dev/null; then KEY_FILE="$(cygpath -m "$KEY_FILE")"; fi
}
