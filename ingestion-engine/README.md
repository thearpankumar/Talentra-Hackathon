# Talentra Ingestion Engine

Agentic, scenario-driven bulk-data ingestion into CockroachDB (PostgreSQL wire). An LLM (Z.ai **GLM 5** on Amazon Bedrock) designs
thousands of data *scenarios*; a deterministic, multi-process loader turns the YAML/JSON config into rows using `COPY`.
Everything is **schema-driven**: tables, columns, keys, FKs, CHECK constraints and enums are introspected from the live database, so
the real Talentra schema (`../infra/schema/ddl/*.sql`) can be plugged in without code changes.

```
            +-------------------- FastAPI + single-page UI (chat | config | runs) --------------------+
            |   SSE progress  |  proposals (diff, accept/reject)  |  run controls  |  ledger        |
            +---------------------------------------+--------------------------------------------------+
                                                    |
   LangGraph chat agent (ReAct, SQLite checkpoints) |  LangGraph scenario pipeline (SQLite checkpoints, interrupt)
   tools: get/validate/edit config, list scenarios, |  init -> gen_batch* -> propose -> review(interrupt) -> finalize
   start/pause/resume/cancel/status, ledger         |  LLM emits compact "briefs" -> compiled to full, schema-valid scenarios
                                                    v
        Services (config versions, proposals, scenario memory) --- SQLite state (.engine_state/engine.sqlite)
                                                    |
                  Runner: plan -> FK-ordered DAG -> ProcessPool workers -> COPY (one txn per batch) -> CockroachDB
                          pause / resume / cancel / purge / validate / estimate          + talentra_ingest_* ledger tables
```

## Quick start

```powershell
cd ingestion-engine
uv sync
docker compose up -d --wait                     # single-node insecure CockroachDB: SQL on localhost:26357, admin UI http://localhost:8089
uv run python scripts/init_stub_schema.py --reset   # creates DB `talentra` + stub schema + master data (or scripts\dev_up.ps1 / dev_up.sh)
uv run python scripts/tune_crdb.py --unsafe-nosync  # OPTIONAL dev-only speed-ups (no raft fsync, no auto-stats). Never on real data.
cp .env.example .env                            # AWS creds + BEDROCK_* (real .env is git-ignored)
uv run engine serve --port 8000                 # UI at http://127.0.0.1:8000
```

CLI (`uv run engine ...`): `serve`, `gen-scenarios`, `validate`, `estimate` (dry-run), `memory`, `run`, `resume`, `runs`, `report`,
`purge`, `config list|use|show|import|export`, `schema`, `config-schema`, `llm discover|probe`. `Ctrl-C` during `engine run` = pause.

```powershell
uv run engine gen-scenarios --count 500 --theme "blue-collar staffing surge in the Gulf" --accept --out gulf.yaml
uv run engine estimate gulf.yaml
uv run engine run gulf.yaml --workers 8 --batch-size 10000        # asks skip/append/replace for already-ingested scenarios
uv run engine runs ; uv run engine report <run_id> ; uv run engine purge <run_id>
```

## LLM / Bedrock

`BEDROCK_MODEL_ID` (default **`zai.glm-5`**, confirmed in the AWS Bedrock model cards; Converse API) in `BEDROCK_REGION`.
`BEDROCK_FALLBACK_MODEL_IDS` is tried next; `LLM_PROVIDER=auto|bedrock|fake`. `auto` probes Bedrock with a tiny Converse call and falls back
to the offline **FakeToolLLM** (deterministic; supports the chat tool calls and procedural scenario generation) so the whole system and the
test-suite run offline. `uv run engine llm discover|probe` lists GLM/Z.ai models visible to the account and probes the configured one.
A process-wide **token budget** (`LLM_TOKEN_BUDGET`) refuses further calls once spent; boto3 adaptive retries handle throttling.

## Config (YAML primary, JSON accepted) - JSON Schema: `uv run engine config-schema`

```yaml
version: 1
defaults: {batch_size: 10000, workers: 4, throttle_rows_per_sec: null}
scenarios:
  - id: scn_bangalore_it_freshers          # unique
    name: Bangalore IT freshers, heavy Java
    description: ...
    tags: [metro, it, freshers]
    # fingerprint: computed (hash of what the data *is*: tables, distributions, locales, rates - not id/name/seed/counts)
    seed: 7
    locale_mix: {en_IN: 0.7, hi_IN: 0.2, ta_IN: 0.1}      # names in native scripts, phone formats (E.164), emails, languages
    duplicate_rate: 0.03        # near-duplicate rows (copied identity fields + typos, new ids/unique keys)
    null_rate: 0.08             # nullable columns; split into UNKNOWN vs NOT-APPLICABLE when a status companion column exists
    typo_rate: 0.01
    date_range: {start: 2024-01-01, end: 2025-12-31}
    tables:
      - table: users
        record_count: 12000                 # per-scenario, per-table record count lives here
        constants: {role: candidate}
      - table: candidate_profile
        record_count: 12000
        fields:                              # distribution per column
          years_experience:  {dist: exponential, params: {scale: 1.2}, min: 0, max: 4, round: 1}
          expected_salary_min: {dist: lognormal, params: {mu: 13.4, sigma: 0.35}}
          education_level:   {dist: categorical, weights: {bachelor: 0.6, master: 0.2, diploma: 0.2}}
        fk: {user_id: {scope: scenario}}     # 1:1 FK is bijective (no collisions)
      - table: application
        record_count: 20000
        fk:
          job_requirement_id:   {scope: scenario, skew: zipf, alpha: 1.1}      # popular jobs get most applications
          candidate_profile_id: {scope: scenario, skew: pareto, alpha: 1.5}
```

