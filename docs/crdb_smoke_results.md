# CockroachDB smoke-test results (real v26.2.8)

Run on 2026-10-16 against the local `main-db` stack (3 insecure nodes behind HAProxy, `cockroach version`: v26.2.8, CCL).
Script: `infra/schema/smoke/smoke.py` (re-runnable; creates and drops the scratch database `smoke`). Follow-up probes (TTL x trigger,
cascade x trigger, `pg_has_role`) were run by hand with `cockroach sql` and are listed in section 2.

## 1. Automated smoke matrix

| # | Mechanism | Result | Notes | s |
|---|---|---|---|---|
| 1 | custom session variable SET app.user_id / current_setting | PASS | value ok; missing_ok returns None | 0.0 |
| 2 | SET LOCAL app.x inside txn reverts at commit; set_config(...,true) | PASS | inside=u1/o1, after commit=None | 0.0 |
| 3 | SET ROLE to member role and back; current_user changes | PASS | as ('sm_ro', 'sm_login') | 0.0 |
| 4 | RLS: ENABLE + policy using current_setting; non-owner sees only own rows | PASS | u1 sees 1, u2 sees 1; with-check blocks cross-owner insert | 0.5 |
| 5 | RLS status via pg_class.relrowsecurity | PASS | relrowsecurity=True | 0.0 |
| 6 | hash-sharded index (secondary) + hash-sharded unique + bucket_count | PASS | rows=100 | 3.4 |
| 7 | partial unique index with deleted_at IS NULL | PASS | dup blocked while live; allowed after soft delete | 1.2 |
| 8 | partial index + STORING + DESC + trigram GIN + JSONB inverted | PASS | ok | 2.8 |
| 9 | computed column LEAST/GREATEST on UUID (STORED) for unordered-pair uniqueness | PASS | unordered-pair uniqueness works | 1.7 |
| 10 | chain FK: composite FK (prior_period_id, subscription_id) -> UNIQUE(id, subscription_id) | PASS | same-parent + single-successor enforced (NULL prior allowed multiple times) | 13.4 |
| 11 | slot table non-overlap: PK (gig_id, slot_start) with exploded slots | PASS | overlapping window rejected atomically | 0.1 |
| 12 | device limit: slot table + SELECT FOR UPDATE, 8 concurrent logins, limit 3 | PASS | 3 granted, 5 denied, active=3 | 2.7 |
| 13 | row-level TTL: ttl_expire_after creates crdb_internal_expiration | PASS | hidden expiration column present=1 | 0.1 |
| 14 | row-level TTL: ttl_expiration_expression with nullable column (NULL = never expires) | PASS | created | 0.1 |
| 15 | TTL job actually deletes expired rows, keeps NULL/future rows (<=150s wait) | PASS | after ~85s: 2 rows left (NULL and future kept) | 89.0 |
| 16 | TTL expression computed from column + interval (ended_at + 30 days) | PASS | ok | 0.2 |
| 17 | CHECK regex ~ (E.164 phone), lower(email), jsonb ? | PASS | regex, lower and jsonb ? checks all reject bad rows | 0.1 |
| 18 | append-only via missing UPDATE/DELETE privilege for non-owner role | PASS | UPDATE/DELETE denied by missing privilege | 0.0 |
| 19 | append-only trigger blocks UPDATE/DELETE even for owner / root | PASS | root blocked by BEFORE UPDATE OR DELETE row trigger | 0.6 |
| 20 | view owner semantics: view readable by role without base-table grant | PASS | view readable, base denied | 0.5 |
| 21 | functions: SQL table function with viewer arg; SECURITY DEFINER keyword | PASS | both created | 0.3 |
| 22 | view using current_setting('app.viewer_id', true) context | PASS | ok | 0.2 |
| 23 | hash functions: hmac, sha256, crypt/gen_salt, digest | PASS | {'hmac': 'yes', 'sha256': 'yes', 'crypt': 'yes', 'digest': 'yes'} | 0.0 |
| 24 | vector type + CREATE VECTOR INDEX (feature.vector_index.enabled) | PASS | vector index created | 0.8 |
| 25 | ALTER ROLE ... SET statement_timeout | PASS | ok | 0.1 |
| 26 | composite FK with ON DELETE SET NULL (col-list) | FAIL | SyntaxError: at or near "(": syntax error | 0.0 |
| 27 | circular FKs via ALTER TABLE ADD CONSTRAINT; ADD CONSTRAINT IF NOT EXISTS support | PASS | ADD CONSTRAINT IF NOT EXISTS supported | 1.0 |
| 28 | DO block (PL/pgSQL anonymous) usable for idempotent DDL incl. EXECUTE | FAIL | FeatureNotSupported: unimplemented: PL/pgSQL EXECUTE of a dynamic SQL string is not yet supported | 0.0 |
| 29 | idempotent forms: CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE VIEW/FUNCTION/TRIGGER | PASS | all accepted | 3.5 |
| 30 | zone config gc.ttlseconds on table | PASS | ok | 0.4 |
| 31 | PARTITION BY LIST (licence check) | PASS | ok | 0.1 |
| 32 | ALTER DEFAULT PRIVILEGES FOR ROLE; REVOKE ALL FROM PUBLIC | PASS | ok | 0.2 |
| 33 | CREATE ROLE ... LOGIN PASSWORD | FAIL | InvalidPassword: setting or updating a password is not supported in insecure mode | 0.0 |
| 34 | stored lower(email) computed column + unique | PASS | ok | 1.7 |
| 35 | CHECK referencing STORED computed column | PASS | ok | 0.1 |

