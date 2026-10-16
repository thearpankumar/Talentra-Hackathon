#!/usr/bin/env bash
# Provision the single Talentra EC2 host. Creates REAL AWS resources (~$0.38/hr + EBS).
# Usage: bash infra/aws-scripts/provision.sh
# Env overrides: INSTANCE_TYPE (m6i.2xlarge) VOLUME_GB (500) VOLUME_IOPS (6000) VOLUME_TPUT (300)
#                OPEN_SQL (1: open 26257/26258 to caller IP only; 0: keep closed) EXTRA_SQL_CIDRS (comma list)
#                SSH_CIDR (default: caller IP/32)  DRY_RUN=1 (only run-instances --dry-run, creates nothing)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

INSTANCE_TYPE="${INSTANCE_TYPE:-m6i.2xlarge}"
VOLUME_GB="${VOLUME_GB:-500}"
VOLUME_IOPS="${VOLUME_IOPS:-6000}"
VOLUME_TPUT="${VOLUME_TPUT:-300}"
OPEN_SQL="${OPEN_SQL:-1}"
DRY_RUN="${DRY_RUN:-0}"

mkdir -p "$CREDS_DIR"
if [ -f "$STATE_FILE" ] && [ "$DRY_RUN" != 1 ]; then
  . "$STATE_FILE"
  if [ -n "${INSTANCE_ID:-}" ]; then
    st=$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo gone)
    if [ "$st" != "terminated" ] && [ "$st" != "gone" ] && [ "$st" != "None" ]; then
      echo "Instance $INSTANCE_ID already exists ($st). Use start/stop/status.sh, or teardown.sh first." >&2; exit 1
    fi
  fi
fi

echo "== Caller identity"
aws_ sts get-caller-identity --query Account --output text | grep -q '^462035738739$' || { echo "Wrong AWS account" >&2; exit 1; }

MYIP="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
SSH_CIDR="${SSH_CIDR:-$MYIP/32}"
echo "== Caller IP: $MYIP  (SSH/SQL restricted to $SSH_CIDR)"

# --- key pair ---
if aws_ ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  [ -f "$KEY_FILE" ] || { echo "Key pair $KEY_NAME exists in AWS but $KEY_FILE is missing. Delete the AWS key pair or restore the PEM." >&2; exit 1; }
  echo "== Key pair exists, reusing"
elif [ "$DRY_RUN" = 1 ]; then
  echo "== [dry-run] would create key pair"
else
  echo "== Creating key pair"
  aws_ ec2 create-key-pair --key-name "$KEY_NAME" --key-type ed25519 --query KeyMaterial --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE" 2>/dev/null || true
  # Windows: strip inherited ACLs so OpenSSH accepts the key
  if command -v icacls >/dev/null 2>&1; then
    WINPATH="$(cygpath -w "$KEY_FILE" 2>/dev/null || echo "$KEY_FILE")"
    icacls "$WINPATH" /inheritance:r /grant:r "${USERNAME:-$USER}:(R)" >/dev/null 2>&1 || true
  fi
fi

# --- security group in default VPC ---
VPC_ID=$(aws_ ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
[ "$VPC_ID" != "None" ] || { echo "No default VPC" >&2; exit 1; }
SG_ID=$(aws_ ec2 describe-security-groups --filters Name=group-name,Values="$SG_NAME" Name=vpc-id,Values="$VPC_ID" --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG_ID" = "None" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    SG_ID="sg-00000000"; echo "== [dry-run] would create SG"
  else
    echo "== Creating security group"
    SG_ID=$(aws_ ec2 create-security-group --group-name "$SG_NAME" --description "Talentra DB host" --vpc-id "$VPC_ID" \
      --tag-specifications 'ResourceType=security-group,Tags=[{Key=Name,Value=talentra-db}]' --query GroupId --output text)
    rule() { aws_ ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port "$1" --cidr "$2" >/dev/null; }
    rule 22 "$SSH_CIDR"; rule 80 0.0.0.0/0; rule 443 0.0.0.0/0
    if [ "$OPEN_SQL" = 1 ]; then
      for c in "$SSH_CIDR" $(echo "${EXTRA_SQL_CIDRS:-}" | tr ',' ' '); do rule 26257 "$c"; rule 26258 "$c"; done
    fi
  fi
else
  echo "== Security group exists: $SG_ID (rules unchanged; edit manually if your IP changed)"
fi

# --- AMI (Ubuntu 24.04 LTS amd64) ---
AMI_ID=$(aws_ ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
echo "== AMI: $AMI_ID"

RUN_ARGS=(ec2 run-instances --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME"
  --security-group-ids "$SG_ID"
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$VOLUME_GB,VolumeType=gp3,Iops=$VOLUME_IOPS,Throughput=$VOLUME_TPUT,Encrypted=true,DeleteOnTermination=true}"
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2"
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=talentra-db},{Key=project,Value=talentra}]' 'ResourceType=volume,Tags=[{Key=Name,Value=talentra-db},{Key=project,Value=talentra}]'
  --user-data "$(cat "$SCRIPT_DIR/user-data.sh")")

if [ "$DRY_RUN" = 1 ]; then
  aws_ "${RUN_ARGS[@]}" --dry-run || true   # success shows DryRunOperation error
  echo "== dry-run done"; exit 0
fi

echo "== Launching instance"
INSTANCE_ID=$(aws_ "${RUN_ARGS[@]}" --query 'Instances[0].InstanceId' --output text)
echo "   $INSTANCE_ID"
aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"

echo "== Allocating Elastic IP"
read -r EIP_ALLOC PUBLIC_IP < <(aws_ ec2 allocate-address --domain vpc \
  --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=Name,Value=talentra-db}]' --query '[AllocationId,PublicIp]' --output text)
aws_ ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC" >/dev/null

cat > "$STATE_FILE" <<EOS
INSTANCE_ID=$INSTANCE_ID
EIP_ALLOC_ID=$EIP_ALLOC
PUBLIC_IP=$PUBLIC_IP
SG_ID=$SG_ID
KEY_NAME=$KEY_NAME
KEY_FILE="$KEY_FILE"
REGION=$AWS_REGION_NAME
EOS
echo
echo "=============================================="
echo " Elastic IP : $PUBLIC_IP"
echo " Instance   : $INSTANCE_ID"
echo " State file : $STATE_FILE"
echo " Point DNS A records (talentra / db. / backup. / ui.thearpanhq.in) at $PUBLIC_IP"
echo " Bootstrap (docker etc.) takes ~2-3 min; check /var/lib/talentra-bootstrap.done via ssh.sh"
echo "=============================================="
