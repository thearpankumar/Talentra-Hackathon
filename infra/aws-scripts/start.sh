#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"; load_state
aws_ ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
echo "Starting $INSTANCE_ID ..."
aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"
echo "Running. Waiting for SSH on $PUBLIC_IP ..."
for i in $(seq 1 60); do
  if (exec 3<>/dev/tcp/"$PUBLIC_IP"/22) 2>/dev/null; then echo "SSH ready: bash infra/aws-scripts/ssh.sh"; exit 0; fi
  sleep 5
done
echo "SSH not reachable after 5 min (is your IP still allowed in SG $SG_ID?)" >&2; exit 1
