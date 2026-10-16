# CockroachDB capabilities for the Talentra DB design (researched 2026-10-15)

Method: official docs fetched from docs.cockroachlabs.com (v26.2 paths unless noted). Where the docs page does not state something, it says **unverified**. No guesses. Page fetches were summarised by a helper model, so exact SQL should still be smoke-tested on the real image.

## 0. Version assumed

Source: https://docs.cockroachlabs.com/docs/releases/release-support-policy (fetched 2026-10).

| Major | Type | Initial release | Notes |
|---|---|---|---|
| v26.3 | Innovation | 2026-08-19 | Newest. Innovation = shorter support (maintenance ends 2027-02-19), no Assistance Support. Not recommended as a base. Only v26.3.0 and v26.3.1 seen in release notes. |
| v26.2 | Regular GA (26.2.0-26.2.6), **LTS from v26.2.7+** (2026-09-23) | 2026-04-27 | **Assumed target.** LTS maintenance until 2027-09-23. |
| v25.4 | LTS from 25.4.10 | 2025-11-03 | |
| v25.2 | LTS from 25.2.10 | 2025-05-09 | |
| v24.3 / v24.1 | LTS | | v24.3 is the first release under the CockroachDB Software License. |

- Assumed: **CockroachDB v26.2 (LTS line, patch >= v26.2.7)**; image `cockroachdb/cockroach:v26.2.<latest patch>`. The exact latest 26.2 patch tag is **unverified**; pin it after checking Docker Hub. The fallback `v26.2.0` tag exists (release page) but is pre-LTS.
- v26.3.0 image `cockroachdb/cockroach:v26.3.0` exists; multi-platform (Intel + ARM both GA). An experimental `-noble` Ubuntu tag is also listed.
- Server reports PostgreSQL 18 compatibility (`server_version`) in v26.3 (postgresql-compatibility page); wire protocol pgwire 3.0.
- Several differences between v26.2 and v26.3 are flagged below as "v26.3 only".

## 1. Capability table

Legend: GA = documented without a preview label; "unverified" = docs fetched did not state it. Licence column: see section 3. Under the post-2024 model every multi-node self-hosted cluster needs some licence (Enterprise / Free / Trial); the pages fetched do not gate individual features except where noted.

