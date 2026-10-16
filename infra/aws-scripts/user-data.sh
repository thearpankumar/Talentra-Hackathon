#!/bin/bash
# EC2 user-data: Docker Engine + compose plugin, kernel/ulimit tuning, chrony. Log: /var/log/talentra-bootstrap.log
set -euxo pipefail
exec > >(tee -a /var/log/talentra-bootstrap.log) 2>&1
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y ca-certificates curl gnupg chrony jq htop iotop unzip rsync ufw fail2ban apache2-utils

# --- Docker official repo ---
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# deb822 source per https://docs.docker.com/engine/install/ubuntu/
cat > /etc/apt/sources.list.d/docker.sources <<DEB
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
DEB
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "local",
  "log-opts": { "max-size": "50m", "max-file": "5" },
  "default-ulimits": { "nofile": { "Name": "nofile", "Hard": 1048576, "Soft": 1048576 } }
}
JSON
systemctl enable --now docker
usermod -aG docker ubuntu

# --- kernel / limits (CockroachDB production checklist) ---
cat > /etc/sysctl.d/99-talentra.conf <<'SYS'
vm.max_map_count = 262144
vm.swappiness = 1
net.core.somaxconn = 4096
fs.file-max = 2097152
SYS
sysctl --system
swapoff -a || true
sed -i '/\sswap\s/ s/^/#/' /etc/fstab || true
cat > /etc/security/limits.d/99-talentra.conf <<'LIM'
*    soft nofile 1048576
*    hard nofile 1048576
root soft nofile 1048576
root hard nofile 1048576
LIM

# --- time sync: Amazon Time Sync Service (CockroachDB needs tight clock sync) ---
if ! grep -q 169.254.169.123 /etc/chrony/chrony.conf; then
  sed -i '1i server 169.254.169.123 prefer iburst minpoll 4 maxpoll 4' /etc/chrony/chrony.conf
fi
systemctl enable chrony
systemctl restart chrony

# --- hardening: fail2ban (sshd) + ufw. Docker-published ports bypass ufw; the AWS security group is the real perimeter. ---
printf '[sshd]
enabled = true
maxretry = 5
bantime = 1h
' > /etc/fail2ban/jail.d/sshd.local
systemctl enable --now fail2ban
ufw allow 22/tcp; ufw allow 80/tcp; ufw allow 443/tcp; ufw allow 26257:26259/tcp
ufw --force enable

# --- workspace ---
mkdir -p /opt/talentra
chown ubuntu:ubuntu /opt/talentra
touch /var/lib/talentra-bootstrap.done