Distributions: `normal lognormal zipf pareto uniform poisson bimodal exponential weights(custom values+weights) categorical constant`
(numbers, dates/timestamps via a unit-interval mapping, categoricals, booleans). `fk.scope`: `scenario` = parents inserted by this scenario,
`any` = sample existing parents from the DB (snapshot stored per run). `skew`: `uniform|zipf|pareto`. Unspecified columns get schema-aware
heuristics (email/phone/name/title/salary/lat-long/DOB/currency by country, enum values from CHECK constraints, `lo <= hi` CHECK pairs repaired).

## Guarantees

* **Idempotent, resumable batches.** A batch is a pure function of `(scenario seed, table, batch index, token block)`; ids come from a
  dedicated RNG stream. Each batch commits data **and** a row in `talentra_ingest_batch` in one transaction (so DB commit and checkpoint are
  atomic); on resume the DB ledger is reconciled into SQLite. Re-running a committed batch is a no-op. Verified by killing the process (`taskkill`) mid-run.
* **Pause / resume / cancel** work in the same process or after restart (`interrupted` runs are resumable). 40001/connection errors retry with jittered backoff.
* **FK-aware ordering** (topological DAG across scenarios); unique keys are collision-free by construction (token blocks for emails/phones/registration
  numbers, per-batch residue classes for composite FK keys); a `UniqueViolation` falls back to a staged `INSERT .. ON CONFLICT DO NOTHING` path.
* **Scenario memory.** Before a run the fingerprint and feature-set of each scenario are compared (exact / same id / Jaccard >= 0.75 "similar") with the
  SQLite ledger **and** the DB table `talentra_ingest_ledger`. Matches require a per-scenario decision: `skip`, `append` (new seed, new ids) or `replace`
  (purge the old run's rows, then load). UI: modal; API: `409 {findings}`; chat: the agent asks.
* **Purge by run** regenerates the keys of every committed batch and deletes exactly those rows (reverse FK order); `report` validates batch completeness,
  ledger-vs-checkpoint counts, unvalidated constraints, orphans and null ratios.

## Pluggable real schema

1. Apply the real DDL to the target DB: `uv run python scripts/init_stub_schema.py --schema-dir ../infra/schema/ddl` (or point `TARGET_DB_URL` at it).
2. Optional: `DATA_DICTIONARY_PATH=../docs/05_data_dictionary.xlsx` enriches columns with classification/description (sheet headers `table, column, classification, description`).
3. `uv run engine schema` shows what was introspected; `engine validate cfg.yaml` checks every table/column in a config. Add per-table `fields` / `fk` for
   realism; extend `src/ingestion_engine/locales.py` (names/phrases) and the heuristics in `generate.py::_gen_heuristic` for domain columns.
   Requirements on the DDL: single-column surrogate PK (uuid/int/text) per table (junction tables may have composite PKs of FKs), FKs enforced.

## API (used by the UI)

`GET/PUT /api/config`, `/api/config/versions[/{id}[/activate]]`, `POST /api/chat`, `GET /api/chat/{session}`, `GET /api/proposals`, `POST /api/proposals/{id}/accept|reject`,
`POST /api/scenarios/generate`, `POST /api/runs/precheck`, `POST /api/runs`, `POST /api/runs/{id}/pause|resume|cancel|purge`, `GET /api/runs[/{id}[/report|/events(SSE)]]`,
`GET /api/ledger`, `GET /api/llm/status|discover`, `GET /api/schema`.

## Tests

`uv run pytest` (needs the compose CockroachDB; uses its own DB `talentra_test`; the browser test uses Playwright + installed Chrome and skips if absent).
Covers config validation/fingerprints, distributions, introspection, deterministic generation, unknown-vs-NA nulls, duplicates, multilingual scripts, full runs
with exact counts, pause/resume, hard-kill/resume, lost-checkpoint idempotency, scenario memory (SQLite + DB), purge, chat tool flows, LangGraph interrupt surviving a restart,
1,500-scenario generation, FastAPI endpoints, and a real-browser UI flow.

## Performance notes

Generation is vectorised (NumPy) and runs in worker processes; the limit on a laptop is CockroachDB CPU for tables with FK + unique-index + CHECK work. Tuning knobs:
`--workers`, `--batch-size` (5-20k), `scripts/tune_crdb.py`, short `gc.ttlseconds` (done by the tune script) to avoid GC-job pile-ups after `DROP DATABASE`.
