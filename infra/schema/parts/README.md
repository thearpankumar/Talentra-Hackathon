# Hand-authored schema parts

`infra/schema/ddl/*.sql` is **generated** from `docs/05_data_dictionary.xlsx` (+ rules and RBAC sheets) by `infra/schema/generate_ddl.py`
and must not be edited. Everything the dictionary cannot express declaratively lives here and is merged with `ddl/` by file name
(`apply.py` sorts both directories together):

| Prefix | Content | Order relative to generated files |
|---|---|---|
| `views/<view_name>.sql` | override for one business view (`CREATE OR REPLACE VIEW <view_name> AS ...;`). If present it replaces the dictionary `sql_sketch`; each override is listed in `docs/07_schema_justification.md` section 12 | picked up by `52_views_business.sql` |
| `60_rules_<area>.sql` | business-rule procedures/functions (device limit login claim, non-overlap slots, wallet debit, ...) | after views (50-52), before grants (70) |
| `65_viewer_<area>.sql` | viewer-scoped functions (`fn_*` taking the viewer id) | after 60, before 70 |
| `75_post_grants_<area>.sql` | GRANT EXECUTE on the functions above, extra REVOKEs | after `70_grants.sql` |
| `90_seed_<area>.sql` | optional reference seed rows that are not lookups | last |

Rules for every file: idempotent (`CREATE OR REPLACE`, `IF NOT EXISTS`, `ON CONFLICT DO NOTHING`), statements separated by a line
`-- @@`, no `DO` blocks that need `EXECUTE` (unsupported on v26.2), no `GRANT` inside functions, PL/pgSQL records are accessed as `(OLD).col`.
Objects are created by `apply.py` under `SET ROLE schema_owner`.