Total 35; pass 32; fail 3


Reading the three FAIL rows (all expected, none blocks the design):

* **#26 `ON DELETE SET NULL (col)`** (PG15 column-list form) is a syntax error in v26.2. The dictionary does not use it (no composite FK has `SET NULL`), so no change.
* **#28 PL/pgSQL `EXECUTE`** is unimplemented. Idempotent DDL therefore uses native `IF NOT EXISTS` / `OR REPLACE` forms, and `ALTER TABLE ... ADD CONSTRAINT IF NOT EXISTS` (supported, row #27) for deferred FKs. No DO-block DDL.
* **#33 `CREATE ROLE ... PASSWORD` on an insecure cluster** is rejected. Logins are created `WITH PASSWORD NULL` (certificate or operator-set password); the local stack ignores passwords.

## 2. Follow-up findings that changed the design

| # | Probe | Result | Design consequence |
|---|---|---|---|
| F1 | Row-level TTL on a table that has a `BEFORE UPDATE OR DELETE` trigger that always raises | The TTL job **fails** (`error during row deletion ... append-only`): TTL deletes fire row triggers in v26.2. | Append-only enforcement is a trigger **plus** privileges, but the trigger must let the TTL job through. Append-only tables **with** TTL get a per-table trigger function that allows DELETE only when the row is already past its TTL expiry (`fn_ao_<table>`); verified: expired row deleted by the job, unexpired row still protected. |
| F2 | `ON DELETE CASCADE` from a parent into a child that has the blocking trigger | The cascade **fires** the trigger and the parent delete fails. 70 FKs cascade into append-only children. | Hard-deleting a parent (retention / erasure by `purge_svc`) must be possible: the trigger also allows DELETE when `fn_is_purge_run()` (current_user is a member of `purge_svc` **and** `SET app.purge_run_id = '<run>'`). Everyone else, including the table owner and root, is blocked. |
| F3 | Trigger body referencing `OLD.col` | Error: must be written `(OLD).col` in CockroachDB. | Generator emits `(OLD).<col>`. |
| F4 | `ttl_expiration_expression` with `(col + INTERVAL '24 months')` and a NULL column | Accepted; NULL means the row never expires (smoke #14/#15: expired row removed ~85 s later, NULL and future rows kept). | Used for every TTL table: `expire_after=<n> column=<c>` becomes `ttl_expiration_expression = '(c + INTERVAL ''n'')'`, `0` expression-only. The 8 tables whose dictionary TTL names no column (implicit `crdb_internal_expiration`) use `created_at` instead, so the trigger can evaluate the same expression. |
| F5 | RLS (`ENABLE ROW LEVEL SECURITY`, policies on `current_setting('app.viewer_id', true)`) | Works for non-owner roles incl. `WITH CHECK` (smoke #4). Custom dotted session variables, `SET LOCAL`, `set_config(...,true)` and `SET ROLE` all work (#1-#3). | The documented design (explicit viewer argument, functions / pair views, no RLS dependence) stays. RLS and `SET LOCAL app.viewer_id` are verified as an optional hardening layer (ADR-043). |
| F6 | Views over base tables, role without base grant (smoke #20) | View is readable and base is denied (owner-privilege semantics). | Masked / default / business views are the persona read path, as designed. |
| F7 | `hmac(bytes, bytes, 'sha256')`, `sha256`, `crypt/gen_salt`, `digest` | All exist (#23). `hmac(bytes, string, string)` signature is not accepted without a cast. | ETL pseudonym function uses two BYTES arguments. |
| F8 | HAProxy/node restarts during a long apply | Connections drop (`server closed the connection`). | `apply.py` reconnects and re-runs the statement (all DDL is idempotent). |
| F9 | `GRANT ... inside DO block` | `unimplemented: GRANT usage inside a function definition`. | Optional GRANTs to infra placeholder roles are separate statements tolerated by `apply.py` when the role is absent. |
| F10 | Device limit with 8 parallel logins against a limit of 3, `SELECT ... FOR UPDATE` on the seat row + slot table + partial unique index | Exactly 3 granted, 5 denied, no errors after client-side 40001 retry (#12). | D-13 / BR-LIC-05/06 mechanism confirmed. |
| F11 | Slot-table non-overlap (#11), unordered pair via `LEAST/GREATEST` computed columns (#9), chain FK with `UNIQUE (id, parent)` and `UNIQUE (prior_period_id)` (#10) | All reject the invalid row and allow the valid one. | P1/P2 patterns confirmed. |
| F12 | `VECTOR(8)` column + `CREATE VECTOR INDEX` after `SET CLUSTER SETTING feature.vector_index.enabled = true` (#24) | Works on v26.2.8. | The dictionary defines no vector index; if added, it needs that cluster setting. |
| F13 | Hash-sharded (secondary and unique), partial, partial-unique, STORING, trigram GIN, JSONB inverted indexes, zone config, `PARTITION BY LIST` | All created; no licence error on the local cluster. | No change. |

Gaps: the TTL job schedule was tested only with a 1-minute cron; production uses `@daily`. `GRANT BACKUP/RESTORE` privilege names are probed in the schema apply (section 4 of `docs/07_schema_justification.md`).
