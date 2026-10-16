#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"; load_state
exec ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP" "$@"
