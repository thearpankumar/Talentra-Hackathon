# infra/schema: Talentra database schema (generated from the data dictionary)

Source of truth: `docs/05_data_dictionary.xlsx` (+ `docs/06_business_rules.xlsx`, `docs/13_rbac_matrix.xlsx`). Nothing here is hand-edited except `parts/`.

| Path | Purpose |
|---|---|
| `generate_ddl.py`, `gen_security.py` | read the workbooks, write `ddl/*.sql` (`make -C infra schema-gen`) |
| `ddl/` | generated, ordered, idempotent SQL: `00_roles`, `05_functions_common`, `10_lookups`, `11_lookup_seeds`, `20_tables_<nn>_<module>` (FK-safe order, indexes + row TTL inline), `30_fks_deferred`, `40_append_only`, `45_db_privileges`, `50_views_masked` (masked, ETL and default `vw_<t>_visible` views), `52_views_business`, `70_grants`, `80_zone_configs`, `WARNINGS.txt` |
| `parts/` | hand-authored: business-rule procedures, viewer-scoped functions, view overrides, post-grants (see `parts/README.md`) |
| `apply.py` / `apply.sh` | apply `ddl/` + `parts/` merged by file name; idempotent; reconnects on dropped connections; `--soft` lists failing statements; `--only 20_,30_` filters |
| `smoke/smoke.py` | Phase-1 smoke test of the risky CockroachDB mechanisms (results: `docs/crdb_smoke_results.md`) |

## Commands (from `infra/`, Git Bash)
```
make up-local STACK=main-db                 # local 3-node cluster, write 26267 / read 26269 / backup SQL 26268
make schema-gen                             # regenerate ddl/ after a dictionary change (docs/tools build first)
make schema-local DB=talentra               # create db + schema_owner, apply everything as role schema_owner (idempotent)
make test-schema DB=talentra                # pytest suite in ../tests against that database
SCHEMA_DSN='postgresql://...' make schema-prod   # prod: never run casually; needs the write endpoint DSN of an admin user
```
A first apply into an empty database takes about 25 minutes on a laptop (1306 tables, ~2300 indexes, ~3000 FKs, 770 views, schema changes cost 0.3 to 1 s each on a 3-node cluster; tables are created in parallel dependency waves, other files are batched 40 statements per implicit transaction). A re-apply on an existing schema takes about 2 minutes.

## Ownership and roles
All objects are owned by the NOLOGIN role `schema_owner` (apply.py runs `SET ROLE schema_owner`). Group roles and persona roles come from the RBAC matrix (`00_roles.sql`); the infra placeholder roles keep working: `talentra_app_rw` joins `app_service`, `talentra_analyst` joins `analytics_reader`, `talentra_audit_reader` joins `audit_reader`, `talentra_app_ro` joins `app_read`; `talentra_admin` stays CockroachDB `admin`. Login users have `PASSWORD NULL` (certificate / operator-set password).
