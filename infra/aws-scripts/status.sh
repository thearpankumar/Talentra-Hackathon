#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"; load_state
aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,State.Name,PublicIpAddress]' --output text | \
  awk '{printf "Instance: %s\nType: %s\nState: %s\nPublic IP: %s\n",$1,$2,$3,$4}'
echo "EIP (static): $PUBLIC_IP"
echo "Cost: running ~\$0.384/hr compute + \$0.005/hr IPv4 + ~\$0.07/hr EBS (500GB gp3, 6000 IOPS/300MBps ~\$52/mo)."
echo "      stopped: EBS + IPv4 only (~\$0.075/hr, ~\$55/mo)."