| # | Feature | Status | Min version | Licence | Syntax / caveats |
|---|---|---|---|---|---|
| 1a | `gen_random_uuid()` default PK | GA | old | any valid | `id UUID NOT NULL DEFAULT gen_random_uuid()`. Returns v4. Uniqueness checks are SKIPPED by default for it (`sql.optimizer.uniqueness_checks_for_gen_random_uuid.enabled`, default off) - matters for implicitly partitioned/RBR tables. Docs "strongly recommend UUIDv4". |
| 1b | UUID v7 (`uuid_v7()`/`uuidv7()`) | **unverified / not found** | - | - | Not on the UUID page, not in functions page (chunks scanned: 100k-700k chars plus first 100k, no hit for `uuid_v7`/`uuidv7`), not in v26.2/v26.3 release notes. Treat as unavailable. Docs: other UUID types "largely untested... require performance testing to avoid hotspots". Use app-generated v7 only if inserted via a hash-sharded index. |
| 1c | `unique_rowid()` / `unordered_unique_rowid()` | GA | old | any | 64-bit: 48-bit time (10us), 15-bit node id. No coordination; not strictly ordered; gaps. Ignores `default_int_size`. |
| 1d | SERIAL | GA, "only for PG compatibility" | old | any | `serial_normalization` default `rowid` => `DEFAULT unique_rowid()` and always INT8 (even `SERIAL2/4`). Other modes: `unordered_rowid`, `virtual_sequence`, `sql_sequence`, `sql_sequence_cached` (cache `sql.defaults.serial_sequences_cache_size` default 256). Write `INT8 DEFAULT unique_rowid()` explicitly. |
| 2 | Hash-sharded indexes | GA-ish (page states no preview label; GA label itself unverified) | prefix-column variant GA in v26.2 | any | `CREATE INDEX ON events (ts) USING HASH WITH (bucket_count = 16);` also in CREATE TABLE and `ALTER PRIMARY KEY`. Default buckets: cluster setting `sql.defaults.default_hash_sharded_index_bucket_count` (docs examples use 16; the page recommends `ALTER ROLE ALL SET` over `sql.defaults.*`). `shard_columns` hashes only a prefix (v26.2). Shard column is VIRTUAL (v22.1+; no backfill). Range scans must fan out over all buckets ("small performance hit"). Cannot be combined with explicit `PARTITION BY` key columns; implicit partitioning (REGIONAL BY ROW) OK. More buckets than nodes = diminishing returns. |
| 3a | Partial index / UNIQUE partial | GA | old | any | `CREATE UNIQUE INDEX ON users (name) WHERE city='ny';` Predicate must be boolean, same table, immutable functions only. Cannot drop a column used in a predicate. `INSERT ... ON CONFLICT DO UPDATE` does not support multiple arbiters. Partial GIN also supported. |
| 3b | Expression indexes | GA | old | any | `CREATE INDEX ON t (lower(email));` `UNIQUE INDEX ((a+b))` allowed but cannot be `ON CONFLICT` arbiter (expressions not valid conflict targets). Immutable only; cannot reference computed columns. |
| 3c | STORING | GA | old | any | `INDEX (a,b) STORING (c)`, synonym `COVERING`. `INCLUDE`: **unverified** on the page (assume STORING only). Inverted indexes cannot have STORING. |
| 3d | GIN/inverted on JSONB | GA | old | any | `CREATE INDEX ON t USING GIN (col)` or `CREATE INVERTED INDEX`. Accelerated: `@>`, `<@`, `col->'k' = 'v'`. The page does **not** list `?`, `?|`, `?&` as accelerated (unverified). Multi-column inverted OK (JSONB last). Partial GIN OK. |
| 3e | Trigram GIN | GA | old | any | `CREATE INDEX ON t USING GIN (col gin_trgm_ops);` accelerates `=`, `LIKE`, `ILIKE`, `%` (`pg_trgm.similarity_threshold`, default 0.3). Needs terms >= 3 chars. No POSIX regex acceleration; no `word_similarity`, `<->`. No collated strings. Page does not mention a `CREATE EXTENSION pg_trgm` requirement (unverified). |
| 3f | Full-text search | GA (listed "supported" in feature table) | old | any | `tsvector`/`tsquery`, `to_tsvector`, `to_tsquery`, `plainto_tsquery`, `phraseto_tsquery`, `ts_rank`, `@@`; `CREATE INDEX ... USING GIN (col)`. Missing: `websearch_to_tsquery`, `ts_rank_cd`, `setweight`, `tsvector \|\| tsvector`. Expression indexes need explicit config e.g. `'english'`. |
| 3g | VECTOR + vector index (C-SPANN) | Feature table: "partial". GA vs Preview label **unverified** | exists in 25.x (v25.4 patch notes mention `CREATE VECTOR INDEX`) | any | `CREATE VECTOR INDEX ON t (emb)`; requires cluster setting `feature.vector_index.enabled = true`. Ops: `vector_l2_ops`, cosine, inner product (L1/hamming/jaccard not implemented). Creating on non-empty table needs `sql_safe_updates=false` and blocks writes during backfill. `IMPORT INTO` unsupported on tables with vector indexes. "C-SPANN" name not on the page (the page describes hierarchical k-means). |
| 4 | Computed columns | GA | old | any | `c T AS (expr) STORED\|VIRTUAL` (also `GENERATED ALWAYS AS (...) STORED`). Immutable expressions only. Cannot be changed in place (drop + add). VIRTUAL cannot be in CHECK, FK or families, and cannot be `STORING`; can be index key. STORED can be FK columns with ON UPDATE NO ACTION/RESTRICT and ON DELETE NO ACTION/RESTRICT/CASCADE only. Cannot combine with DEFAULT/ON UPDATE. |
| 5 | CHECK incl. regex `~`, jsonb `?` | GA (basic) | old | any | CHECK page fetched does not mention `~`, `?`, subqueries or NOT VALID (unverified). Standard: TRUE or NULL passes; multi-column and `ADD CONSTRAINT` supported. Subqueries in CHECK are not allowed by PG either; **test** `~` and `?` on the target image before relying on them. |
| 6 | Foreign keys | GA | old | any | Composite FK OK. Actions: NO ACTION (default), RESTRICT (=NO ACTION currently), CASCADE, SET NULL, SET DEFAULT. `MATCH SIMPLE` (default) and `MATCH FULL`; **MATCH PARTIAL unsupported**. Referenced columns must match a UNIQUE or PRIMARY KEY constraint (order may differ). FK to a non-PK UNIQUE constraint: yes (per that sentence). FK to a *partial* unique index: page silent (unverified; PG also disallows). DEFERRABLE and NOT VALID: **not covered by page (unverified)**. Each write does lookups in the other table; index the referencing columns (not auto-created). Cross-database FKs need `sql.cross_db_fks.enabled`. Partitioned->unpartitioned FK causes cross-node latency. Under RBR with auto-rehoming an FK cannot reference `crdb_region`. `IMPORT INTO` leaves FKs invalid (`VALIDATE CONSTRAINT` after). |
| 7 | Exclusion constraints | **Unsupported / not available**: not listed anywhere as supported; page for partial indexes says "unique or exclusion constraint" in ON CONFLICT text (generic). Unverified whether CREATE rejects. Treat as No. | - | - | Use unique indexes, partial unique indexes, or app-level checks. (The PG-compat page fetched did not list exclusion constraints at all.) |
| 8a | Triggers | **GA in v26.2** ("SQL triggers are now generally available", v26.2 highlights); Enterprise-labelled in 24.3 announcement search snippet (unverified); preview in earlier lines (unverified) | v24.3 first appeared; GA v26.2 | any valid licence (no separate gate stated) | `CREATE TRIGGER ... BEFORE\|AFTER INSERT\|UPDATE\|DELETE ... FOR EACH ROW [WHEN (..)] EXECUTE FUNCTION f()`. PL/pgSQL function, no args, returns TRIGGER. `CREATE OR REPLACE TRIGGER` in 26.2; `ALTER TRIGGER RENAME` in 26.3. Unsupported: statement-level, INSTEAD OF, REFERENCING, `UPDATE OF col`, TRUNCATE. TTL deletes fire triggers (run as table owner, v26.3). v26.3 fixed bugs: trigger on REGIONAL BY TABLE/GLOBAL table, cross-table dependency tracking. Needs `TRIGGER` priv on table + `EXECUTE` on function. |
| 8b | UDFs | "Partial" in feature table | 22.2+ (version unverified) | any | SQL and PL/pgSQL; volatility default VOLATILE; STABLE/IMMUTABLE cannot mutate; no DDL inside; no temp tables; `ANYENUM` params unsupported; hint injection ignored. **SECURITY DEFINER / INVOKER: unverified on fetched pages** (RLS page mentions SECURITY DEFINER functions exist, "use with extreme caution"). |
| 8c | Stored procedures (PL/pgSQL) | "Partial"; DDL/DCL in procedures = Preview in v26.3 | 23.2+ (unverified) | any | `CREATE PROCEDURE ... LANGUAGE PLpgSQL`, `CALL`; IN/OUT/INOUT; named args and nested COMMIT/ROLLBACK unsupported; no DDL (preview in 26.3). |
| 9 | Row-level security | Documented; GA/preview label **unverified**; no version stated. Bug-fix mentions exist in v25.2.x so it is >= v25.2 | >= 25.2 | no stated gate | `ALTER TABLE t ENABLE ROW LEVEL SECURITY; [FORCE]`; `CREATE POLICY p ON t AS PERMISSIVE\|RESTRICTIVE FOR ALL\|SELECT.. TO role USING (..) WITH CHECK (..)`. Limits: FKs, PK, UNIQUE checks and TRUNCATE bypass RLS; CDC messages not filtered (CDC queries on RLS tables fail); backup/restore and LDR/PCR ignore RLS; policy expressions cannot contain subexpressions; `ON CONFLICT DO NOTHING` skips check on VALUES for conflicting row; admin and table owner exempt unless FORCE; `BYPASSRLS` role option; `row_security` session var. Views: owner's RLS unless `security_invoker` (and the owner-vs-invoker behaviour changed in 26.2 and 26.3 - fixed bugs). |
| 10 | Custom session vars (`SET app.user_id`, `current_setting`) | `current_setting(name[, missing_ok])` exists (STABLE); `set_config(name, value, is_local)` exists. Whether arbitrary `SET app.x = ...` custom (dotted) variables are accepted: **unverified** - set-vars page lists only built-ins. `SET LOCAL` supported (rolled back with txn/savepoint). RLS example uses `current_setting('application_name', true)`. | | any | Verify `SET app.user_id='..'; SELECT current_setting('app.user_id', true)` on the image before relying. Fallback: `application_name`, or `SET ROLE`. |
| 11 | Roles / privileges / auth | GA | old | any | `CREATE ROLE` (NOLOGIN default), `CREATE USER`, `GRANT role TO user [WITH ADMIN OPTION]`, role hierarchy with inheritance, `admin` and `public` built-ins, owner = creator; `ALTER DEFAULT PRIVILEGES`, `GRANT ... ON ALL TABLES IN SCHEMA` supported. **Column-level privileges are NOT supported** (PG-compat page). `REVOKE UPDATE, DELETE`: REVOKE page not fetched; standard table-level REVOKE assumed (unverified), but note: owner and `admin` always retain privileges, and `root` bypasses. `ALTER ROLE r WITH PASSWORD '..' [VALID UNTIL ..]` (secure clusters only); `PASSWORD NULL` forces cert auth. System privileges (`GRANT CREATEROLE ON SYSTEM`) preferred over legacy role options. SCRAM-SHA-256 / `server.user_login.password_encryption`: **not stated on fetched pages (unverified)**. Cert auth: `cockroach cert create-client <user>`; HBA via `server.host_based_authentication.configuration`. |
| 12 | Row-level TTL | GA (no preview label on page; version unverified) | >= 22.x | any | `WITH (ttl_expiration_expression = 'expires_at', ttl_job_cron = '@daily', ttl_select_batch_size = 500, ttl_delete_batch_size = 100)`. `ttl_expire_after` on an existing table rewrites the table (page recommends expression form). Default select batch 500, delete batch 100 (cluster settings `sql.ttl.default_*`). Job cron should be >= `gc.ttlseconds` (4h default). Queries do not hide expired rows - filter yourself. Inbound FKs apply: ON DELETE RESTRICT with children makes the job fail; CASCADE not described. Secondary indexes slow the job, add contention. Fires triggers (26.3). |
| 13 | CHANGEFEED | Enterprise sinks GA; Pulsar = Preview | old | **Core vs Enterprise split not stated on the fetched page (unverified)**. Sinkless (omit INTO) streams to the SQL client. | `SET CLUSTER SETTING kv.rangefeed.enabled = true;` `CREATE CHANGEFEED FOR TABLE t INTO 's3://...' WITH updated, resolved, diff, initial_scan='yes', format=json`. Sinks: Kafka, Amazon MSK, Confluent, Event Hubs, Pub/Sub, cloud storage (S3/GCS/Azure) + HTTP, webhook, Pulsar(preview). `IMPORT INTO` fails changefeeds. Not RLS-filtered. `schema_locked` improves changefeed perf (default for new tables since v26.1). |
| 14 | Multi-region | GA; super regions GA in v26.3 | old | no gate stated on the page (licence blanket applies) | Nodes started with `--locality=region=us-east,zone=a`. `ALTER DATABASE d PRIMARY REGION "us-east"; ALTER DATABASE d ADD REGION "us-west"; ALTER TABLE t SET LOCALITY REGIONAL BY ROW\|REGIONAL BY TABLE IN ..\|GLOBAL; ALTER DATABASE d SURVIVE ZONE\|REGION FAILURE;` `SURVIVE REGION FAILURE` needs >= 3 database regions (else SQLSTATE 42602). Regions come only from node startup flags, so a single Docker host CAN exercise it by giving the 3 containers different `region=` localities (the docs never say it must be separate hardware; the "single host is fine" claim itself is **unverified**/inferred). Global tables: lower `--max-offset` (250ms recommended for new clusters). `sql.defaults`-style RBR adds hidden `crdb_region` column. |
| 15a | Zone configs | GA | old | index-level zones need Enterprise ("with an Enterprise license..."); others not stated | `ALTER TABLE t CONFIGURE ZONE USING num_replicas=3, constraints='[+region=us]', lease_preferences='[[+region=us]]', range_max_bytes=.., gc.ttlseconds=..;` Defaults: 3 replicas, range max 512 MiB, gc.ttlseconds 14400 (page itself is inconsistent: examples show 90000). Docs advise using multi-region SQL instead of manual zone configs. |
| 15b | Partitioning | GA | old | Docs examples say set an Enterprise/trial licence first (range, sub, geo partitioning). | `PARTITION BY LIST (col) (PARTITION a VALUES IN (..), PARTITION DEFAULT VALUES IN (default))` / `PARTITION BY RANGE ... FROM .. TO ..`. Partition columns must be leading PK columns. Cannot drop a single partition; non-partitioned secondary indexes NOTICE. Placeholders not allowed. |
| 15c | Follower reads | GA | old | Page mentions no licence | `AS OF SYSTEM TIME follower_read_timestamp()` (about 4.2s stale), exact staleness `AS OF SYSTEM TIME '-10s'`, bounded `with_max_staleness('10s')`/`with_min_timestamp(..)` (single-statement, single-row, no index join), `SET default_transaction_use_follower_reads = on;` (read-only txns only). |
| 16a | Isolation | SERIALIZABLE default; READ COMMITTED GA-ish | RC introduced ~23.2 (**unverified from page**) | any | `sql.txn.read_committed_isolation.enabled` is **true by default**; if false RC txns are upgraded to SERIALIZABLE. `SET default_transaction_isolation = 'read committed'` or `BEGIN ISOLATION LEVEL READ COMMITTED`. RC allows non-repeatable/phantom reads, lost updates, write skew; practically no `40001` for serialization but deadlocks/constraint errors may still give 40001. Mixed-isolation workloads must enable FK-check locking for SERIALIZABLE (`enable_implicit_fk_locking_for_serializable`). Schema change in explicit RC txn aborts unless `autocommit_before_ddl=on`. |
| 16b | `SELECT FOR UPDATE` | GA | old | any | Exclusive lock; `SKIP LOCKED`, `NOWAIT` supported; `FOR SHARE` (aliases FOR KEY SHARE / FOR NO KEY UPDATE). Under SERIALIZABLE locks are best-effort unreplicated (lost on lease transfer/split/merge, can lead to 40001) unless `enable_durable_locking_for_serializable = true`. `SKIP LOCKED` unsupported under SERIALIZABLE on multi-column-family tables. FOR UPDATE does not eliminate retries. `UPDATE`/`UPSERT` use implicit FOR UPDATE. |
| 16c | Retries / 40001 | GA | | | Serializable txns need client retry loop on SQLSTATE 40001; implicit (single-statement, auto-commit) txns retry server-side automatically unless results already streamed. |
| 16d | Advisory locks | Session-scoped `pg_advisory_lock`: **unsupported** (errors; `pg_try_advisory_lock` no-op). Transaction-scoped `pg_advisory_xact_lock`: supported, **Preview in v26.3** | | any | Do not use session advisory locks; use `SELECT ... FOR UPDATE` on a lock row. v26.3 fixed a bug where xact locks "could be silently lost". |
| 17 | Sequences | GA | old | any | `CREATE SEQUENCE s CACHE 256` (`PER NODE CACHE`, `PER SESSION CACHE`). `nextval` is a replicated write: contention/hotspot, never rolled back (gaps). Avoid indexing sequential keys unless hash-sharded. |
| 18a | ENUM | GA | old | any | `CREATE TYPE s AS ENUM (..)`; database-scoped. Casting subqueries to UDTs in views unsupported. |
| 18b | Domains | v26.2: **`CREATE DOMAIN` unsupported** (v26.2 PG-compat page). v26.3: "`DOMAIN` data types" GA. | v26.3 | any | Do not use in the v26.2 DDL. |
| 18c | Arrays, JSONB | GA | old | any | Arrays and JSONB with GIN indexes; JSONB path ops partial (`jsonb_ops`). |
| 19 | BACKUP / RESTORE | GA | old | **Docs: `RESTORE` no longer requires Enterprise. BACKUP licence gating: page says nothing (unverified).** | `BACKUP INTO 'external://x' AS OF SYSTEM TIME '-10s' WITH revision_history;` incremental `BACKUP INTO LATEST IN '...'`. Old `BACKUP ... TO` removed in v24.3. Tested storage: S3, GCS, Azure; `nodelocal`/`userfile` mentioned but nodelocal as a backup target not described (unverified). `CREATE SCHEDULE FOR BACKUP ... RECURRING '@daily' FULL BACKUP ALWAYS WITH SCHEDULE OPTIONS first_run='now'` (UTC cron; default fulls: hourly->daily, daily->weekly). PITR: `RESTORE ... AS OF SYSTEM TIME` between `revision_start_time` and `backup_time` (revision_history default true for BACKUP/schedules). `WITH EXPERIMENTAL COPY` faster restore: Preview in v26.2, needs `backup.compaction.threshold=4`, no encrypted backups. Full cluster backup carries the licence key. |
| 20 | Licensing | see section 3 | | | |
| 21 | Views / materialized views | GA | old | any | `CREATE MATERIALIZED VIEW`; `REFRESH MATERIALIZED VIEW [CONCURRENTLY] v [AS OF SYSTEM TIME follower_read_timestamp()]` - `CONCURRENTLY` is accepted but a no-op (all refreshes already non-blocking). Cannot refresh inside an explicit txn. No stats on matviews. Manual refresh only (needs `MAINTAIN` privilege). `CREATE VIEW ... WITH (security_invoker)`. Cross-db views need `sql.cross_db_views.enabled`. Parameterised views: use SQL UDFs (inlinable if non-volatile, single statement). |
| 22 | Keywords / identifiers | | | | Four categories: reserved, type-function-name, column-name, unreserved (full lists only in the SQL grammar page; **not enumerated here - unverified**). Quote with `"` to bypass; quoted identifiers are case-sensitive. Identifier max 128 bytes (`max_identifier_length`, not enforced yet); user/role names 63 bytes. Avoid: `user`, `order`, `group`, `table`, `check`, `end`, `from`, `limit`, `offset`, `primary`, `references`, `unique`, `value` (standard SQL reserved - **verify with the grammar**). |
| 23 | Encryption | At rest GA with `--enterprise-encryption=path=..,key=..,old-key=plain` (page states no explicit licence rule; name implies enterprise - unverified). TLS in transit: secure mode via `--certs-dir`; `cockroach cert create-ca / create-node / create-client`. | old | | `--insecure` disables TLS and passwords (testing only). Password auth needs secure mode. pgcrypto-compatible functions in the function reference: `digest(data, type)` (md5, sha1, sha224/256/384/512), `hmac`, `crypt`, `gen_salt(type[, iter])`, plus `sha256/sha512/md5` aggregates/scalars. `encrypt()`/`decrypt()` listed but "require an enterprise license on a CCL distribution" (applicability to 26.x BSL/Software-licence builds unverified). |
| 24 | SQL features | | | | Window functions, CTEs, `INSERT ... ON CONFLICT`, `UPSERT`, `RETURNING`, `AS OF SYSTEM TIME`, generated columns: GA. **`MERGE`: no docs page (404 at `/v26.2/merge`), not in release notes, no feature-table row - treat as unsupported/unverified.** `GENERATED ... AS IDENTITY`: serial page silent (unverified). Generated STORED columns supported (section 4). |
| 25 | Limits | | | | Recommended: tested with 10,000 tables; `sql.schema.approx_max_object_count` default 20000 (0 disables). Max columns/row size/index size: **not stated on fetched pages**; keep indexed values < 1 MiB (docs warn of write amplification), declare lengths e.g. `STRING(100)`. Default `range_max_bytes` 512 MiB. Schema changes online as background jobs; most are NOT atomic inside multi-statement txns (error `XXA00` possible); `CREATE TABLE`/`CREATE INDEX` keep atomicity; DROP COLUMN alone; at most one schema change at a time in prod; ADD/DROP COLUMN and PK change can need up to 3x range size free space; no concurrent PK change + schema change. `autocommit_before_ddl` makes DDL commit the open txn. `sql.defaults.*` cluster settings are redundant with `ALTER ROLE ALL SET <session var>` (preferred). |
| 26 | EXPLAIN | GA | old | any | `EXPLAIN`, `EXPLAIN (VERBOSE)`, `EXPLAIN ANALYZE` (executes statement - not a dry run), `EXPLAIN ANALYZE (DISTSQL)` (URL to plan diagram), `EXPLAIN ANALYZE (DEBUG)` (diagnostics bundle zip; `cockroach statement-diag download`), `(REDACT)`. Spans show `FULL SCAN` vs `LIMITED SCAN`. `SHOW RANGES FROM TABLE t WITH DETAILS, KEYS, INDEXES`, `SHOW CREATE TABLE`. v26.3 EXPLAIN suggests vector indexes. |
| 27 | Bulk load | | | | `COPY t FROM STDIN [WITH CSV [HEADER], DELIMITER, NULL, BINARY]`: **admin role only**; unsupported `WITH FREEZE`, `WITH QUOTE`, `FROM ... WHERE`. `IMPORT INTO t (cols) CSV DATA ('s3://...')`: table offline for the duration, insert-only, no PK/unique conflicts, restart from scratch if paused, FKs invalidated, fails changefeeds, unsupported with vector indexes; no throughput numbers on the page (throttle with `kv.bulk_io_write.max_rate`). |
| 28 | `current_user` / `SET ROLE` | `SET ROLE` page not retrievable (404 for `/v26.2/set-role`); `SET ROLE` itself unverified from docs. Authorization page does not discuss `SET ROLE`. | | | Prefer: connect as a login role per tenant/user/service (cert or password) so `CURRENT_USER` works in RLS policies/views (policy example uses `username = CURRENT_USER`); or `SET LOCAL` custom var. Pool-level per-request context via `SET LOCAL` inside the txn is the PG idiom - **test that dotted custom variables are accepted**. |
| 29 | Node sizing | | | | `--cache` default 256 MiB (dev); production "25% or higher"; `--max-sql-memory` default 25%; sum of cache + sql-memory + tsdb-memory <= 75% of the process memory. Multiple nodes per host: distinct `--listen-addr`/`--http-port`/`--store`, same `--join`; the docs give no per-node split guidance (inference below). `--max-offset` default 500ms. |

