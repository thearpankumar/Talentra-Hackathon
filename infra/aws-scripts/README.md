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

## Host setup (Ubuntu 24.04), exact commands
`user-data.sh` does all of this on first boot (log: `/var/log/talentra-bootstrap.log`); the same commands can be re-run by hand over `ssh.sh`. It follows Docker's official apt-repository method (https://docs.docker.com/engine/install/ubuntu/), with the deb822 source format.
```
sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg chrony jq htop iotop unzip rsync ufw fail2ban apache2-utils
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources <<EOT
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOT
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu
```
Also set by user-data: `/etc/docker/daemon.json` (local log driver 50m x5, nofile 1048576), sysctl `vm.max_map_count=262144`, `vm.swappiness=1`, `net.core.somaxconn=4096`, swap off, `limits.d` nofile 1048576, chrony against the Amazon Time Sync Service (169.254.169.123), fail2ban `sshd` jail (5 tries, 1 h ban), ufw (22, 80, 443, 26257-26259). Docker-published ports bypass ufw, so the AWS security group is the real perimeter. `apache2-utils` gives `htpasswd`; Caddy hashes come from `docker run caddy:2-alpine caddy hash-password`.
Installed on this host (Oct 2026): Docker Engine 29.9.1, Compose v5.6.0, kernel 7.0.0-1014-aws.

## Deploy through git
The repo is public, so the host needs no credentials: `make -C infra deploy-prod` clones/fast-forwards `/opt/talentra/repo` (depth 1) and copies the gitignored `infra/*/.env` files over ssh (tar, mode 600). Generated files (`.env`, `certs-public/`, `logs/`) are gitignored and survive pulls. `make -C infra up-prod` then starts network, backup-db, main-db, monitoring, caddy.
Windows note: Git Bash's own ssh can fail to parse the ed25519 key (`error in libcrypto`); `common.sh` therefore prefers `C:\Windows\System32\OpenSSH` and converts the key path.
