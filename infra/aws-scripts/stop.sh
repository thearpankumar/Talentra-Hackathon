#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"; load_state
echo "Tip: 'docker compose down' the stacks first for a clean shutdown (docker stops containers on shutdown anyway)."
aws_ ec2 stop-instances --instance-ids "$INSTANCE_ID" >/dev/null
aws_ ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"
echo "Stopped. EIP $PUBLIC_IP retained. Compute billing paused; EBS (~\$50/mo) and public IPv4 (\$0.005/hr) still accrue."
