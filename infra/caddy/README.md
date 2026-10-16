# caddy

Local: `docker compose up -d` serves HTTP on :8081 by Host header (`db.localhost`, `backup.localhost`, `monitoring.localhost`; basic auth local/local on the consoles).
Prod: `docker compose -f docker-compose.prod.yml up -d` with `.env` (see `.env.example`; generate BASIC_AUTH_HASH with `caddy hash-password`). Requires DNS A records for talentra./db./backup./monitoring. and ports 80/443; start after backup-db and main-db (their `certs-public/` directories are mounted so Caddy trusts the Cockroach CA). `monitoring.` proxies to `grafana:3000` on talentra-edge (infra/monitoring).


## Metrics
Global `metrics` option plus an internal site `:9180 { metrics /metrics }` (both Caddyfile and Caddyfile.local). Port 9180 is not published on the host; Prometheus scrapes `caddy:9180` over `talentra-edge`. Validate: `caddy validate --config /etc/caddy/Caddyfile` (prod needs the `ACME_EMAIL`/`*_HOST`/`BASIC_AUTH_*` env vars and `/certs/{main,backup}/ca.crt`). The prod compose mounts the `certs-public/` directories, never the `ca.crt` file (Docker would create a directory if it did not exist yet).
