# Data dictionary format (05_data_dictionary.xlsx)

`docs/05_data_dictionary.xlsx` is the single source of truth (SSOT) for the schema. It is generated from `docs/_staging/*.yaml` by `python docs/tools/build_dictionary.py` after `python docs/tools/lint_dictionary.py` passes with 0 errors. A DDL generator must read **only** this workbook (sheet names and header names below are stable; extra columns may be appended at the end but never inserted or renamed).

Engine: CockroachDB v26.2 (PostgreSQL dialect). All names are lowercase snake_case, never quoted. Booleans in sheets are `YES`/`NO`.

Current content: 1306 tables (538 lookups), 15784 columns, 78 views, 457 business rules.

## Sheets

### Tables (one row per table)
`table, module, owner, kind, delete_policy, volume, tier, milestones, max_sensitivity, column_count, audit_columns, ttl, partitioning, retention, description`
* `kind`: entity|link|event|ledger|audit|snapshot|config|dim|fact|agg|bridge|staging|lookup.
* `delete_policy`: soft (table has `deleted_at`, `deleted_by`), hard, append_only (grant INSERT/SELECT only; no UPDATE/DELETE).
* `audit_columns`: `full` (created_at/by + updated_at/by) or `created_only`.
* `ttl`: empty or `expire_after=<interval> column=<timestamp column>` -> `WITH (ttl_expire_after = '<interval>')` when column is the implicit `crdb_internal_expiration`; when `column` is a timestamp column use `ttl_expiration_expression = '<column>'` (expire_after `0 seconds` means expression-only TTL).
* `volume`: low|medium|high|very_high (drives hash-sharding and seed sizes).
* Create tables in dependency order computed from the ForeignKeys sheet (topological sort; add FKs via `ALTER TABLE ... ADD CONSTRAINT` after all tables when cycles exist).

### Columns (one row per column, ordered by `ordinal`)
`table, ordinal, column, type, domain, nullable, nullability_justification, default, sensitivity_class, delete_policy, pii_treatment, computed_expr, is_pk, fk_ref, standard_column, description`
* `type` is a CockroachDB SQL type literal: `UUID`, `STRING(n)`, `STRING`, `BOOL`, `INT2/4/8`, `DECIMAL(p,s)`, `DATE`, `TIMESTAMPTZ`, `JSONB`, `BYTES`, `INTERVAL`, `INET`, `FLOAT4/8`, `TIME`.
* `nullable`: YES -> omit NOT NULL; NO -> `NOT NULL`. `default` is a SQL expression string (empty = none).
* `computed_expr`: empty or `<expr> STORED|VIRTUAL` -> `AS (<expr>) STORED|VIRTUAL`.
* `is_pk`: YES for `id` (`UUID NOT NULL DEFAULT gen_random_uuid()` PRIMARY KEY, named `pk_<table>`).
* `fk_ref`: `<table>.<column>` when the column participates in an FK (details in ForeignKeys).
* `domain` + `sensitivity_class` drive masking/grant generation; `pii_treatment` is free text for the security notes.
* `standard_column`: YES for auto-injected id/created_*/updated_*/deleted_*.

### Enums (lookup seed values, long format)
`lookup_table, code, sort_order, is_active, language_tag, display_name, description`
One row per (lookup, code, language). Seed each lookup table: `code`, `sort_order`, `is_active`, and `name_i18n` = JSON object {language_tag: display_name} built from all rows of the code. `en` is mandatory. Lookup table columns: `code STRING(64)`, `name_i18n JSONB`, `sort_order INT4`, `is_active BOOL` (see Columns sheet).

### Indexes
`table, index_name, columns, unique, hash_sharded, bucket_count, inverted, where_predicate, storing, kind, description, opclass`
* `columns`: comma-separated; entries may carry ` DESC`. Hash-sharded: `CREATE INDEX name ON t (cols) USING HASH WITH (bucket_count = n)`. Inverted: JSONB columns use `CREATE INVERTED INDEX` (plain, `opclass` empty); STRING columns are trigram indexes `CREATE INDEX ... USING GIN (col gin_trgm_ops)` and always carry `opclass = gin_trgm_ops`. `opclass` is the last column of the sheet. `where_predicate` -> partial index. `storing` -> `STORING (...)`.
* `unique = YES` entries are `CREATE UNIQUE INDEX uq_...` (partial unique rules live here).

### Constraints
`table, constraint_name, constraint_type, columns, expression, description`
`PRIMARY KEY` (always `id`), `UNIQUE` (non-partial; `columns`), `CHECK` (`expression`).

### ForeignKeys
`table, fk_name, columns, ref_table, ref_columns, on_delete, on_update, ref_module, cross_module, index_exempt, description`
Multi-column FKs use comma-separated `columns`/`ref_columns` in matching order and rely on a UNIQUE constraint on the referenced columns. Use `MATCH SIMPLE` (default) unless the description says otherwise.

### Views
`view_name, kind, module, tables, purpose, security, sql_sketch` -- `sql_sketch` is design intent (parameter names, predicates), to be completed by the DDL author; `kind` view|materialized.

### Partitioning
`table, module, volume, strategy, key_or_columns, naming_pattern, notes` -- hash-sharded indexes, TTL and any zone-config/partition design per table.

## Related files
* `docs/06_business_rules.xlsx`: rule -> enforcement mechanism (must be implemented by the DDL: CHECK/UNIQUE/FK/chain/slot/privileges/TTL).
* `docs/13_rbac_matrix.xlsx`: grants per role.
* `docs/_staging/FORMAT_AND_DECISIONS.md`: binding design decisions D-01..D-20.
