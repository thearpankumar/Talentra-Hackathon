# infra/aws-scripts

Scripts for the single EC2 host (profile `arpan-aws`, us-east-1, account 462035738739). Run in Git Bash from the repo root. Sizing rationale: [EC2_SIZING.md](EC2_SIZING.md).

| Script | What it does |
|---|---|
| `provision.sh` | Key pair `talentra-key` (PEM saved to `.aws-creds/`), security group (22 + SQL ports to your IP only; 80/443 public), Ubuntu 24.04 m6i.2xlarge, 500 GB encrypted gp3 (6000 IOPS/300 MB/s), IMDSv2, user-data (Docker + compose, sysctl, ulimits, chrony), Elastic IP, writes `.aws-creds/state.env` |
| `start.sh` | Start the instance, wait until running and SSH is open (EIP is kept) |
| `stop.sh` | Stop the instance (EBS and EIP still billed, ~$0.075/hr) |
| `status.sh` | State, IP, cost note |
| `ssh.sh` | `ssh -i key ubuntu@EIP [cmd]` |
| `teardown.sh` | **DESTRUCTIVE**: terminates instance, deletes the data volume, releases EIP, deletes SG/key/state. Requires typing a confirmation |

## Usage
```
bash infra/aws-scripts/provision.sh              # creates real resources
DRY_RUN=1 bash infra/aws-scripts/provision.sh    # checks only, creates nothing
bash infra/aws-scripts/ssh.sh 'cat /var/lib/talentra-bootstrap.done && docker compose version'
```
Options (env): `INSTANCE_TYPE`, `VOLUME_GB`, `VOLUME_IOPS`, `VOLUME_TPUT`, `OPEN_SQL=0|1`, `EXTRA_SQL_CIDRS=a.b.c.d/32,...`, `SSH_CIDR`.

After provisioning, create DNS A records for `talentra.`, `db.`, `backup.`, `ui.` under thearpanhq.in pointing at the printed EIP, then copy `infra/` to `/opt/talentra` and bring up the compose stacks.

If your public IP changes, add it to the SG:
`aws --profile arpan-aws --region us-east-1 ec2 authorize-security-group-ingress --group-id <SG_ID> --protocol tcp --port 22 --cidr <new>/32`

`.aws-creds/` is gitignored; never commit the PEM or state file.
