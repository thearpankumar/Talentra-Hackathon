# EC2 sizing: single host for Talentra (us-east-1)

Decision: **m6i.2xlarge (8 vCPU / 32 GiB, x86_64), 500 GB encrypted gp3 at 6000 IOPS / 300 MB/s**, Ubuntu 24.04 LTS. Approved shape kept; only the gp3 performance was raised above baseline.

## Sources
- CockroachDB production checklist (https://www.cockroachlabs.com/docs/stable/recommended-production-settings): at least 4 vCPU per node (8 recommended), 4 GiB RAM per vCPU, disk capable of ~500 IOPS and 30 MB/s per vCPU, no burstable instances, `--cache` and `--max-sql-memory` set explicitly when sharing a host, swap off, tight clock sync.
- AWS EBS gp3: 3000 IOPS / 125 MB/s included, extra IOPS $0.005/IOPS-mo, extra throughput ~$0.04/MBps-mo, $0.08/GB-mo (AWS EBS pricing and storage blog).

## Honest assessment
Four CockroachDB nodes on 8 vCPU is **2 vCPU each, below the 4 vCPU/node guidance**. It is a hackathon/demo topology, not production. It works because the 3 main nodes are mostly idle (single leaseholder serves writes, follower reads are light) and the backup node is the one that gets hammered during the 10M-row load. RAM is 8 GiB per node-equivalent, which is fine. Do not benchmark this for capacity claims.

## Memory and CPU budget (32 GiB total)
Cockroach RSS runs about cache + SQL memory + ~1-1.5 GiB Go/overhead. Fixed values (not percentages) so the four nodes cannot oversubscribe.

| Container | --cache | --max-sql-memory | Approx RSS | CPU limit (cpus) |
|---|---|---|---|---|
| main node 1 (preferred leaseholder) | 3 GiB | 2 GiB | ~6 GiB | 2.0 |
| main node 2 | 2 GiB | 1.5 GiB | ~4.5 GiB | 1.5 |
| main node 3 | 2 GiB | 1.5 GiB | ~4.5 GiB | 1.5 |
| backup cockroach (standalone) | 4 GiB | 3 GiB | ~8.5 GiB | 3.0 (no limit while bulk loading is fine) |
| HAProxy | - | - | 0.1 GiB | 0.25 |
| Caddy | - | - | 0.2 GiB | 0.25 |
| backup-handler / scheduler + loader | - | - | 1-2 GiB | 1.0 |
| OS + Docker + page cache | - | - | ~3 GiB | - |
| Total | | | ~28-29 GiB | |

CPU limits are caps and deliberately oversubscribed (sum > 8) because the load phases do not overlap. Set `mem_limit` about 1 GiB above each node's RSS so a runaway OOM-kills one container, not the host. Use `--cache` and `--max-sql-memory` as absolute sizes, and `stop_grace_period: 60s` for cockroach containers.

## Disk layout
Single 500 GB gp3 root volume (AMI /dev/sda1), no extra mounts.
- `/var/lib/docker/volumes`: one named volume per cockroach node. 10M profile-centric rows with indexes: roughly 30-60 GB logical per copy; main is 3x replicated (RF=3, so 90-180 GB) plus backup copy 1x. Fits, with headroom under CockroachDB's ~60% fullness guidance. Do not load the full 100M applications; that would not fit.
- Keep >30% free. Prune docker images and set Docker log rotation (done in user-data).
- gp3 sizing: guidance is 500 IOPS and 30 MB/s per vCPU, so 8 vCPU gives 4000 IOPS and 240 MB/s. Baseline (3000/125) is under that, so **6000 IOPS / 300 MB/s** (about +$15 + $7/mo). m6i.2xlarge EBS bandwidth is limited (baseline roughly 2.5 Gbps, bursts higher); 300 MB/s is chosen to stay near that, and more throughput than the instance link buys nothing. Can be raised live with `aws ec2 modify-volume`.

## x86 vs Graviton
m7g.2xlarge is about 15% cheaper ($0.3264/hr vs $0.384/hr) and CockroachDB publishes arm64 builds, but the ingestion loader, HAProxy/Caddy images and any amd64-only tooling add risk for ~$0.06/hr. Stay x86.

## Network / SQL access
SQL is pgwire, not HTTP, so Caddy cannot front it without the caddy-l4 plugin and a custom build. Chosen: **HAProxy on 26257 (main) and the backup node on 26258, TLS enforced by CockroachDB, host SG opens them to the caller's IP only** (set `OPEN_SQL=0` to close, `EXTRA_SQL_CIDRS` to add). Caddy 443 serves `ui.` (DB Console) and the app. Never open 26257/26258 to 0.0.0.0/0.
Because IMDSv2 hop limit is 2, containers can still reach instance metadata if needed; set to 1 if no container should.

## Cost (us-east-1, on-demand)
- Compute $0.384/hr (~$280/mo running 24x7).
- EBS 500 GB gp3: $40 + extra perf ~$22 = ~$52/mo (~$0.07/hr), charged even when stopped.
- Public IPv4 $0.005/hr (~$3.65/mo), charged while stopped too.
- Running ~$0.46/hr; stopped ~$0.075/hr. A 3-day demo is about $35. Stop the instance between sessions; data and EIP persist.

## Risks
1. 2 vCPU per node: load causes latency spikes; run the backup load with main idle.
2. Single host, single AZ, single volume: no real HA. The 3-node cluster is topology, not durability. Back up to S3 if the data matters.
3. EBS bandwidth caps bulk-load speed; expect hours, not minutes, for 10M rows with indexes (ingest with IMPORT/COPY in batches, add secondary indexes after).
4. Home IP changes lock out SSH/SQL: update SG rules (`aws ec2 authorize-security-group-ingress`) or edit in console.
5. Stop/start keeps EIP and disk, but containers need `restart: unless-stopped` to come back.
6. Let's Encrypt needs DNS A records for talentra./db./backup./ui. pointed at the EIP before Caddy starts, plus ports 80/443 open.

## Applied budget (deployed Oct 2026, host has 30 GiB usable)
The defaults above exceeded 30 GiB once monitoring (about 3.3 GiB) and the handler were added, so `infra/main-db/.env` and `infra/backup-db/.env` set (in compose via `N*_CACHE`, `N*_SQL_MEM`, `N*_MEM_LIMIT`, `BK_*`): main node1 cache 2 GiB / sql 1.5 GiB / limit 5 g; node2 and node3 1.5 GiB / 1 GiB / 4 g; backup 2 GiB / 1.5 GiB / 5 g. Container limits total about 18 g (databases) + 3.3 g (monitoring) + 1.5 g (handler) + 0.75 g (haproxy, caddy) = about 23.6 g, leaving page cache headroom. Raise caches again if the host is resized.
