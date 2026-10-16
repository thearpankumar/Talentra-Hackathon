# Alignment report: docs vs infra vs schema

Status: skeleton. Counts are filled in by the lead after the EC2 run. Item-level detail: `docs/_staging/audit_items.csv`.

## Scope
Static walk of `docs/` (00 milestones, 02 ADRs, 06 business rules, 08 compliance, 09 boundaries, 13 RBAC enforcement, 14/15 security, 20 backup plan, doc READMEs) against `infra/` (schema ddl + parts, main-db, backup-db, caddy, monitoring, scripts, Makefile). No database was changed by the audit.

## What was changed
* `infra/README.md`: sync now documented as drop + restore under the stable name (no rename-swap), op names `restore/acl/verify`, deviations table.
* `infra/main-db/docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`: `INCR_EVERY_MIN` default 15 (local) / 5 (prod) to match docs/20.
* `infra/monitoring/prometheus/rules/00-recording.yml`: backup RPO staleness target 7200 s -> 1800 s.
* `infra/caddy/Caddyfile`: HSTS header on all public hostnames (docs/15 section 3).
* `docs/tools/build_justification.py` -> `docs/07_schema_justification.md`: new section 12 (deviations and overrides); lint 0 errors.
* `docs/tools/sec_doc15.py` -> `docs/15_security_design_note.md`: deployed-TLS/auth deviations paragraph, local.env statement corrected.

## Counts (to be filled by lead)
| Metric | Value |
|---|---|
| Tables / views / routines / roles in `talentra` | TBD |
| Business rules implemented / deferred (app, job) / gap | TBD |
| Tests passed / failed / skipped (`make test-schema`) | TBD |
| Local acceptance (`make test-local`) | TBD |

## Test commands to run on EC2
```
cd infra
make schema-local DB=talentra        # (prod: SCHEMA_DSN=... make schema-prod)
make test-schema DB=talentra
make test-local
make license-check
make config-check
```

## Deferred to EC2
* Full 1306-table apply timing, 1M-profile seed, performance plan (docs/11) measurements, RTO/RPO runs into `docs/20_backup_restore_log.xlsx`.
* Backup encryption, `revision_history`, retention pruning, S3 object-lock bucket, `backup_operator` login for the handler.
* Certificate-only logins, host-based auth, per-service client certificates, 90-day client cert rotation.
* Application-layer rules (APP_LAYER / JOB mechanisms in 06): see CSV rows with status `deferred`.

## Gaps
See rows with status `gap` in `docs/_staging/audit_items.csv`.
