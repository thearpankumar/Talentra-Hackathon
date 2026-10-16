<div align="center">

# Talentra

**A LinkedIn / Naukri-class job search and demand management database, designed, secured and deployed on CockroachDB.**

[![CockroachDB](https://img.shields.io/badge/CockroachDB-v26.2-6933FF?logo=cockroachlabs&logoColor=white)](https://www.cockroachlabs.com/)
[![Tables](https://img.shields.io/badge/tables-1%2C306-blue)](docs/05_data_dictionary.xlsx)
[![Business rules](https://img.shields.io/badge/business%20rules-457-success)](docs/06_business_rules.xlsx)
[![ADRs](https://img.shields.io/badge/ADRs-43-orange)](docs/02_adr_log.md)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](infra/)
[![AWS EC2](https://img.shields.io/badge/AWS-EC2-FF9900?logo=amazonaws&logoColor=white)](infra/aws-scripts/)
[![Grafana](https://img.shields.io/badge/Grafana-Prometheus%20%2B%20Loki-F46800?logo=grafana&logoColor=white)](infra/monitoring/)
[![Caddy](https://img.shields.io/badge/Caddy-auto%20TLS-1F88C0?logo=caddy&logoColor=white)](infra/caddy/)
[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=white)](ingestion-engine/)
[![LangGraph](https://img.shields.io/badge/LangGraph-agentic-1C3C3C)](ingestion-engine/)
[![FastAPI](https://img.shields.io/badge/FastAPI-UI-009688?logo=fastapi&logoColor=white)](ingestion-engine/)
[![License](https://img.shields.io/badge/license-Apache%202.0-lightgrey)](LICENSE)

</div>

---

## At a glance

- **Product:** the full data backbone of a job platform, covering candidates, employers, recruiters, consultants, gigs, billing, messaging and analytics
- **Engine:** CockroachDB v26.2 (PostgreSQL wire), self-hosted, 3-node main cluster plus a separate backup cluster
- **Built for:** the Talentra Database Design Hackathon ([docket](talentra_docket.md)), every Section 5 data rule honoured or deviation recorded
- **Delivered:** design docs, DDL generator, secured production stack, monitoring, and an AI-driven data ingestion engine

## Scale

| Measure | Value |
|---|---|
| Tables | **1,306** (768 entity tables + 538 enumeration lookups) |
| Columns | **15,784**, each with type, domain, nullability reason and sensitivity class |
| Foreign keys | **2,951**, all enforced |
| Indexes | **4,600+** (hash-sharded, partial, computed, inverted, vector) |
| Business views | **78**, plus masking and viewer-scoped views |
| Business rules | **457**, each mapped to its enforcement mechanism |
| Design decisions | **43** ADRs |
| Modules | **11** docket modules + 5 extension modules + optional Tier 3 |
| Target data volume | **10M** candidate profiles, **1M** jobs, **100M** applications |
| Laptop seed volume | 1M profiles, 100K jobs, 5M applications |

## Architecture

```mermaid
flowchart TB
    subgraph Client["Clients"]
        DB[DBeaver / psql / apps]
        BR[Browser]
    end

    subgraph EC2["AWS EC2  m6i.2xlarge  (Elastic IP, Ubuntu 24.04)"]
        direction TB
        CADDY["Caddy<br/>auto TLS, basic auth"]
        HAP["HAProxy<br/>write :26257  |  read :26259"]

        subgraph MAIN["Main cluster (1 writer + 2 readers)"]
            N1["crdb node 1<br/>leaseholder"]
            N2["crdb node 2<br/>follower reads"]
            N3["crdb node 3<br/>follower reads"]
        end

        subgraph BKP["Backup cluster"]
            B1["crdb backup node<br/>:26258"]
        end

        SCHED["Backup scheduler<br/>full + incremental,<br/>sync to backup cluster"]

        subgraph MON["Monitoring"]
            PROM[Prometheus]
            LOKI[Loki]
            GRAF[Grafana]
            ALERT[Alertmanager]
        end
    end

    DB -->|TLS verify-full| HAP
    DB -->|TLS verify-full| B1
    BR -->|HTTPS| CADDY
    HAP --> N1
    HAP --> N2
    HAP --> N3
    CADDY --> GRAF
    CADDY -->|DB Console| N1
    CADDY -->|DB Console| B1
    N1 --- N2
    N2 --- N3
    N1 --- N3
    SCHED --> N1
    SCHED --> B1
    PROM --> MAIN
    PROM --> BKP
    PROM --> ALERT
    LOKI --> GRAF
    PROM --> GRAF
```

## Data model

```mermaid
flowchart LR
    MD[Master data<br/>70 tables]
    ADM[Administration<br/>88]
    ORG[Organisation<br/>65]
    PRO[Profile<br/>90]
    JOB[Jobs and<br/>Applications<br/>99]
    GIG[Gig marketplace<br/>65]
    LIC[Licences, devices,<br/>sessions<br/>34]
    SRC[Sourcing and<br/>consultants<br/>42]
    BIL[Subscriptions,<br/>wallet, payments<br/>74]
    NTF[Notifications and<br/>community<br/>93]
    ANA[Analytics star<br/>schema<br/>80]
    EXT[Extensions:<br/>network, matching,<br/>ads, learning,<br/>talent ops<br/>482]

    MD --> ADM & ORG & PRO & JOB & GIG
    ADM --> LIC
    ADM --> PRO
    ORG --> JOB
    ORG --> SRC
    PRO --> JOB
    JOB --> SRC
    GIG --> BIL
    LIC --> BIL
    JOB --> NTF
    PRO --> NTF
    JOB --> ANA
    BIL --> ANA
    PRO --> EXT
    JOB --> EXT
```

## Key features

**Data model**
- Gigs live in their own subsystem, sharing master data, organisations and users with the core job model
- Enumerations are lookup tables with a code, a name per language, an active flag and a sort order
- UI strings are stored by key with per-language translations and a defined fallback
- Soft-delete on business entities, hard-delete on transient ones, stated per entity
- Every business table carries `created_at`, `created_by`, `updated_at`, `updated_by`

**Integrity enforced in the database**
- Device limit enforced at login with row locking, safe under concurrent logins
- No overlapping gig windows or subscription periods, via slot tables (CockroachDB has no exclusion constraints)
- Append-only wallet, payment, audit, download and view ledgers, enforced by privileges and triggers
- Correction entries reference the original entry, never an update
- CHECK constraints for date ranges, salary ranges, E.164 phones, lowercase emails and percentages

**Security**
- Role-based access across the documented actors, enforced through grants, views and viewer-scoped functions
- Recruiters see only their own company's jobs, and a blocked party cannot see the blocker's candidate
- Sensitive data classified in four tiers, with masking views for personal fields
- argon2id password hashing, a tamper-evident audit chain, and session revocation as an audited action
- TLS everywhere with `sslmode=verify-full`, and SCRAM password auth on the cluster

**Performance and scale**
- Hash-sharded indexes on time-ordered, high-volume tables to avoid hotspots
- Follower reads on two reader nodes for read-heavy and analytical traffic
- Row-level TTL for retention and inactivity deletion
- Full-scale projection from measured laptop-scale results, naming the query expected to degrade first

**Operations**
- Separate backup cluster, kept in sync on a schedule while main stays up
- Full monitoring stack: 26 Grafana dashboards, 90 alert rules, host, container, database and TLS metrics
- Local and production Docker Compose files for every stack, driven from one Makefile
- Whole prod environment rebuildable from this repo plus a secrets file

## Ingestion engine

```mermaid
flowchart LR
    U[User chat<br/>HTML UI] --> A[LangGraph<br/>agents]
    A -->|zai.glm-5<br/>on Bedrock| S[Scenarios<br/>1000s]
    S --> C[Config<br/>YAML / JSON]
    C --> V[Validate<br/>and dedupe check]
    V --> L[Loader<br/>FK ordered COPY]
    L --> DB[(CockroachDB)]
    L --> P[(SQLite<br/>progress and<br/>checkpoints)]
    P --> U
```

- **AI-designed scenarios:** agents invent realistic populations, for example fresher hiring in Bangalore or blue-collar staffing in the Gulf
- **Distribution control:** each scenario takes its own distribution (normal, lognormal, zipf, pareto, uniform, poisson, bimodal and more) and a record count
- **Chat with tools:** edit, validate and diff the config in plain language, then accept or reject
- **Resumable:** pause, resume, and restart after a crash with exact row counts and no duplicates
- **Scenario memory:** warns before re-ingesting a scenario already in the database
- **Observable:** per-run progress, rate and history in the browser

## Repository layout

| Path | Contents |
|---|---|
| [`docs/`](docs/README.md) | ER diagrams, data dictionary, ADRs, business rules, RBAC matrix, threat model, security note, compliance table, performance plan |
| [`infra/`](infra/README.md) | Docker Compose stacks (caddy, main-db, backup-db, monitoring), schema generator, AWS scripts, Makefile |
| [`ingestion-engine/`](ingestion-engine/README.md) | LangGraph scenario agents, loader, FastAPI UI |
| [`tests/`](tests/README.md) | pytest suites for conformance, rules and access control |
| [`talentra_docket.md`](talentra_docket.md) | The hackathon brief |

## Quick start

```bash
# local stacks (Docker Desktop)
make -C infra up-local-all

# generate DDL from the data dictionary
make -C infra schema-gen

# production host
bash infra/aws-scripts/provision.sh     # EC2 + Elastic IP
make -C infra deploy-prod               # git clone on host + compose up
```

- Connecting from DBeaver: see [`infra/DBEAVER_CONNECT.md`](infra/DBEAVER_CONNECT.md)
- Rebuilding prod from scratch: see [`infra/PROD_RUNBOOK.md`](infra/PROD_RUNBOOK.md)

## Connect with psql

```bash
# Where to get these:
#   <DB_USER> / <DB_PASSWORD>  ->  .aws-creds/prod-credentials.txt (local, gitignored; generated at deploy time)
#       main, admin       user: talentra_admin      password var: TALENTRA_ADMIN_PASSWORD
#       main, read-only   user: talentra_app_ro     password var: TALENTRA_APP_RO_PASSWORD
#       backup            user: backup_admin        password var: BACKUP_ADMIN_PASSWORD
#   CA certificates       ->  infra/certs/main-ca.crt and infra/certs/backup-ca.crt (public, in this repo)
#   Full role list and DBeaver settings  ->  infra/DBEAVER_CONNECT.md

# Main DB, write port (leaseholder node)
PGPASSWORD='<DB_PASSWORD>' psql "host=db.talentra.thearpanhq.in port=26257 dbname=talentra user=<DB_USER> sslmode=verify-full sslrootcert=infra/certs/main-ca.crt"

# Main DB, read port (follower reads, read-only roles)
PGPASSWORD='<DB_PASSWORD>' psql "host=db.talentra.thearpanhq.in port=26259 dbname=talentra user=<DB_USER> sslmode=verify-full sslrootcert=infra/certs/main-ca.crt"

# Backup DB
PGPASSWORD='<DB_PASSWORD>' psql "host=backup.talentra.thearpanhq.in port=26258 dbname=talentra user=<DB_USER> sslmode=verify-full sslrootcert=infra/certs/backup-ca.crt"
```

- Run from the repo root, or point `sslrootcert` at the CA file
- Passwords are alphanumeric, so no shell escaping is needed
- Smoke check: `-c "select count(*) from information_schema.tables where table_schema='public'"` should return 2077 (1,306 tables + 771 views)

## Documentation index

| Deliverable | File |
|---|---|
| ER diagrams (per module + map) | [`docs/01_er/`](docs/01_er/module_map.md) |
| Architecture decision log | [`docs/02_adr_log.md`](docs/02_adr_log.md) |
| Data dictionary (single source of truth) | [`docs/05_data_dictionary.xlsx`](docs/05_data_dictionary.xlsx) |
| Business rules and enforcement | [`docs/06_business_rules.xlsx`](docs/06_business_rules.xlsx) |
| Schema justification | [`docs/07_schema_justification.md`](docs/07_schema_justification.md) |
| Section 5 rule compliance | [`docs/08_rule_compliance.xlsx`](docs/08_rule_compliance.xlsx) |
| RBAC matrix | [`docs/13_rbac_matrix.xlsx`](docs/13_rbac_matrix.xlsx) |
| Threat model | [`docs/14_threat_model.md`](docs/14_threat_model.md) |
| Security design note | [`docs/15_security_design_note.md`](docs/15_security_design_note.md) |
| Performance and indexing plan | [`docs/11_performance_indexing_plan.md`](docs/11_performance_indexing_plan.md) |
| Backup and restore log | [`docs/20_backup_restore_log.xlsx`](docs/20_backup_restore_log.xlsx) |

## Status

- [x] Design documentation suite (milestones B1 to B59, plus PII notes, threat model and security note)
- [x] Production stack on EC2: 3-node main cluster, backup cluster, HAProxy, Caddy, monitoring
- [x] Ingestion engine built and tested against a stub schema
- [x] Full schema applied from empty (1,306 tables, 765 views, 4,614 indexes, 2,951 FKs), repeatable, backup cluster synced on a schedule
- [ ] Test suites, RBAC verification and independent doc-vs-database check
- [ ] Engine aligned to the real schema and run against production
- [ ] Seeding, query performance and backup/restore timing on EC2 (milestones B60 to B74, B79, B80, B82)

## License

Apache 2.0, see [`LICENSE`](LICENSE).