## 2. Notes and detail

### 2.1 UUID/PK strategy
- Docs recommend UUIDv4 or `unique_rowid()` for write-spreading PKs. `gen_random_uuid()` skips uniqueness checks by default.
- Sequential keys (timestamp, sequence, v7 UUID) hotspot a single range; use `USING HASH` indexes (and for PKs `PRIMARY KEY (id) USING HASH`).
- Hash-sharded + range scans: time-range queries on the hashed index fan out across buckets (e.g. 16).

### 2.2 FK validation and design
- Parent lookups on insert and child lookups (for RESTRICT/CASCADE) on parent delete/update. Without an index on the child FK columns, parent delete scans the child. Create explicit indexes (CRDB does not auto-index the referencing side, per the page's recommendation).
- Under RC, FK locks are fully replicated and FK checks are not parallelised; extra latency.

### 2.3 Triggers vs RLS
- Triggers GA in v26.2, but only row-level FOR EACH ROW; no statement/INSTEAD OF; PL/pgSQL only.
- RLS: policies bypassed by FK, UNIQUE, PK, TRUNCATE, backup/restore, CDC, LDR/PCR. Not suitable as sole authorisation. GA label unverified.

### 2.4 TTL
- Row-level TTL is implemented as a background job issuing normal DELETEs: FK behaviour as standard, triggers fire, secondary indexes slow it.

### 2.5 Backup
- `external://` URIs reference pre-created external connections (`CREATE EXTERNAL CONNECTION`); examples use S3. For Docker without cloud, `nodelocal` behaviour for backups is not described on the fetched pages (unverified); MinIO-style S3 is "untested" per docs.

### 2.6 Node sizing, 3 nodes on one host, ~100M-row tables (judgement, not doc-stated)
- Page-stated: cache + SQL memory + tsdb <= 75% of the node's memory; production wants cache >= 25%.
- Inference: for 3 containers on one host, give each container a memory limit (e.g. 8 GiB) and run `--cache=.25 --max-sql-memory=.25` of that limit (Docker limits are not auto-detected reliably - pass explicit fractions/values). Minimum viable: 2 vCPU and 8 GiB per node, SSD/NVMe store per node; 4 vCPU/16 GiB comfortable for 100M rows with several secondary indexes. These figures are **not** from the docs.
- Disk: schema changes may need 3x range size free.

## 3. Licensing (item 20)

Source: https://docs.cockroachlabs.com/docs/stable/licensing-faqs (fetched). Also release page: binaries >= 24.3.0 are under the CockroachDB Software License; "Core" retired in late 2024.

| Licence | Terms |
|---|---|
| Enterprise | Paid, all features, annual/negotiated, via Sales. Never throttles on expiry. |
| **Enterprise Free** | Same features, no charge. Combined revenue of company + parent + affiliates < US$10M; governments excluded; only on own hardware or own public cloud account; annual renewal (new licence); **throttled after 7 days without telemetry**; throttles 30 days after expiry. Academic non-commercial research eligible. |
| Trial | 30-day self-service; telemetry required; max one new Trial licence per day; cannot be re-applied to the same cluster; throttles 7 days after expiry. |
| Evaluation | Enterprise features, obtained through Sales. |

- Throttle = **max 5 concurrent open SQL transactions**. Triggers: telemetry unmet (7-day grace on first add or interruption), expiry beyond grace, no valid key within 7 days of cluster initialisation, cluster upgraded from Core (30-day grace).
- **No licence needed for `cockroach start-single-node` or `cockroach demo`** (dev/test only; never throttled).
- Obtain Free/Trial: CockroachDB Cloud Console, Organization Admin, Organization > Enterprise Licenses > Create License.
- Apply (as root, in `cockroach sql`): `SET CLUSTER SETTING enterprise.license = '<key>';` Verify: `SHOW CLUSTER SETTING enterprise.license;` Also `SET CLUSTER SETTING cluster.organization = '<org name>';` is the historic companion setting - **not on the fetched page (unverified for v26.2)**. `enterprise.license` is marked sensitive (v24.3.36 notes).
- Monitor: Prometheus `seconds_until_enterprise_license_expiry`; DB Console and logs warn.
- Which features need which licence: **the licensing page says Enterprise provides "usage of all CockroachDB features" and does not list per-feature gates.** Per-feature mentions elsewhere: partitioning examples (set Enterprise/trial first), index-level zone configs (Enterprise), `encrypt()/decrypt()` (enterprise on CCL distributions). RESTORE explicitly no longer needs a licence. Treat everything else as "licence blanket applies; no per-feature gate verified".
- **Hackathon Docker implication:** a 3-node cluster is not covered by the single-node exemption. Without a licence key the cluster is throttled to 5 open transactions after 7 days. So: (a) get a Free or Trial key and apply it right after `cockroach init`, (b) ensure outbound telemetry connectivity from the containers (Trial/Free require it), (c) a 30-day Trial may expire mid-project, prefer Enterprise Free if eligible, (d) CI/dev can use `start-single-node` without a licence, but do not exercise multi-region/survival there.

## 4. Recommended design position

Conservative rule: correctness must not depend on triggers or RLS unless GA in the supported version AND licence-free; DDL must run on `cockroachdb/cockroach:v26.2.x`. (No feature is verified licence-free in the post-2024 model - every multi-node cluster requires some licence - so triggers/RLS can never be correctness-critical under this rule.)

| Feature | May schema rely on it? |
|---|---|
| `gen_random_uuid()` PKs | **Yes** |
| `uuid_v7()` | **No** (not found) |
| `unique_rowid()` / SERIAL (as INT8 default) | Yes (explicit `DEFAULT unique_rowid()`); sequences only optional |
| Hash-sharded indexes | Yes (for monotonic columns); optional otherwise |
| Partial indexes / partial UNIQUE | **Yes**, but not as FK target and not as a multi-arbiter ON CONFLICT |
| Expression indexes | Yes (not as ON CONFLICT target) |
| STORING | Yes; `INCLUDE` No |
| GIN JSONB (`@>`) | Yes; `?` acceleration unverified -> optional |
| Trigram GIN | Optional hardening |
| Full-text search | Optional |
| Vector index | **No** (GA label unverified; feature flag) - optional |
| Computed STORED | Yes. VIRTUAL: Yes for query convenience, not for CHECK/FK |
| CHECK (simple) | Yes. CHECK with `~` / `?`: test first, then Yes |
| Composite FK, ON DELETE/UPDATE, MATCH SIMPLE/FULL | Yes. FK to non-PK UNIQUE constraint: Yes. FK to partial index: No. DEFERRABLE: No |
| Exclusion constraints | **No** |
| Triggers | Optional hardening only (GA v26.2, licence blanket) |
| UDFs / procedures | Optional (SECURITY DEFINER unverified) |
| RLS | **Optional hardening only**; never the sole enforcement |
| Custom session vars (`app.user_id`) | Optional until tested; write paths must pass actor explicitly |
| Roles, GRANT/REVOKE table-level, default privileges, password auth | Yes. **Column-level privileges: No** |
| Row-level TTL | Yes for purge jobs; mind FKs/triggers; optional for correctness |
| CHANGEFEED | Optional (needs `kv.rangefeed.enabled`, licence) |
| Multi-region / zone configs / partitioning / follower reads | Optional (exercise only if licence applied; partitioning docs want Enterprise) |
| SERIALIZABLE default | Yes (retry loop required). READ COMMITTED: optional |
| SELECT FOR UPDATE / SKIP LOCKED | Yes for queues; best-effort lock durability under SERIALIZABLE |
| Session advisory locks | **No**. xact advisory locks: No (Preview in 26.3) |
| ENUM | Yes. CREATE DOMAIN: **No** in v26.2 (GA only in v26.3) |
| Arrays, JSONB | Yes |
| BACKUP/RESTORE, schedules, PITR | Yes (operational); licence for BACKUP unverified |
| Views | Yes. Materialized: Yes (manual refresh, `CONCURRENTLY` no-op, not inside txn) |
| Encryption at rest / TLS | Optional / Yes (secure mode strongly advised) |
| pgcrypto `digest/hmac/crypt/gen_salt` | Yes; `encrypt/decrypt` No (enterprise/CCL) |
| MERGE | **No** (unverified/not documented) |
| ON CONFLICT / UPSERT / RETURNING / CTE / windows | Yes |
| COPY FROM | Yes (admin only); IMPORT INTO optional (offline table) |

## 5. Ranked gotchas (most important first)

1. **Licence/throttle**: a 3-node cluster without an applied licence is throttled to 5 open transactions after 7 days; Free/Trial need telemetry egress. Only single-node dev is exempt.
2. **No `MERGE`, no exclusion constraints, no `CREATE DOMAIN` (v26.2), no session advisory locks, no column-level privileges, no `uuid_v7`** - design around them (partial unique indexes, FOR UPDATE lock rows, table-level grants, enums+CHECK).
3. **Serializable default needs client retry on 40001**; `FOR UPDATE` locks are best-effort under SERIALIZABLE (unless `enable_durable_locking_for_serializable`). RC removes most retries but permits write skew and lost updates.
4. **RLS and triggers cannot be the correctness layer**: RLS is bypassed by FK/unique/PK checks, backup, CDC; triggers are row-level only and TTL deletes fire them. GA labelling of RLS is unverified.
5. **Sequential PKs/indexes hotspot**: use UUIDv4 / `unique_rowid()` / hash-sharded; `gen_random_uuid()` skips uniqueness checks by default; SERIAL maps to INT8 `unique_rowid()`; sequences are replicated writes and leave gaps.
6. **FK child columns are not auto-indexed**; FK to partial unique index not possible; MATCH PARTIAL unsupported; DEFERRABLE unverified; FK checks cost lookups (and locks under RC); virtual computed columns cannot be FK/CHECK columns.
7. **DDL is not fully transactional**: schema changes in multi-statement txns can half-commit (`XXA00`); one schema change at a time; ADD/DROP COLUMN or PK change may need 3x range free space; set `autocommit_before_ddl` consciously. Dropping a column used by a partial-index predicate is blocked.
8. **Row-level TTL**: expired rows remain visible until the job deletes them (filter by the expiration column); inbound `ON DELETE RESTRICT` makes the job fail; `ttl_expire_after` on an existing table rewrites it; cron should be >= `gc.ttlseconds`.
9. **Index/search limits**: GIN accelerates `@>`, not documented for `?`; trigram needs >= 3 chars and no POSIX-regex acceleration; expression indexes cannot be `ON CONFLICT` targets; ON CONFLICT DO UPDATE allows one arbiter only; vector indexes need a feature flag, block writes during backfill, and break `IMPORT INTO`.
10. **Bulk load/ops**: `COPY` is admin-only; `IMPORT INTO` takes the table offline and leaves FKs unvalidated; materialized views refresh manually and not within explicit txns; identifier limit 128 bytes (not enforced yet); keep indexed values < 1 MiB; docker image must be a pinned LTS tag (v26.2.7+), not v26.3 (Innovation).

## 6. Source URLs (fetched 2026-10)
- https://docs.cockroachlabs.com/docs/releases , /releases/release-support-policy , /releases/v26.3 , /releases/v26.2 , /releases/v26.1 , /releases/v25.4 , /releases/v25.2 , /releases/v24.3
- https://docs.cockroachlabs.com/docs/stable/licensing-faqs
- https://docs.cockroachlabs.com/docs/stable/postgresql-compatibility , /v26.2/postgresql-compatibility , /stable/sql-feature-support
- https://docs.cockroachlabs.com/docs/v26.2/ + uuid, hash-sharded-indexes, partial-indexes, expression-indexes, indexes, trigram-indexes, full-text-search, vector-indexes, inverted-indexes, computed-columns, check, foreign-key, triggers, create-trigger, row-level-security, create-policy, user-defined-functions, stored-procedures, set-vars, grant, security-reference/authorization, authentication, alter-role, row-level-ttl, create-changefeed, multiregion-overview, alter-database, partitioning, configure-replication-zones, follower-reads, read-committed, select-for-update, serial, create-sequence, create-type, backup, restore, create-schedule-for-backup, views, refresh, keywords-and-identifiers, encryption, functions-and-operators, online-schema-changes, schema-design-overview, production-checklist, cockroach-start, cockroach-cert, import-into, copy, explain-analyze, cockroachdb-feature-availability
- 404 on fetch (so unverified): /v26.2/merge, /v26.2/known-limitations, /v26.2/set-role, /v26.2/create-domain
