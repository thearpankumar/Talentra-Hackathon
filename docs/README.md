# Talentra DB: documentation suite

Engine: **CockroachDB v26.2 (PostgreSQL dialect)**. Source of truth for the schema: `05_data_dictionary.xlsx` (format in `05_data_dictionary_schema.md`). Everything else is derived from, or checked against, it.

## Reading order
1. `00_milestone_traceability.xlsx` : B1-B82 mapped to artifacts and status.
2. `01_er/module_map.md` then `01_er/<module>.md` : Mermaid ER per module (12 modules incl. Tier 3, plus 5 labelled extension modules `ext_*`).
3. `02_adr_log.xlsx` / `02_adr_log.md` : architecture decisions.
4. `03_module_ownership.xlsx` : modules, tables, owners, cross-module FKs.
5. `04_design_conventions.xlsx` : naming, keys, domains, patterns.
6. `05_data_dictionary.xlsx` : tables, columns, enums, indexes, constraints, FKs, views, partitioning.
7. `06_business_rules.xlsx` : rule to enforcement mechanism (all implementable on CockroachDB).
8. `07_schema_justification.md`, `08_rule_compliance.xlsx` : justification, Section 5 compliance and deviations.
9. `09_integration_boundaries.xlsx`, `10_competitor_benchmark.xlsx`.
10. `11_performance_indexing_plan.md` (+ `11_index_catalogue.xlsx`), `12_seed_and_validation_plan.md` (+ `12_seed_targets.xlsx`).
11. `13_rbac_matrix.xlsx`, `14_threat_model.md`, `15_security_design_note.md`, `20_backup_restore_log.xlsx` (template; measurements filled in later).

## Tooling (`docs/tools/`)
* Authoring input: `docs/_staging/*.yaml` (per-module staging files; contract in `_staging/FORMAT_AND_DECISIONS.md`; verified CockroachDB capabilities in `_staging/crdb_capabilities.md`).
* `python docs/tools/reconcile.py` : de-duplicates cross-module definitions, aligns lookup/identifier domains.
* `python docs/tools/lint_dictionary.py` : fails on rule violations (naming, reserved words, domains, nullability justification, FK rules, audit columns, date-range checks, index names...).
* `python docs/tools/build_dictionary.py` : writes `05_data_dictionary.xlsx`, `01_er/*.md`, `03_module_ownership.xlsx`.
* `python docs/tools/build_misc.py` : writes `06_business_rules.xlsx`, `00_milestone_traceability.xlsx`, `05_data_dictionary_schema.md`.
* Other generators (`build_rbac.py`, `gen_*.py`, ...) regenerate the remaining files; each is named after its output.

Regenerate order: reconcile, lint, build_dictionary, build_misc, then the other generators.
