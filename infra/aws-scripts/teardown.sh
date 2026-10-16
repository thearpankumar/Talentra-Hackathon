#!/usr/bin/env bash
# !!! DESTRUCTIVE !!! Terminates the instance (EBS volume + ALL DATABASE DATA deleted),
# releases the Elastic IP, deletes the security group, key pair and local PEM/state.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"; load_state
echo "This will PERMANENTLY DESTROY $INSTANCE_ID, its data volume, EIP $PUBLIC_IP, SG $SG_ID and key $KEY_NAME."
read -r -p "Type 'destroy-talentra' to continue: " ans
[ "$ans" = "destroy-talentra" ] || { echo aborted; exit 1; }
aws_ ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null
aws_ ec2 wait instance-terminated --instance-ids "$INSTANCE_ID"
aws_ ec2 release-address --allocation-id "$EIP_ALLOC_ID" || true
aws_ ec2 delete-security-group --group-id "$SG_ID" || echo "SG delete failed (retry shortly)"
aws_ ec2 delete-key-pair --key-name "$KEY_NAME" || true
rm -f "$KEY_FILE" "$STATE_FILE"
echo "Torn down."
