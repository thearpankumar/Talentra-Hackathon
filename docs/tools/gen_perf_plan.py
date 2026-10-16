"""Generate docs/11_performance_indexing_plan.md and docs/11_index_catalogue.xlsx from the dictionary + perf_model + perf_extrap.

python docs/tools/gen_perf_plan.py
"""
import math, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from collections import Counter, defaultdict
import perf_model as pm
import perf_extrap as px
import fixed_queries as fq
from dictlib import ROOT, MODULES, MODULE_OWNER
from xl import new_wb, add_sheet

GB = pm.GB
DISK_FACTOR = 0.6   # assumed on-disk / logical (Pebble compression); calibrate in B70
M = pm.Model()
T = M.T
ST = {s["name"]: s for s in M.all_stats()}
errs = fq.validate()
assert not errs, errs


def tsize(n, scale):
    s = ST[n]
    rows = s["laptop_rows"] if scale == "laptop" else s["full_rows"]
    return rows * s["row_bytes"], rows * s["idx_bytes_row"]


def gb(x):
    return x / GB


def fmt_rows(n):
    return f"{n:,}"


def ix_def(t, name):
    tb = T[t]
    ix = next(i for i in tb["indexes"] if i["name"] == name)
    u = "UNIQUE " if ix.get("unique") else ""
    if ix.get("inverted"):
        col = ix["columns"][-1]
        last = col.split()[0]
        c = next(c for c in tb["columns"] if c["name"] == last)
        if c["type"].startswith("STRING") and "gin_trgm_ops" not in col:
            col = f"{last} gin_trgm_ops"
        s = f"CREATE INDEX {name} ON {t} USING GIN ({col})"
    else:
        s = f"CREATE {u}INDEX {name} ON {t} ({', '.join(ix['columns'])})"
        if ix.get("hash"):
            s += f" USING HASH WITH (bucket_count = {ix.get('bucket_count', 16)})"
        if ix.get("storing"):
            s += f" STORING ({', '.join(ix['storing'])})"
    if ix.get("where"):
        s += f" WHERE {ix['where']}"
    return s


def classify(t, ix):
    cm = {c["name"]: c for c in t["columns"]}
    if ix.get("inverted"):
        c = cm[ix["columns"][-1].split()[0]]
        return "trigram (inverted)" if c["type"].startswith("STRING") else "inverted (JSONB)"
    parts = []
    if ix.get("hash"):
        parts.append(f"hash-sharded/{ix.get('bucket_count', 16)}")
    if ix.get("unique"):
        parts.append("unique")
    if ix.get("where"):
        parts.append("partial")
    if ix.get("storing"):
        parts.append("covering (STORING)")
    return ", ".join(parts) or "btree"


# ---- query -> index serving map -------------------------------------------------------------------------------------
SERVES = defaultdict(list)
for qq in fq.Q:
    for iname in qq["indexes"]:
        SERVES[iname.split(" ")[0]].append(qq["id"])
LOOKUPS = {t["name"] for t in M.m["tables"] if t["is_lookup"]}


def purpose(t, ix):
    nm = ix["name"]
    cm = {c["name"]: c for c in t["columns"]}
    fkcols = {fk["columns"][0]: fk for fk in t["fks"] if len(fk["columns"]) == 1}
    first = ix["columns"][0].split()[0]
    kind = []
    if ix.get("unique"):
        kind.append("enforces uniqueness")
    if ix.get("hash"):
        kind.append("spreads time-ordered writes")
    if ix.get("inverted"):
        kind.append("search")
    if first in fkcols and not ix.get("hash"):
        kind.append("FK child lookup / parent delete check")
    return "; ".join(kind) or "access path"


# ---- hot spot analysis ---------------------------------------------------------------------------------------------------
HOT_TIME = []   # high volume, leading timestamp, not hash
HOT_ENUM = []   # high volume, leading lookup FK then timestamp
for t in M.m["tables"]:
    if t["volume"] not in ("high", "very_high"):
        continue
    cm = {c["name"]: c for c in t["columns"]}
    fkcol = {fk["columns"][0]: fk["ref"] for fk in t["fks"] if len(fk["columns"]) == 1}
    for ix in t["indexes"]:
        cols = [c.split()[0] for c in ix["columns"]]
        if ix.get("hash") or ix.get("inverted"):
            continue
        c0 = cm.get(cols[0])
        if c0 and c0["type"].startswith("TIMESTAMPTZ"):
            HOT_TIME.append((t["module"], t["name"], ix["name"], t["volume"], ix.get("where") or "-"))
        elif fkcol.get(cols[0]) in LOOKUPS and len(cols) > 1 and cm.get(cols[1], {}).get("type", "").startswith("TIMESTAMPTZ"):
            HOT_ENUM.append((t["module"], t["name"], ix["name"], t["volume"], ix.get("where") or "-"))
HASH_COUNT = Counter()
for t in M.m["tables"]:
    for ix in t["indexes"]:
        if ix.get("hash"):
            HASH_COUNT[ix.get("bucket_count", 16)] += 1
NO_OPS = []
for t in M.m["tables"]:
    cm = {c["name"]: c for c in t["columns"]}
    for ix in t["indexes"]:
        if ix.get("inverted") and cm[ix["columns"][-1].split()[0]]["type"].startswith("STRING") and "gin_trgm_ops" not in " ".join(ix["columns"] + [str(ix.get("opclass", ""))]):
            NO_OPS.append((t["name"], ix["name"]))

# ======================================================================================================================
# index catalogue workbook
# ======================================================================================================================
def build_catalogue():
    rows = []
    for t in M.m["tables"]:
        s = ST[t["name"]]
        for nm, kind, per_row in s["indexes"]:
            ix = next((i for i in t["indexes"] if i["name"] == nm), None)
            if ix is None:
                rows.append([t["module"], t["name"], nm, "unique constraint", "", "", "", "", "enforces uniqueness (natural key / idempotency key)",
                             ", ".join(SERVES.get(nm, [])) or "write-path uniqueness check",
                             round(s["laptop_rows"] * per_row / 1048576, 1), round(s["full_rows"] * per_row / GB, 3)])
                continue
            q = ", ".join(SERVES.get(nm, [])) or ("FK check/child lookup" if purpose(t, ix).startswith("FK") else ("TTL job scan" if re.search(r"expires_at|_expir", nm) else "module query (see description)"))
            rows.append([t["module"], t["name"], nm, classify(t, ix), ", ".join(ix["columns"]), ", ".join(ix.get("storing") or []),
                         ix.get("where") or "", ix.get("bucket_count") if ix.get("hash") else "", (ix.get("desc") or purpose(t, ix))[:300], q,
                         round(s["laptop_rows"] * per_row / 1048576, 1), round(s["full_rows"] * per_row / GB, 3)])
    wb = new_wb()
    hdr = ["module", "table", "index / unique constraint", "type", "columns", "storing", "partial predicate", "hash buckets", "purpose", "query it serves", "est. size laptop (MiB)", "est. size full (GiB)"]
    add_sheet(wb, "Index catalogue", hdr, rows, widths=[22, 32, 56, 26, 40, 30, 36, 8, 60, 28, 12, 12], wrap_cols=("purpose",), freeze_cols=3)
    # module summary
    msum = defaultdict(lambda: Counter())
    msz = defaultdict(float)
    for r in rows:
        k = r[0]
        msum[k]["indexes+unique"] += 1
        typ = r[3]
        for key in ("hash-sharded", "partial", "unique", "trigram", "inverted", "covering"):
            if key in typ:
                msum[k][key] += 1
        msz[k] += r[10]
    mrows = []
    for k in MODULES:
        c = msum[k]
        mrows.append([k, MODULES[k], c["indexes+unique"], c["hash-sharded"], c["partial"], c["unique"] + c["unique constraint"], c["trigram"], c["inverted"], c["covering"], round(msz[k] / 1024, 2)])
    add_sheet(wb, "By module", ["module_key", "module", "indexes + unique constraints", "hash-sharded", "partial", "unique", "trigram", "inverted JSONB", "covering", "est. index GiB laptop"], mrows, widths=[24, 50, 12, 10, 10, 10, 10, 10, 10, 12])
    # table sizes
    trows = []
    for s in sorted(ST.values(), key=lambda s: -s["full_rows"] * (s["row_bytes"] + s["idx_bytes_row"])):
        n = len(s["indexes"])
        trows.append([s["module"], s["name"], s["kind"], s["volume"], s["driver"], s["laptop_rows"], s["full_rows"], round(s["row_bytes"]), round(s["idx_bytes_row"]), n,
                      round(gb(s["laptop_rows"] * s["row_bytes"]), 3), round(gb(s["laptop_rows"] * s["idx_bytes_row"]), 3),
                      round(gb(s["full_rows"] * s["row_bytes"]), 2), round(gb(s["full_rows"] * s["idx_bytes_row"]), 2)])
    add_sheet(wb, "Table sizes", ["module", "table", "kind", "volume class", "scale driver", "laptop rows", "full rows", "row bytes", "index bytes per row", "indexes", "laptop data GiB", "laptop index GiB", "full data GiB", "full index GiB"],
              trows, widths=[22, 38, 9, 10, 8, 13, 14, 9, 9, 8, 11, 11, 11, 11], freeze_cols=2)
    add_sheet(wb, "Hot-spot candidates", ["pattern", "module", "table", "index", "volume", "partial predicate", "assessment"],
              [["leading timestamp, not hash-sharded"] + list(h) + ["Sequential key at the write head. Acceptable only when the partial predicate keeps the index tiny or sparse (due-queues); otherwise hash-shard."] for h in HOT_TIME]
              + [["leading low-cardinality lookup + timestamp"] + list(h) + ["Few distinct leading values concentrate writes on few key prefixes with a rising timestamp: hot-range risk when volume is high; consider (hash-shard) or leading on a high-cardinality column."] for h in HOT_ENUM],
              widths=[30, 22, 30, 60, 10, 36, 80], wrap_cols=("assessment",))
    # FK overhead
    frows = []
    for t in M.m["tables"]:
        fks = t["fks"]
        if not fks:
            continue
        nl = sum(1 for f in fks if f["ref"] in LOOKUPS)
        ne = len(fks) - nl
        s = ST[t["name"]]
        frows.append([t["module"], t["name"], t["volume"], len(fks), nl, ne, len(t["indexes"]) + sum(1 for c in t["constraints"] if c["type"] == "unique"), s["laptop_rows"], s["full_rows"]])
    frows.sort(key=lambda r: (-r[5], -r[3]))
    add_sheet(wb, "FK overhead", ["module", "table", "volume", "FKs", "FKs to lookups (cached, cheap)", "FKs to entities (point read, maybe remote)", "secondary indexes + unique", "laptop rows", "full rows"], frows, widths=[22, 38, 10, 6, 14, 16, 12, 13, 14], freeze_cols=2)
    qrows = []
    for qq in fq.Q:
        qrows.append([qq["id"], qq["kind"], qq["title"], "yes" if qq["explain"] else "no", qq["target_p95_ms"], "; ".join(qq["indexes"])])
    add_sheet(wb, "Query-index map", ["query", "kind", "title", "EXPLAIN ANALYZE (top ten)", "target p95 ms (laptop)", "indexes relied on"], qrows, widths=[7, 14, 60, 12, 12, 100], wrap_cols=("title", "indexes relied on"))
    out = os.path.join(ROOT, "docs", "11_index_catalogue.xlsx")
    wb.save(out)
    return len(rows), out


# ======================================================================================================================
# markdown
# ======================================================================================================================
def md_table(headers, rows):
    s = "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n"
    for r in rows:
        s += "| " + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in r) + " |\n"
    return s


KEY_TABLES = ["candidate_profile", "candidate_skill", "candidate_experience", "candidate_education", "resume", "candidate_profile_view", "candidate_activity_log",
              "job_requirement", "job_requirement_skill", "job_application", "job_application_status_history", "questionnaire_answer", "candidate_job_search_history", "job_view_event",
              "app_user", "audit_event", "identity_session", "device_session", "login_attempt", "wallet_ledger_entry", "profile_download", "payment_order", "invoice",
              "notification", "notification_delivery", "notification_delivery_event", "provider_webhook_event", "job_alert_match", "feed_item", "outreach_recipient", "gig_slot",
              "gig_fulfilment_event", "fact_application_event", "fact_search_event", "agg_funnel_daily", "dim_candidate"]


def totals(scale):
    d = i = 0
    for s in ST.values():
        rows = s["laptop_rows"] if scale == "laptop" else s["full_rows"]
        d += rows * s["row_bytes"]
        i += rows * s["idx_bytes_row"]
    return d, i


def module_totals():
    rows = []
    for k in MODULES:
        ld = li = fd = fi = 0
        n = 0
        for s in ST.values():
            if s["module"] != k:
                continue
            n += 1
            ld += s["laptop_rows"] * s["row_bytes"]; li += s["laptop_rows"] * s["idx_bytes_row"]
            fd += s["full_rows"] * s["row_bytes"]; fi += s["full_rows"] * s["idx_bytes_row"]
        rows.append((k, n, ld, li, fd, fi))
    return rows


JUST = {
    "master_data": ("city", "idx_city_name_search",
                    "SELECT id, display_name, state_region_id FROM city WHERE country_id = $1 AND is_active AND name_search LIKE '%' || lower($2) || '%' ORDER BY is_popular DESC, population DESC LIMIT 10",
                    "Without the trigram index the optimizer can only use idx_city_country_id_name_search for the country prefix and filter the infix match: scans every active city of the country (up to 150k rows at laptop scale, ~7,000 for a typical country).",
                    "Inverted-index scan over the trigram posting lists of the typed text (3+ characters), index join of the few hundred matches, top-10 sort."),
    "profile": ("candidate_profile", "idx_candidate_profile_current_city_id_availability_status_id",
                "SELECT cp.id, cp.current_job_title, cp.total_experience_months FROM candidate_profile cp WHERE cp.current_city_id = $1 AND cp.deleted_at IS NULL AND cp.is_searchable AND cp.total_experience_months BETWEEN 36 AND 96",
                "Only single-column FK index idx_candidate_profile_current_country_id / state exist; city filter becomes a FULL SCAN of 1M profiles (about 1 KB each).",
                "Partial covering index scan: prefix constraint on current_city_id, STORING provides current_job_title and total_experience_months so no index join; only searchable, live profiles are indexed."),
    "jobs_applications": ("job_application", "idx_job_application_job_requirement_id_status_applied", fq.Q[2]["sql"][0],
                          "Fallback idx_job_application_organisation_id_application_status_id_status_changed_at cannot serve a per-requirement filter: full or tenant-wide scan then sort 5M rows.",
                          "Constrained scan on (job_requirement_id, application_status_id) in applied_at DESC order with LIMIT 50: no sort, ~50 rows read."),
    "administration": ("user_block", "uq_user_block_blocker_app_user_id_blocked_app_user_id_open",
                       "SELECT 1 FROM user_block WHERE blocker_app_user_id = $1 AND blocked_app_user_id = $2 AND effective_to IS NULL",
                       "Falls back to idx_user_block_blocker_app_user_id_effective_from: scans all blocks of the candidate (heavy blockers have thousands) and filters.",
                       "Unique point lookup (2 key columns, partial WHERE effective_to IS NULL): 1 KV read; also guarantees at most one open block per pair."),
    "organisation_account": ("recruiter_organisation_mapping", "idx_recruiter_organisation_mapping_organisation_id_recruiter_app_user_id",
                             "SELECT recruiter_app_user_id, mapping_scope_id, effective_from FROM recruiter_organisation_mapping WHERE organisation_id = $1 AND effective_to IS NULL",
                             "Falls back to the unique (recruiter_app_user_id, organisation_id) indexes, which cannot be entered by organisation: full scan of the mapping table (60k rows laptop, 600k full).",
                             "Covered partial index scan (open mappings only): one span per organisation, no index join (STORING mapping_scope_id, effective_from)."),
    "gig_marketplace": ("gig_slot", "uq_gig_slot_gig_worker_profile_id_slot_start_at",
                        "SELECT count(*) FROM gig_slot WHERE gig_worker_profile_id = $1 AND slot_start_at >= $2 AND slot_start_at < $3",
                        "Only idx_gig_slot_slot_start_at (hash-sharded) leads on time: the check becomes a scan across 16 buckets for the window then filter by worker (2M rows laptop). The uniqueness (overlap prevention) could not be enforced at all.",
                        "Range scan inside one worker's key prefix: the same index that enforces non-overlap answers 'is this window free' in O(slots in window) = 4 per hour."),
    "licences_devices": ("device_session", "uq_device_session_device_slot_id",
                         "SELECT 1 FROM device_session s WHERE s.device_slot_id = $1 AND s.ended_at IS NULL",
                         "idx_device_session_licence_seat_id (partial WHERE ended_at IS NULL) answers per seat, but slot exclusivity under concurrent logins is not enforced by it.",
                         "Partial unique point lookup; concurrent second claim of the same slot fails with a unique violation: limit enforced in the database."),
    "sourcing_consultant": ("candidate_contact_attempt", "idx_candidate_contact_attempt_candidate_profile_id_attempted_at",
                            "SELECT count(*) FROM candidate_contact_attempt WHERE candidate_profile_id = $1 AND attempted_at > now() - interval '7 days'",
                            "Only the hash-sharded attempted_at index leads on time: 16-bucket scan for 7 days of ALL candidates, then filter (about 1M rows per week at laptop scale).",
                            "Prefix scan on one candidate then the newest rows first: reads only that candidate's attempts of the last 7 days (usually 0-5 rows)."),
    "subscriptions_payments": ("payment_order", "idx_payment_order_payment_order_status_id_expires_at",
                               "SELECT id FROM payment_order WHERE payment_order_status_id = $1 AND expires_at < now() AND paid_at IS NULL ORDER BY expires_at LIMIT 500",
                               "Without the partial index the sweeper scans idx_payment_order_organisation_id_created_at or the full table (300k rows laptop, 3M full) every run.",
                               "Partial index (WHERE paid_at IS NULL) contains only unsettled orders; the scan starts at the oldest expires_at and stops at LIMIT 500."),
    "notifications_community": ("notification", "idx_notification_recipient_app_user_id_created_at_unread",
                                fq.Q[9]["sql"][0],
                                "idx_notification_recipient_app_user_id_created_at scans the user's whole recent history (read and unread, up to 180 days) to find unread rows.",
                                "Partial index holds unread rows only: reads at most the latest 20 unread entries of the user."),
    "analytics": ("agg_funnel_daily", "PROPOSED idx_agg_funnel_daily_dim_job_category_id_period_date",
                  fq.Q[11]["sql"][0],
                  "Existing idx_agg_funnel_daily_period_date is hash-sharded (8 buckets) and not covering: 8 range spans + index join per row for the category filter and the measures.",
                  "CREATE INDEX idx_agg_funnel_daily_dim_job_category_id_period_date ON agg_funnel_daily (dim_job_category_id, period_date) STORING (applications, shortlisted, joined): one covered range scan, ~90 rows for a 90-day window. Table is an aggregate (not append-hot), so the index needs no hash sharding."),
    "tier3_reference": ("salary_benchmark", "idx_salary_benchmark_job_role_id_country_id_reference_period_start_date",
                        "SELECT p25_amount, p50_amount, p75_amount FROM salary_benchmark WHERE job_role_id = $1 AND country_id = $2 AND is_publishable ORDER BY reference_period_start_date DESC LIMIT 1",
                        "idx_salary_benchmark_country_id then filter by job_role and sort: scans every benchmark of the country.",
                        "Partial covering index: latest published period of (role, country) in one KV read."),
}


def just_section():
    out = []
    n = 0
    for mod, (tbl, idx, sql, before, after) in JUST.items():
        n += 1
        real = idx.replace("PROPOSED ", "")
        if not idx.startswith("PROPOSED"):
            assert any(i["name"] == idx for i in T[tbl]["indexes"]) or any(c["name"] == idx for c in T[tbl]["constraints"]), idx
            ddl = ix_def(tbl, idx) if any(i["name"] == idx for i in T[tbl]["indexes"]) else f"-- declared as UNIQUE constraint {idx} on {tbl}"
        else:
            ddl = after.split(": ")[0].replace("CREATE INDEX", "CREATE INDEX")
            ddl = "CREATE INDEX idx_agg_funnel_daily_dim_job_category_id_period_date ON agg_funnel_daily (dim_job_category_id, period_date) STORING (applications, shortlisted, joined)"
        ws = ST[tbl]
        b = MODULES[mod] + " (" + mod + ")"
        out.append(f"#### 7.{n} {b}: `{real}` on `{tbl}`\n")
        out.append(f"* Backlog: {'B71' if mod in ('administration', 'organisation_account', 'gig_marketplace') else ('B72' if mod in ('licences_devices', 'sourcing_consultant', 'subscriptions_payments') else 'B73' if mod in ('notifications_community', 'analytics') else 'B70/B71 (common modules, team)')}. Table rows: {fmt_rows(ws['laptop_rows'])} laptop, {fmt_rows(ws['full_rows'])} full.")
        out.append(f"* DDL: `{ddl}`")
        out.append("* Query:\n```sql\n" + sql + ";\n```")
        out.append(f"* Before (index invisible or dropped), predicted: {before}")
        out.append(f"* After, predicted: {after}")
        out.append("* Measurement template (fill from B70 harness; keep the raw `EXPLAIN ANALYZE` files in `perf/b71/<module>/`):\n")
        out.append(md_table(["metric", "before", "after", "delta"], [["p50 ms", "", "", ""], ["p95 ms", "", "", ""], ["p99 ms", "", "", ""], ["rows read (EXPLAIN ANALYZE)", "", "", ""], ["KV bytes read", "", "", ""], ["plan shape (scan / index join / sort)", "", "", ""], ["index size on disk (SHOW RANGES ... WITH INDEXES)", "-", "", ""], ["write cost: INSERT p95 ms on the table", "", "", ""]]))
    return "\n".join(out)


def ext_just():
    rows = []
    for k in MODULES:
        if not k.startswith("ext_"):
            continue
        best = None
        for t in M.m["tables"]:
            if t["module"] != k:
                continue
            s = ST[t["name"]]
            for nm, kind, per_row in s["indexes"]:
                ix = next((i for i in t["indexes"] if i["name"] == nm), None)
                if ix is None or ix.get("unique"):
                    continue
                size = s["full_rows"] * per_row
                if best is None or size > best[0]:
                    best = (size, t["name"], nm, kind, (ix.get("desc") or "")[:160])
        rows.append([k, best[1], f"`{best[2]}`", best[3], f"{gb(best[0]):.1f} GiB", best[4]])
    return md_table(["extension module", "table", "largest non-unique index (candidate to justify)", "type", "est. size at full scale", "purpose (dictionary)"], rows)


def build_md():
    L = []
    A = L.append
    ld, li = totals("laptop")
    fd, fi = totals("full")
    nidx = sum(len(t["indexes"]) for t in M.m["tables"])
    nuq = sum(1 for t in M.m["tables"] for c in t["constraints"] if c["type"] == "unique")
    nfk = sum(len(t["fks"]) for t in M.m["tables"])
    ntab = len(M.m["tables"])
    A("# 11. Performance and indexing plan (B65, B70-B74)\n")
    A("Target engine: CockroachDB v26.2 (LTS line), PostgreSQL dialect, 3-node Docker cluster. Everything below is computed from the consolidated data dictionary (`docs/_staging/*.yaml`, built into `docs/05_data_dictionary.xlsx`) by `python docs/tools/gen_perf_plan.py` (inputs: `docs/tools/perf_model.py`, `perf_extrap.py`, `fixed_queries.py`). The companion workbook `docs/11_index_catalogue.xlsx` lists every index.\n")
    A("**Status of numbers.** Row counts, sizes, plan notes and latency predictions in this document are model outputs, not measurements. The docket (Section 6) requires a measured basis: sections 5-8 define the measurement protocol and templates; the predictions are the hypotheses it will test. Sizes use an uncompressed logical-bytes model and an assumed 0.6 compression factor; both are calibrated after the seed with `python docs/tools/perf_model.py --calibrate <factor>`.\n")
    A("Note on the docket: Section 6 refers to a 'method in Section 10' and Section 8 KPIs; the supplied docket has neither (it ends at Section 7). The measurement method (section 5) and the KPI set (analytics module notes K01..K20) were therefore defined by us.\n")
    A("## Contents\n\n1. Model facts and assumptions\n2. Index strategy per module\n3. Sharding, partitioning, zone configuration, follower reads\n4. Table-size estimates (laptop and full scale)\n5. The twelve fixed queries (B65) and the measurement method (B70)\n6. Hot-spot analysis\n7. Index justification per module with before/after template (B71-B73)\n8. Partitioning strategy for application and notification tables (B73)\n9. Full-scale extrapolation and the query that degrades first (B74)\n10. Node sizing, contention analysis, FK overhead\n11. Findings and recommendations to the data-model owner\n")
    # 1
    A("## 1. Model facts and assumptions\n")
    A(md_table(["item", "value"], [
        ["Dictionary", f"{ntab} tables ({len(LOOKUPS)} lookups), {nidx} declared secondary indexes, {nuq} unique constraints, {nfk} foreign keys, {len(M.m['views'])} views, 17 module files (12 modules + 5 EXTENSION modules)"],
        ["Primary keys", "UUID v4 (`gen_random_uuid()`) on every table: writes spread over ranges, no hot PK range at any volume (D-02)."],
        ["Time-ordered secondary indexes", f"{sum(HASH_COUNT.values())} hash-sharded indexes ({HASH_COUNT[8]} with 8, {HASH_COUNT[16]} with 16, {HASH_COUNT[32]} with 32 buckets)"],
        ["Cluster", "3 CockroachDB nodes in Docker on one host, replication factor 3 (every range has a replica on every node); each node 8 GiB RAM limit, 2 vCPU minimum, `--cache=.25 --max-sql-memory=.25`; licence applied after `cockroach init` (see crdb_capabilities.md section 3)."],
        ["Laptop dataset (docket)", "1,000,000 candidate profiles, 100,000 job requirements, 5,000,000 applications (fixed); other tables per seed plan (doc 12)."],
        ["Full scale (docket)", "10,000,000 profiles, 1,000,000 jobs, 100,000,000 applications (extrapolated, not loaded)."],
        ["Row model", "key 22 B + MVCC 12 B + value header 5 B + per column (type width + 1.5 B overhead); NULL columns cost 0 B; nullable columns assumed 60% populated. Index entry = prefix 6 B + key columns + PK 17 B + MVCC 12 B + header 5 B + STORING columns; partial indexes scaled by predicate selectivity; trigram/JSONB indexes by entries per row."],
        ["Replication", "Sizes are per replica; cluster disk = 3x on a 3-node RF3 cluster; assumed on-disk compression factor 0.6 (unverified until measured)."],
    ]))
    A(f"\nLogical size (one replica, data + indexes): laptop **{gb(ld + li):.0f} GiB** ({gb(ld):.0f} data + {gb(li):.0f} index); full scale **{gb(fd + fi):,.0f} GiB** ({gb(fd):,.0f} + {gb(fi):,.0f}). On the 3-node laptop cluster that is about **{gb(ld + li) * DISK_FACTOR * 3:.0f} GiB** of disk at the assumed 0.6 compression (docket asks for 512 GB free SSD). If measured usage exceeds 330 GiB, the seed plan's fallback (halve the non-docket event streams) applies.\n")
    # 2
    A("## 2. Index strategy per module\n")
    A("Rules applied by the model authors and checked by the lint: (1) every FK column leads some index (D-20); (2) time-ordered indexes on high-volume tables are hash-sharded (D-02); (3) 'live/open/unread' query shapes use partial indexes; (4) read paths that must avoid an index join use STORING; (5) natural and idempotency keys are unique constraints/partial unique indexes; (6) free-text search uses trigram GIN, JSONB filters use inverted indexes (ranking is delegated to the search service through `search_index_outbox`).\n")
    ms = defaultdict(Counter)
    for t in M.m["tables"]:
        ms[t["module"]]["tables"] += 1
        for ix in t["indexes"]:
            ms[t["module"]]["indexes"] += 1
            c = classify(t, ix)
            for key in ("hash-sharded", "partial", "unique", "trigram", "inverted (JSONB)", "covering"):
                if key in c:
                    ms[t["module"]][key] += 1
        ms[t["module"]]["uq"] += sum(1 for c in t["constraints"] if c["type"] == "unique")
    A(md_table(["module", "tables", "indexes", "+ unique constraints", "hash-sharded", "partial", "unique (partial)", "trigram", "inverted JSONB", "covering"],
               [[k, ms[k]["tables"], ms[k]["indexes"], ms[k]["uq"], ms[k]["hash-sharded"], ms[k]["partial"], ms[k]["unique"], ms[k]["trigram"], ms[k]["inverted (JSONB)"], ms[k]["covering"]] for k in MODULES]))
    A("\nFlagship indexes per module (real names; each is in the catalogue with estimated size):\n")
    FLAG = {
        "master_data": [("city", "idx_city_name_search", "trigram typeahead"), ("city", "idx_city_country_id_name_search", "prefix typeahead (partial is_active)"), ("skill", "idx_skill_canonical_name_lower", "skill typeahead (trigram)"), ("job_role", "idx_job_role_canonical_name_lower", "role typeahead (trigram)")],
        "profile": [("candidate_profile", "idx_candidate_profile_current_city_id_availability_status_id", "recruiter search entry (partial, covering)"), ("candidate_profile", "idx_candidate_profile_last_active_at", "hash-sharded activity time"), ("candidate_skill", "idx_candidate_skill_skill_id_years_of_experience", "skill-to-candidate search (partial, covering)"), ("candidate_profile_view", "idx_candidate_profile_view_candidate_profile_id_viewed_at", "who-viewed-me"), ("candidate_profile_view", "idx_candidate_profile_view_viewed_at", "hash-sharded 32 buckets, TTL scan"), ("candidate_activity_log", "idx_candidate_activity_log_activity_type_id_occurred_at", "activity feed (see hot-spot note)")],
        "jobs_applications": [("job_requirement", "idx_job_requirement_primary_city_id_published_at", "Q1 partial covering"), ("job_requirement", "idx_job_requirement_title_search_trgm", "Q2 trigram"), ("job_requirement", "idx_job_requirement_attributes", "JSONB filters"), ("job_application", "idx_job_application_job_requirement_id_status_applied", "Q3 pipeline board"), ("job_application", "idx_job_application_candidate_profile_id_applied_at", "Q4 history"), ("job_application", "uq_job_application_candidate_profile_id_job_requirement_id", "one live application"), ("job_application", "idx_job_application_applied_at_hash", "hash-sharded 32"), ("job_application_status_history", "idx_job_application_status_history_job_requirement_id_funnel", "Q11 funnel")],
        "administration": [("user_block", "uq_user_block_blocker_app_user_id_blocked_app_user_id_open", "open block (partial unique)"), ("user_block", "idx_user_block_blocked_app_user_id_blocker_app_user_id_open", "reverse lookup"), ("audit_event", "idx_audit_event_audit_entity_type_id_entity_ref_occurred_at", "entity history"), ("audit_event", "idx_audit_event_occurred_at", "hash-sharded 16"), ("app_user", "uq_app_user_email_live", "login by email (partial unique)"), ("identity_session", "uq_identity_session_refresh_token_hash", "refresh token lookup")],
        "organisation_account": [("organisation", "idx_organisation_legal_name_normalised", "name search (GIN)"), ("organisation", "uq_organisation_slug", "public URL"), ("recruiter_organisation_mapping", "idx_recruiter_organisation_mapping_organisation_id_recruiter_app_user_id", "open mappings (partial, covering)"), ("organisation_identifier", "uq_organisation_identifier_country_id_tax_id_type_id_value_lookup_hash", "tax id uniqueness via HMAC"), ("organisation_verification", "idx_organisation_verification_organisation_id_verification_kind_id_requested_at", "latest verification")],
        "gig_marketplace": [("gig_slot", "uq_gig_slot_gig_worker_profile_id_slot_start_at", "non-overlap slot table"), ("gig_request", "idx_gig_request_gig_request_status_id_window_start_at", "open gig feed (partial, covering)"), ("gig_assignment", "uq_gig_assignment_handoff_idempotency_key", "idempotent handoff"), ("gig_assignment", "idx_gig_assignment_gig_reconcile_state_id_status_reconciled_at", "reconciliation sweep (partial)"), ("gig_fulfilment_event", "idx_gig_fulfilment_event_received_at", "hash-sharded 16")],
        "licences_devices": [("device_session", "uq_device_session_device_slot_id", "device limit (partial unique)"), ("device_session", "uq_device_session_device_id", "one live session per device"), ("device_session", "idx_device_session_expires_at", "expiry sweeper (partial, hash-sharded)"), ("licence_seat", "uq_licence_seat_licence_id_app_user_id", "one live seat per user"), ("device", "uq_device_app_user_id_device_fingerprint_hash", "device identity")],
        "sourcing_consultant": [("candidate_contact_attempt", "idx_candidate_contact_attempt_candidate_profile_id_attempted_at", "contact frequency cap"), ("outreach_message", "idx_outreach_message_rendered_at", "hash-sharded TTL scan")],
        "subscriptions_payments": [("wallet", "uq_wallet_owner_organisation_id_currency", "one wallet per org and currency"), ("wallet_ledger_entry", "uq_wallet_ledger_entry_wallet_id_sequence_no", "gap-free sequence"), ("wallet_ledger_entry", "uq_wallet_ledger_entry_idempotency_key", "replay safety"), ("payment_order", "idx_payment_order_payment_order_status_id_expires_at", "expiry sweeper (partial)"), ("payment_event", "uq_payment_event_gateway_event_ref", "webhook dedupe"), ("subscription_period", "uq_subscription_period_licence_id_first", "chain head (partial unique)"), ("profile_download", "idx_profile_download_organisation_id_candidate_profile_id_downloaded_at", "download history / free repeat")],
        "notifications_community": [("notification", "idx_notification_recipient_app_user_id_created_at_unread", "Q10 unread (partial)"), ("notification_delivery", "uq_notification_delivery_idempotency_key", "dispatch idempotency"), ("notification_delivery", "idx_notification_delivery_dispatched_at", "hash-sharded 16"), ("provider_webhook_event", "uq_provider_webhook_event_notification_provider_id_provider_event_ref", "webhook dedupe"), ("job_alert_match", "uq_job_alert_match_job_alert_preference_id_job_requirement_id", "no duplicate alert"), ("job_alert_preference", "idx_job_alert_preference_keyword_list", "JSONB inverted")],
        "analytics": [("agg_funnel_daily", "idx_agg_funnel_daily_period_date", "hash-sharded 8"), ("fact_application_event", "idx_fact_application_event_event_at", "hash-sharded 32"), ("fact_application_event", "uq_fact_application_event_source_ref", "idempotent load"), ("fact_application_event", "idx_fact_application_event_dim_job_requirement_id_to_dim_application_status_id", "requirement funnel")],
        "tier3_reference": [("salary_benchmark", "idx_salary_benchmark_job_role_id_country_id_reference_period_start_date", "latest benchmark (partial, covering)"), ("company_rating_snapshot", "idx_company_rating_snapshot_organisation_id_snapshot_date", "latest rating (partial, covering)")],
    }
    rows = []
    for mod, lst in FLAG.items():
        for tb, nm, why in lst:
            ok = any(i["name"] == nm for i in T[tb]["indexes"]) or any(c["name"] == nm for c in T[tb]["constraints"])
            assert ok, (tb, nm)
            ix = next((i for i in T[tb]["indexes"] if i["name"] == nm), None)
            rows.append([mod, f"`{nm}`", tb, classify(T[tb], ix) if ix else "unique constraint", why])
    A(md_table(["module", "index", "table", "type", "role"], rows))
    A("\nExtension modules follow the same rules; their largest non-unique index per module (the one to justify first):\n")
    A(ext_just())
    # 3
    A("\n## 3. Sharding, partitioning, zone configuration, follower reads\n")
    A("**Principle.** CockroachDB shards every table and index into ranges (default maximum 512 MiB) and splits automatically; there is no need to partition for size. Partitioning is only needed for data locality (multi-region), tiered storage or mass-delete convenience. We therefore rely on (a) random UUID primary keys, (b) hash-sharded time indexes, (c) row-level TTL for expiring streams and (d) zone configs for GC and locality. Partitioning documentation in crdb_capabilities.md: partition columns must be leading PK columns and examples require an Enterprise/Trial licence.\n")
    A("| mechanism | where used | rule |\n|---|---|---|\n"
      f"| UUID v4 PK | all {ntab} tables | no sequential PK anywhere; `unique_rowid()` and SERIAL are not used |\n"
      f"| hash-sharded index | {sum(HASH_COUNT.values())} time-ordered indexes | 8 buckets for low-volume dims/aggregates, 16 default for events, 32 for application/history/view tables (up to 4x node count of an 8-node cluster; with 3 nodes more than 6-9 buckets only adds fan-out cost on range scans) |\n"
      "| row-level TTL | notification, notification_delivery, notification_delivery_event, provider_webhook_event, job_alert_*, feed_item, identity_session, device_session, login_attempt, candidate_job_search_history, job_view_event, gig_slot, gig_fulfilment_event, payment_event, audit_event (400 d, after sink confirmation) | `ttl_expiration_expression` on an `expires_at` column; `ttl_job_cron='@daily'`; keep only the indexes the read path needs (secondary indexes slow the TTL job); no inbound FKs from live tables onto TTL tables |\n"
      "| zone config | see below | applied after seed, before measurement |\n"
      "| follower reads | analytics and public listing reads | see below |\n")
    A("Zone configuration (names follow Rule 8: `<table>_zone`; statements are DDL to run on the cluster, not part of the dictionary):\n")
    A("```sql\n"
      "-- Expiring high-churn streams: shorter MVCC garbage window (TTL tombstones are removed sooner). Default gc.ttlseconds is 14400 (4 h).\n"
      "ALTER TABLE notification_delivery        CONFIGURE ZONE USING gc.ttlseconds = 3600;   -- notification_delivery_zone\n"
      "ALTER TABLE notification_delivery_event  CONFIGURE ZONE USING gc.ttlseconds = 3600;\n"
      "ALTER TABLE provider_webhook_event       CONFIGURE ZONE USING gc.ttlseconds = 3600;\n"
      "ALTER TABLE notification                 CONFIGURE ZONE USING gc.ttlseconds = 7200;\n"
      "ALTER TABLE job_view_event               CONFIGURE ZONE USING gc.ttlseconds = 3600;\n"
      "-- Keep the BACKUP/PITR requirement in mind: gc.ttlseconds below the incremental backup interval breaks AS OF SYSTEM TIME backups; schedule incrementals hourly.\n"
      "-- Large append-mostly tables: bigger ranges mean fewer ranges to track (default range_max_bytes = 512 MiB).\n"
      "ALTER TABLE job_application_status_history CONFIGURE ZONE USING range_max_bytes = 1073741824, range_min_bytes = 268435456;\n"
      "-- Small, constantly read reference tables: no change (cached in every node).\n"
      "-- Hot-row config tables (wallet, invoice_number_series, audit_chain_head): keep default 3 replicas; do not co-locate with scans.\n"
      "```\n")
    A("**Multi-region option (not used on the laptop).** `job_application` and `job_requirement` can become `REGIONAL BY ROW` on a country-derived `crdb_region` for data residency: it changes the primary key to `(crdb_region, id)`, and hash-sharded indexes cannot be combined with an explicit PARTITION BY key, so the two time indexes become plain per-region indexes. Not applied; recorded in section 8.\n")
    A("**Follower reads** (`AS OF SYSTEM TIME follower_read_timestamp()`, ~4.8 s staleness; read-only; verified GA in crdb_capabilities.md row 15c):\n")
    A(md_table(["query / path", "follower read?", "reason"], [
        ["Q1 job search, Q2 title search", "yes", "listing freshness of a few seconds is acceptable; offloads leaseholders"],
        ["Q3 pipeline board (recruiter)", "no", "recruiters must see a status change they just made"],
        ["Q4 candidate history", "no (primary)", "read-your-writes after applying"],
        ["Q10 unread notifications", "no", "marking read must not reappear"],
        ["Q11 funnel, Q12 KPI trend, all `mv_*`/`agg_*` reads", "yes", "analytics ETL itself reads `AS OF SYSTEM TIME follower_read_timestamp()` (analytics notes)"],
        ["salary_benchmark / company_rating_snapshot reads", "yes", "reference data refreshed daily"],
        ["Q5 recruiter candidate search", "yes, bounded (`with_max_staleness('10s')` cannot be used: multi-row and joins)", "use `follower_read_timestamp()` and accept ~5 s staleness of block/privacy flags: NOT acceptable for the block check, so Q5 runs on the leaseholder"],
        ["Q6-Q9 transactions", "no", "writes; locks"],
    ]))
    A("\nOn a single-host 3-node Docker cluster a follower read does not reduce network latency (all nodes are local); the benefit measured on the laptop is leaseholder CPU offload only. Latency benefit needs `--locality` regions (crdb_capabilities.md row 14).\n")
    # 4
    A("## 4. Table-size estimates (laptop and full scale)\n")
    A(f"Row counts: explicit anchors for {len(pm.ANCHORS)} tables (docket-fixed 1M / 100k / 5M plus derived volumes), long-tail rule for the rest (doc 12 section 3 is the authority): lookups = their value count; per volume class low 1,000 / medium 20,000 / high 150,000 / very_high 500,000 rows; analytics tail 1,000 / 20,000 / 100,000 / 500,000; extension modules 5% of those; full scale = laptop x driver factor (profiles 10, jobs 10, applications 20, tail 10).\n")
    rows = []
    for n in KEY_TABLES:
        s = ST[n]
        ldat, lidx = tsize(n, "laptop")
        fdat, fidx = tsize(n, "full")
        rows.append([f"`{n}`", s["module"], fmt_rows(s["laptop_rows"]), f"{s['row_bytes']:.0f}", f"{s['idx_bytes_row']:.0f}", len(s["indexes"]), f"{gb(ldat):.2f}", f"{gb(lidx):.2f}", fmt_rows(s["full_rows"]), f"{gb(fdat):.1f}", f"{gb(fidx):.1f}", f"{gb(fdat + fidx):.1f}"])
    A(md_table(["table", "module", "rows laptop", "row B", "index B/row", "# idx+uq", "laptop data GiB", "laptop idx GiB", "rows full", "full data GiB", "full idx GiB", "full total GiB"], rows))
    A("\nModule totals (GiB, one replica):\n")
    mrows = []
    for k, n, a, b, c, d in module_totals():
        mrows.append([k, n, f"{gb(a):.1f}", f"{gb(b):.1f}", f"{gb(a + b):.1f}", f"{gb(c):.1f}", f"{gb(d):.1f}", f"{gb(c + d):,.1f}"])
    A(md_table(["module", "tables", "laptop data", "laptop index", "laptop total", "full data", "full index", "full total"], mrows))
    A("\nIndex overhead is 0.9-1.0x the data on the three biggest OLTP tables because of 7-8 secondary indexes on `job_application`; the per-index breakdown for any table is in the catalogue sheet `Index catalogue` (column 'est. size').\n")
    # ranges
    rows = []
    for n in ["job_application", "job_application_status_history", "candidate_profile", "candidate_skill", "notification_delivery", "wallet_ledger_entry", "audit_event"]:
        s = ST[n]
        for scale in ("laptop", "full"):
            r = s["laptop_rows"] if scale == "laptop" else s["full_rows"]
            tot = r * (s["row_bytes"] + s["idx_bytes_row"])
            rows.append([f"`{n}`", scale, f"{gb(tot):.1f}", math.ceil(tot / (512 * 1024 ** 2 * 0.6)), math.ceil(r * s["row_bytes"] / (512 * 1024 ** 2 * 0.6))])
    A("Range counts (ranges split at 512 MiB; modelled average fill 60%):\n")
    A(md_table(["table", "scale", "GiB (data+idx)", "ranges (all indexes)", "ranges (primary index only)"], rows))
    # 5
    A("\n## 5. The twelve fixed queries (B65) and the measurement method (B70)\n")
    A("All statements were parsed and column-checked against the dictionary by `python docs/tools/fixed_queries.py` (sqlglot, PostgreSQL dialect; INSERTs are also checked for NOT NULL columns without default; `created_by`/`updated_by` are omitted from the INSERT text for brevity and are set by the application per D-04). `$n` are bind parameters. The plan notes are **predictions** to be confirmed or refuted with `EXPLAIN ANALYZE`. The ten flagged queries are the 'top ten' of B65/B70.\n")
    A(md_table(["id", "kind", "title", "EXPLAIN (top ten)", "target p95 ms (laptop)"], [[q["id"], q["kind"], q["title"], "yes" if q["explain"] else "no (captured, not in baseline)", q["target_p95_ms"]] for q in fq.Q]))
    for qq in fq.Q:
        A(f"\n### {qq['id']} - {qq['title']} ({qq['kind']}{', EXPLAIN' if qq['explain'] else ''})\n")
        A(f"Parameters: {qq['params']}. Index(es): " + ", ".join(f"`{i}`" for i in qq["indexes"]) + ".\n")
        for s in qq["sql"]:
            A("```sql\n" + s + ";\n```")
        A(f"\nExpected plan: {qq['plan']}\n")
    A("\n### 5.1 Measurement method (B70)\n")
    A(md_table(["item", "protocol"], [
        ["Hardware (record in the report)", "Host: >= 8 cores, 32 GB RAM, NVMe SSD with >= 512 GB free; record CPU model, RAM, SSD model, OS, Docker version, Docker CPU/RAM limits. Cluster: 3 x `cockroachdb/cockroach:v26.2.<LTS patch>` containers, each limited to 2 vCPU and 8 GiB (`--cache=.25 --max-sql-memory=.25`), one named volume per node, bridge network, licence applied, `kv.rangefeed.enabled=false`, no changefeeds, no other workloads. Load generator on the same host with CPU pinned away from the nodes."],
        ["Dataset", "Seeded per doc 12 at laptop scale (1M profiles, 100k jobs, 5M applications; row counts verified by `SELECT count(*)` against the doc 12 targets); statistics refreshed (`CREATE STATISTICS` or wait for auto stats; `SHOW STATISTICS FOR TABLE job_application` captured); `ANALYZE`-equivalent done after the last load; no schema change running; `SHOW JOBS` clean."],
        ["Query text", "Exactly the SQL in this section, parameters drawn from a fixed random stream (seed 20261015) with skew: city by Zipf(1.0) over cities, skill by Zipf(1.0) over skills, requirement by the application distribution (doc 12 section 4) so that both typical and heavy requirements are exercised; the same parameter sequence is replayed for before/after runs."],
        ["Warm-up", "Cold run (restart the 3 containers, `SET CLUSTER SETTING` unchanged) for 1 pass of 200 executions recorded separately as 'cold'; then 2,000 untimed warm-up executions per query."],
        ["Runs", "Each query: 5 independent runs x 10,000 timed executions single-client (latency), then 4/16/64 concurrent clients for 120 s each (throughput and tail). Transactional queries Q6-Q9 use a retry loop on SQLSTATE 40001; retries are counted and reported, latency is end-to-end including retries."],
        ["Statistics reported", "p50, p95, p99, max, mean, stddev of latency in ms per run and pooled (HDR histogram, 1 microsecond resolution; coordinated-omission-safe open-loop mode for the concurrency tests); throughput; error and retry counts; rows returned. Report the median of the 5 run p95 values with min/max; reject a run when p99/p50 > 50 and re-run once."],
        ["EXPLAIN ANALYZE capture", "For each flagged query: `EXPLAIN ANALYZE (VERBOSE)` text and `EXPLAIN ANALYZE (DISTSQL)` URL after warm-up, 3 samples (typical, heavy, empty parameter), stored in `perf/b70/<qid>/`. Record plan hash, `FULL SCAN` presence, rows read, KV bytes read, KV round trips, contention time, `SHOW RANGES FROM TABLE ... WITH DETAILS` for the tables touched."],
        ["Harness design", "Python (psycopg 3 pool) or Go (pgx) driver reading a parameter file, one process per client, `statement_timeout=5s`, `application_name='b70'`; per-execution timestamps written to Parquet; a controller script runs the matrix (query x clients x run) and writes `perf/b70/results.csv` with columns `query_id, run, clients, n, p50_ms, p95_ms, p99_ms, max_ms, qps, retries, errors, rows_read_avg`. `crdb_internal.node_statement_statistics` is sampled before/after as a cross-check of server-side latency."],
        ["Acceptance", "Pass = p95 <= target in the table above on the warm laptop run; any miss gets a recorded cause and either an index change (B71-B73) or a justified waiver."],
        ["Calibration after seed", "Compute actual bytes/row from `SHOW RANGES FROM TABLE t WITH DETAILS` for 10 tables, derive the correction factor, re-run `perf_model.py --calibrate` and refresh sections 4, 9, 10."],
    ]))
    # 6
    A("\n## 6. Hot-spot analysis\n")
    A(f"* **UUID primary keys**: all {ntab} PKs are random v4; inserts are uniformly distributed over the table's ranges. Cost: no insert locality (each insert touches a random leaf), and secondary-index entries carry the 16-byte UUID (17 B encoded). `gen_random_uuid()` skips uniqueness checks by default, which is safe for v4 (collision probability negligible) and avoids one read per insert.\n"
      f"* **Hash-sharded time indexes**: {sum(HASH_COUNT.values())} indexes ({HASH_COUNT[8]}/{HASH_COUNT[16]}/{HASH_COUNT[32]} at 8/16/32 buckets). A plain index on `created_at` of an append table concentrates every insert on the last range (single leaseholder, ~one core); a hash-sharded index turns that into N independent write streams. Price: range scans on the index fan out over all N buckets (reads of a time window = N spans), so per-entity reads lead with the entity id instead (e.g. `(candidate_profile_id, viewed_at DESC)`), and the hash-sharded index is used for TTL/time-window jobs only.\n"
      "* **Bucket count**: more buckets than nodes gives diminishing returns (crdb_capabilities.md row 2). With 3 nodes the useful ceiling is ~9; 16/32 are kept so that the dictionary does not need to change when the cluster grows to 8 nodes (32 = 4 x 8). Measured trade-off to record in B73: insert throughput vs 16/32-bucket scan latency for `idx_job_application_applied_at_hash`.\n")
    A(f"* **Remaining sequential-key candidates** found by scanning all {nidx} indexes of high/very_high tables ({len(HOT_TIME)} leading-timestamp indexes without hashing, {len(HOT_ENUM)} leading low-cardinality lookup + timestamp):\n")
    A(md_table(["pattern", "table", "index", "volume", "partial predicate"], [["timestamp-led"] + [h[1], h[2], h[3], h[4]] for h in HOT_TIME] + [["enum+timestamp"] + [h[1], h[2], h[3], h[4]] for h in HOT_ENUM]))
    A("\nAssessment: the timestamp-led partial indexes (`*_due`, `expires_at_due`, `remind_at_due`) index a small due-set and are acceptable. **Needs action:** `idx_app_user_last_login_at` (high volume, WHERE deleted_at IS NULL AND anonymised_at IS NULL): every login rewrites the entry to the head of the index, a continuously hot range at login rate; make it hash-sharded or drop it in favour of the existing activity data. `idx_candidate_activity_log_activity_type_id_occurred_at` (very_high), `idx_user_badge_badge_type_id_awarded_at`, `idx_proctoring_event_proctoring_severity_id_occurred_at`, `idx_candidate_media_file_scan_status_id_created_at`: a handful of leading enum values with a rising timestamp concentrate writes on a few key prefixes (each prefix is one contiguous span, split into at most a few ranges); acceptable below ~1,000 writes/s per value, otherwise leading with a hash of the entity is required. Work-queue indexes such as `idx_payment_order_payment_order_status_id_expires_at` stay small because rows leave the partial predicate (paid_at IS NULL) quickly.\n")
    # 7
    A("## 7. Index justification per module with before/after template (B71-B73)\n")
    A("One index per module, the one whose absence changes a plan from scan to point/range access. 'Before' = index made invisible (`ALTER INDEX ... NOT VISIBLE`, to be verified on the v26.2 image; otherwise DROP and re-CREATE) on the same dataset; 'After' = index visible. The query text is run through the B70 harness (single client, 5 x 10,000 executions, same parameter stream); `EXPLAIN ANALYZE` captured for both. The analytics entry proposes a new index (not in the dictionary): record it as change request R-01 in section 11.\n")
    A(just_section())
    A("\n#### 7.13 Extension modules\n\nUse the same template; candidates by size (section 2 table). Priority order if time is short: `ext_matching` (`search_index_outbox`), `ext_ads` (`ad_budget_shard` write path), others optional.\n")
    # 8
    A("## 8. Partitioning strategy for application and notification tables (B73)\n")
    A("**Decision: no declarative partitioning at laptop scale or at full scale in a single region; physical distribution relies on UUID PKs, hash-sharded indexes, TTL and range splitting.** Reasons (all from the dictionary and crdb_capabilities.md): (1) partition columns must lead the primary key, but the PK is `id UUID` by D-02 and changing it to a time-led key would create exactly the hot spot we avoid; (2) a table with an explicit `PARTITION BY` cannot use hash-sharded indexes on the same key columns; (3) partitioning docs require a licence; (4) dropping a partition is not supported (cannot drop a single partition), so partitions do not give cheap archival.\n")
    A(md_table(["table group", "volume (laptop -> full rows)", "strategy", "retention mechanism", "named objects"], [
        ["`job_application`", f"{fmt_rows(ST['job_application']['laptop_rows'])} -> {fmt_rows(ST['job_application']['full_rows'])}", "Single table, random UUID PK. Secondary indexes lead with entity ids (requirement, candidate, organisation) so reads are range-local; the two time indexes are hash-sharded (32). At 100M rows (about 108 GiB incl. indexes) the table is ~360 ranges (data + indexes): no partitioning needed for size.", "Soft delete + anonymise-in-place on erasure; no TTL (business record). Archive of closed requirements > 7 years by changefeed to cloud storage, then delete in bounded batches.", "`idx_job_application_applied_at_hash`, `idx_job_application_status_changed_at_hash`"],
        ["`job_application_status_history`", f"{fmt_rows(ST['job_application_status_history']['laptop_rows'])} -> {fmt_rows(ST['job_application_status_history']['full_rows'])}", "Single table; hash-sharded created_at (32); funnel index leads with requirement id. Larger ranges (1 GiB) to reduce range count.", "Append-only; export closed requirements older than 24 months and prune in batches (retention job).", "`job_application_status_history_zone`"],
        ["`notification`", f"{fmt_rows(ST['notification']['laptop_rows'])} -> {fmt_rows(ST['notification']['full_rows'])}", "Single table; per-user index leads with the random recipient id; hash-sharded created_at/expires_at (16).", "Row-level TTL 180 days on `expires_at` (daily job, batch 500). This is time-based expiry without partition management.", "`notification_zone` (gc.ttlseconds 7200)"],
        ["`notification_delivery`, `_event`, `provider_webhook_event`", f"{fmt_rows(ST['notification_delivery']['laptop_rows'])} -> {fmt_rows(ST['notification_delivery']['full_rows'])} (deliveries)", "Event-sourced insert-only path (B57): batches of 500-1000 rows per txn, `ON CONFLICT DO NOTHING`; no updates; hash-sharded time indexes (16); TTL 30 d / 30 d / 7 d.", "TTL; before expiry the daily rollup writes `notification_delivery_daily_summary` (7 years).", "`notification_delivery_zone` (gc.ttlseconds 3600)"],
        ["design-rate stress case", "50M deliveries/day x 30 days = 1.5 billion live rows (notifications notes)", "Same design, but the 30-day TTL window would hold about 1.5e9 x 715 B = ~1.0 TiB per replica; at that rate move delivery rows to a 7-day window (about 250 GiB) and keep the 7-year daily summary, or shard by `crdb_region`. Decision threshold: if measured TTL job throughput (rows/s deleted) < ingest rate x 1.2, shorten the window.", "TTL delete rate is the limiting factor: tune `ttl_select_batch_size`, `ttl_delete_batch_size` (500), `ttl_delete_rate_limit`.", "-"],
    ]))
    A("\n**Optional multi-region / residency variant** (documented, not built): `ALTER DATABASE ... PRIMARY REGION`, `ALTER TABLE job_application SET LOCALITY REGIONAL BY ROW`; the implicit `crdb_region` column is derived from the requirement's country; partition names `job_application_p_<region>`; hash-sharded time indexes become per-region plain indexes (volume is split per region anyway). Requires >= 3 regions for `SURVIVE REGION FAILURE` and a licence; a single Docker host can emulate regions with distinct `--locality` flags for functional tests only.\n")
    A("**Before/after for B73** (template, to fill): insert throughput of `job_application` with (a) hash-sharded applied_at 32 buckets, (b) 8 buckets, (c) plain index; p95 of Q3 and of a 'last 24 h applications' scan in each case; TTL job duration and rows/s for `notification_delivery` at 30 days of seeded rows.\n")
    # 9
    A("## 9. Full-scale extrapolation (B74)\n")
    A("**Method.** Measure at laptop scale (B70), then project with an explicit model whose parameters are re-fitted from the measurements: (1) rows touched per query as a function of the scale drivers (profiles P, jobs J, applications A: x10, x10, x20); (2) index depth growth `depth = 1 + ceil(log_F(N / leaf entries))` with F the internal fan-out (32 KiB block / (key bytes + 16)); (3) cache hit ratio under Zipf(s=1) access: `h = ln(1 + C) / ln(1 + N)` where C and N are the cache and the query working set in 4 KiB blocks; (4) range count = bytes / (512 MiB x 0.6); (5) plan changes (hash join vs lookup join, sort spill) when estimated rows cross thresholds. Latency = fixed + scan rows x c_scan + hash rows x c_hash + sorted rows x c_sort + lookups x (c_lookup + miss x c_miss + extra levels x c_level).\n")
    # working set
    ws_idx = [("job_requirement", "idx_job_requirement_primary_city_id_published_at"), ("job_requirement", "idx_job_requirement_title_search_trgm"), ("job_application", "idx_job_application_job_requirement_id_status_applied"),
              ("job_application", "idx_job_application_candidate_profile_id_applied_at"), ("candidate_profile", "idx_candidate_profile_current_city_id_availability_status_id"),
              ("candidate_skill", "idx_candidate_skill_skill_id_years_of_experience"), ("job_application_status_history", "idx_job_application_status_history_job_requirement_id_funnel"),
              ("notification", "idx_notification_recipient_app_user_id_created_at_unread")]
    def ixsize(tn, nm, scale):
        s = ST[tn]
        rows = s["laptop_rows"] if scale == "laptop" else s["full_rows"]
        per = next(c for a, b, c in s["indexes"] if a == nm)
        return rows * per
    ws_lap = sum(ixsize(a, b, "laptop") for a, b in ws_idx) + 0.10 * sum(ST[n]["laptop_rows"] * ST[n]["row_bytes"] for n in ("job_application", "candidate_profile", "job_requirement"))
    ws_full = sum(ixsize(a, b, "full") for a, b in ws_idx) + 0.10 * sum(ST[n]["full_rows"] * ST[n]["row_bytes"] for n in ("job_application", "candidate_profile", "job_requirement"))
    rows = []
    depth_rows = []
    for a, b in ws_idx:
        s = ST[a]
        per = next(c for x, y, c in s["indexes"] if x == b)
        ix = next((i for i in T[a]["indexes"] if i["name"] == b), None)
        kb = 40
        dl = px.btree_depth(s["laptop_rows"], max(int(per), 20), kb)
        df = px.btree_depth(s["full_rows"], max(int(per), 20), kb)
        depth_rows.append([f"`{b}`", a, fmt_rows(s["laptop_rows"]), fmt_rows(s["full_rows"]), f"{per:.0f}", dl, df, f"{gb(ixsize(a, b, 'laptop')):.2f}", f"{gb(ixsize(a, b, 'full')):.2f}"])
    A("Index depth and size of the indexes relied on by the fixed queries (32 KiB blocks):\n")
    A(md_table(["index", "table", "entries laptop", "entries full", "bytes/row", "depth laptop", "depth full", "GiB laptop", "GiB full"], depth_rows))
    dmax_l = max(r[5] for r in depth_rows)
    dmax_f = max(r[6] for r in depth_rows)
    ml, mf = px.miss_ratio(ws_lap), px.miss_ratio(ws_full)
    nodebytes_l = (totals("laptop")[0] + totals("laptop")[1]) * DISK_FACTOR
    nodebytes_f = (totals("full")[0] + totals("full")[1]) * DISK_FACTOR
    lsm = lambda b: 1 + math.ceil(math.log10(max(b / (256 * 1024 ** 2), 1)))
    A(f"\nBecause leaf blocks hold ~{32768 // 150} entries and fan-out is >200, depth grows by at most one level (laptop {dmax_l} to full {dmax_f}); the extra level costs ~{px.PARAMS['c_level']} ms per lookup, which is negligible next to the cache effect. LSM levels per node: laptop {lsm(nodebytes_l)} (about {nodebytes_l / GB:.0f} GiB on disk per node) to full {lsm(nodebytes_f)} ({nodebytes_f / GB:,.0f} GiB per node); bloom filters keep point-read amplification near 1 for hits.\n")
    A(f"Cache model: query working set = the {len(ws_idx)} indexes above + 10% of the three hot primary tables: **{gb(ws_lap):.1f} GiB** laptop and **{gb(ws_full):.1f} GiB** full scale; total block cache {px.PARAMS['cache_bytes_total'] / GB:.0f} GiB (3 x 2 GiB). Predicted miss ratio: **{ml * 100:.1f}%** laptop, **{mf * 100:.1f}%** full scale ({mf / ml:.1f}x more cache misses per lookup).\n")
    res = px.run(ws_lap, ws_full, dmax_l, dmax_f)
    rows = []
    for q, desc, slo, l1, l10, ratio, br in res:
        rows.append([q, desc, slo, f"{l1:.1f}", f"{l10:.1f}", f"{ratio:.1f}x", ("already above SLO at laptop scale" if br == 1.0 and l1 >= slo else (f"{br:.1f}x (~{br * 1.0:.1f}M profiles / {br * 100:.0f}k jobs / {br * 10:.0f}M applications)" if br else "does not breach up to full scale"))])
    A("Predicted latency (ms, single client, warm, mean of the model; NOT a measurement) and the scale at which each query first exceeds its SLO (lam = position on the path laptop 1 -> full 10; profiles 1M x lam, jobs 100k x lam, applications 5M x 2 lam):\n")
    A(md_table(["query", "rows model (laptop)", "SLO ms", "laptop ms", "full ms", "growth", "first breach"], rows))
    A("\nParameters (re-fit them from the B70 baseline; one run of `gen_perf_plan.py` regenerates this table): " + ", ".join(f"{k}={v}" for k, v in px.PARAMS.items() if k != 'cache_bytes_total') + ".\n")
    A("### Prediction: which query degrades first and why\n")
    q5 = next(r for r in res if r[0] == "Q5")
    q2 = next(r for r in res if r[0] == "Q2")
    q12 = next(r for r in res if r[0].startswith("Q12 (existing"))
    q11 = next(r for r in res if r[0].startswith("Q11 (largest"))
    A(f"1. **Q5, recruiter candidate search (city + skill + experience, block/privacy anti-joins, ORDER BY last_active_at).** It is the only fixed query whose work is proportional to the number of profiles in a city and with a skill (sets of ~{26000:,} index entries at 1M profiles, ~260,000 at 10M) and whose sort key `last_active_at` is in none of the indexes used, so the engine must read both candidate sets and top-k sort every match; there is no LIMIT pushdown. Predicted {q5[3]:.0f} ms to {q5[4]:,.0f} ms (x{q5[5]:.0f}); it crosses its 250 ms SLO at about {q5[6]:.1f}M profiles, i.e. soon after the laptop scale. The plan can also change: when the estimated skill set exceeds the city set the optimizer may switch from lookup join to hash join with higher memory (`distsql` temp storage spill beyond the SQL memory budget) - watch for `spilled to disk` in EXPLAIN ANALYZE. Mitigation if confirmed: add `last_active_at DESC` as the second key of a per-city partial index (`(current_city_id, last_active_at DESC) STORING (...) WHERE deleted_at IS NULL AND is_searchable`) so that the city side streams in order with LIMIT pushdown, and drive skill filtering by lookup; or delegate ranking to the search service (D-16) and keep the SQL for filters only.\n"
      f"2. **Q2, trigram title search** second: posting-list size and the number of index-joined rows grow linearly with jobs (x10); predicted {q2[3]:.0f} ms to {q2[4]:.0f} ms, breach at ~{q2[6]:.1f}x laptop jobs ({q2[6] * 100:.0f}k). The inverted index is not partial (`is_live`), so closed jobs add to the posting list and are filtered after the join.\n"
      f"3. **Q11 on the largest requirement (p99 tail, not the average)**: average cost only doubles (applications per requirement 50 to 100) but the largest requirement's history grows x20; predicted {q11[3]:.0f} ms to {q11[4]:,.0f} ms, breach at ~{q11[6]:.1f}x. The docket jobs notes expected the pipeline board for mega-requirements (Q3) to degrade first; Q3 is protected by LIMIT 50 and the covering index, while Q11's aggregation has no limit, so Q11's tail degrades earlier. Mitigation: read the pre-aggregated `job_requirement_stat` / `fact_application_pipeline` for large requirements.\n"
      f"4. **Q12 on the existing schema** already exceeds its SLO at laptop scale ({q12[3]:.0f} ms predicted vs 120 ms) because the only date index is hash-sharded and not covering; it is a present defect, fixed by the proposed index (section 7.11), after which it is flat.\n"
      "5. Point/lookup and transactional queries (Q1, Q3, Q4, Q6-Q10) are predicted to grow by less than 2x: they are bounded by LIMIT or key lookups; their growth is cache misses and one more index level. The exception under concurrency is Q8 (wallet row lock), see section 10.\n")
    A("**How the prediction will be tested (falsifiable).** At laptop scale run Q5 and Q2 at 25%, 50%, 100% of the seeded profiles/jobs (delete-free subsetting by `id` hash, or three loads): the measured p95 vs rows must be linear with the fitted slope; extrapolate to 10x; the prediction is wrong if Q5's slope is below Q2's or Q11-max's. Optional bonus run (docket): one query set on a cloud cluster at 10M profiles to compare.\n")
    # 10
    A("## 10. Node sizing, contention analysis, FK overhead\n")
    A("### 10.1 Node sizing for the 3-node Docker cluster at 100M-row tables\n")
    sub = ["job_application", "job_application_status_history", "candidate_profile", "candidate_skill", "job_requirement", "job_requirement_skill", "questionnaire_answer", "job_application_screening_result"]
    sub_full = sum(ST[n]["full_rows"] * (ST[n]["row_bytes"] + ST[n]["idx_bytes_row"]) for n in sub)
    sub_lap = sum(ST[n]["laptop_rows"] * (ST[n]["row_bytes"] + ST[n]["idx_bytes_row"]) for n in sub)
    A(f"* **100M-application subset** (tables {', '.join('`' + n + '`' for n in sub)}): {gb(sub_lap):.0f} GiB laptop -> **{gb(sub_full):.0f} GiB** per replica at full scale ({gb(sub_full) * DISK_FACTOR:.0f} GiB on disk after compression). With RF3 on 3 nodes every node stores the whole subset.\n"
      f"* **Whole database at full scale**: {gb(fd + fi):,.0f} GiB logical = {gb(fd + fi) * DISK_FACTOR / 1024:.1f} TiB on disk per node (RF3, 3 nodes): not feasible on a laptop; this is why only the subset (or one query set) is run in the cloud (bonus).\n")
    A(md_table(["resource", "laptop run (1M/100k/5M)", "100M-application subset run", "reasoning"], [
        ["per-node disk (data volume)", f"{gb(ld + li) * DISK_FACTOR:.0f} GiB used, provision >= {gb(ld + li) * DISK_FACTOR * 1.5:.0f} GiB", f"{gb(sub_full) * DISK_FACTOR:.0f} GiB used, provision >= {gb(sub_full) * DISK_FACTOR * 2.2:.0f} GiB", "schema changes and index backfills need up to 3x the range size free (online-schema-changes); compaction headroom 30%; NVMe/SSD only"],
        ["per-node RAM", "8 GiB container (cache 2 GiB, SQL 2 GiB, rest Go heap + OS)", "16-32 GiB container (cache 4-8 GiB)", f"cache must cover the hot index set ({gb(ws_full):.0f} GiB full vs {px.PARAMS['cache_bytes_total'] / GB:.0f} GiB total today): at 3 x 8 GiB cache the predicted miss ratio is {px.miss_ratio(ws_full, dict(px.PARAMS, cache_bytes_total=3 * 8 * GB)) * 100:.0f}%"],
        ["per-node CPU", "2-3 vCPU", "4-8 vCPU", "8-core laptop shared by 3 nodes + load generator: leave >= 2 cores to the client; hash-sharded fan-out and trigram scans are CPU-bound"],
        ["ranges", f"~{math.ceil(gb(ld + li) * GB / (512 * 1024 ** 2 * .6)):,} ranges in the database", f"~{math.ceil(sub_full / (512 * 1024 ** 2 * .6)):,} ranges in the subset", "each range = a Raft group; keep < 100k ranges/cluster on this hardware; 512 MiB split size"],
        ["file descriptors / memory", "ulimit -n >= 65535", "same", "LSM files per node grow with data"],
        ["network", "docker bridge (local)", "same", "RF3 replication traffic stays on the host; no WAN latency (use `--locality` + tc netem to emulate 5-60 ms for multi-region tests)"],
    ]))
    A("\n### 10.2 Contention analysis (single-row serialisation points)\n")
    A("Model: a transaction holding a row lock for S ms gives a ceiling of 1000/S transactions per second per row; with Poisson arrivals at rate lambda the mean queue wait is `Wq = rho S / (2 (1 - rho))`, rho = lambda S / 1000 (M/D/1). S is estimated as (statements - 1) x 1.5 ms round trips + 6 ms commit (one consensus round + parallel commit) and must be **measured** in B70 (statement trace of Q8/Q9).\n")
    def md1(lam, S):
        rho = lam * S / 1000
        return "saturated" if rho >= 1 else f"{rho * S / (2 * (1 - rho)):.1f} ms (rho {rho:.2f})"
    rows = []
    cont = [
        ("wallet (Q8 debit/credit)", "`wallet` row: `SELECT ... FOR UPDATE` then `UPDATE`; 4 statements", 4 * 1.5 + 6, [5, 20, 60], "one writer per wallet by design (BR-SUB-08); organisation wallets shared by many recruiters: 60 downloads/s on ONE wallet is unrealistic (credits are bought in packs), but bulk outreach (`outreach_credit_usage`) can burst: batch the debit (one ledger row per batch) instead of per recipient"),
        ("device slot / licence seat (Q9 login)", "`licence_seat` row per user; slots per seat", 4 * 1.5 + 6, [0.2, 1, 3], "locks are per user, so users never contend with each other; same user's concurrent logins queue; ceiling 1000/12 = 83 logins/s PER USER, far above any real rate"),
        ("invoice series", "`invoice_number_series` row per (seller_country_id, series_document_kind_id, prefix, fiscal_year_label), `UPDATE next_number+1` in the issuing txn (gap-free)", 3 * 1.5 + 6, [5, 20, 80], "ONE row per series is global to the country+year: month-end invoice runs (300/s) saturate it. Mitigations: issue invoices from one worker in batches, assign N numbers in one UPDATE (`next_number = next_number + N`), or split the series by issuing entity (prefix); numbering stays gap-free because the number is assigned in the issuing txn"),
        ("ad budget pacing", "`ad_budget_shard` (16 random shards per campaign) + hourly accrual into `ad_campaign.spent_amount/ledger_sequence`", 2 * 1.5 + 6, [200, 1000, 5000], "click-time charges pick a random shard: per-shard rate = total/16; 16 shards x 111/s = ~1,800 charges/s per campaign; flush job locks the campaign row once per hour. Hot campaigns above that need 64 shards (CHECK shard_no BETWEEN 0 AND 15 must be widened)"),
        ("audit chain heads", "`audit_chain_head` row per chain (32 chains), FOR UPDATE per audit write", 3 * 1.5 + 6, [20, 100, 400], "32 chains x 95/s = ~3,000 audit events/s ceiling; the audit write is in the business txn, so a slow audit chain slows the business action: monitor chain queueing"),
        ("job counters", "`job_requirement_stat` row per requirement (view/apply counters)", 2 * 1.5 + 6, [1, 10, 50], "viral job at 50 applications/s would saturate one row; counters are updated asynchronously in batches by a worker (BR), never in the apply txn (Q6 does not touch it)"),
    ]
    for name, mech, S, lams, note in cont:
        rows.append([name, mech, f"{S:.1f}", f"{1000 / S:.0f}/s"] + [f"{l}/s: {md1(l, S)}" for l in lams] + [note])
    A(md_table(["serialisation point", "mechanism", "S ms", "ceiling", "load A", "load B", "load C", "analysis and mitigation"], rows))
    A("\nUnder SERIALIZABLE, `FOR UPDATE` locks are best-effort (lost on lease transfer/range split) so a 40001 retry is possible even when the lock queue is short; the billing role can set `enable_durable_locking_for_serializable = true` (crdb_capabilities.md row 16b). B70 records retry counts per hot row at 4/16/64 clients.\n")
    A("### 10.3 Foreign-key overhead\n")
    top = sorted([t for t in M.m["tables"] if t["fks"]], key=lambda t: -len(t["fks"]))[:12]
    A(f"{nfk} foreign keys over {sum(1 for t in M.m['tables'] if t['fks'])} tables; {sum(1 for t in M.m['tables'] for f in t['fks'] if f['ref'] in LOOKUPS)} of them point at lookup tables (tiny, permanently cached: ~0.05 ms) and {sum(1 for t in M.m['tables'] for f in t['fks'] if f['ref'] not in LOOKUPS)} at entity tables (a point read of the parent primary key, possibly on another node: ~0.3-1 ms, executed after the mutation, in parallel batches per statement). Every FK also needs a leading index on the child column (lint D-20), which is where most of the {nidx} secondary indexes come from.\n")
    A(md_table(["table", "FKs", "to lookups", "to entities", "indexes + unique", "FK read cost per insert (serial worst / parallel best, ms)", "write amplification"], [
        [f"`{t['name']}`", len(t['fks']), sum(1 for f in t['fks'] if f['ref'] in LOOKUPS), sum(1 for f in t['fks'] if f['ref'] not in LOOKUPS),
         len(t['indexes']) + sum(1 for c in t['constraints'] if c['type'] == 'unique'),
         f"{sum(1 for f in t['fks'] if f['ref'] in LOOKUPS) * 0.05 + sum(1 for f in t['fks'] if f['ref'] not in LOOKUPS) * 0.6:.1f} / {0.05 + 0.6 if any(f['ref'] not in LOOKUPS for f in t['fks']) else 0.05:.1f}",
         f"1 + {len(t['indexes']) + sum(1 for c in t['constraints'] if c['type'] == 'unique')} KV writes"] for t in top]))
    A("\nFindings: (a) high-volume insert paths pay the entity-FK checks (Q6: job_application has FKs to candidate_profile, organisation, job_requirement (composite), job_position, resume, ...). The composite FK `(organisation_id, job_requirement_id)` adds tenant integrity at the price of one extra parent read. (b) FK checks on parents that are TTL tables are avoided by design (no inbound FKs). (c) `ON DELETE RESTRICT` parents deleted by retention jobs scan child indexes: all children have leading indexes. (d) FKs to lookups are cheap and keep Section 5 rule 3 (no free-text enums); do not drop them for speed. (e) Bulk load: `IMPORT INTO` leaves FKs unvalidated; the seed uses `COPY`/batched INSERT in FK order (doc 12) and runs `VALIDATE CONSTRAINT` or the orphan checks of doc 12 section 6.\n")
    # 11
    A("## 11. Findings and recommendations to the data-model owner\n")
    A("These are observations from the model; this document does not edit the staging files.\n")
    A(md_table(["id", "finding", "recommendation"], [
        ["R-01", "`agg_funnel_daily` has no index that serves a category + date-range query; the hash-sharded date index is not covering (Q12).", "Add `idx_agg_funnel_daily_dim_job_category_id_period_date ON agg_funnel_daily (dim_job_category_id, period_date) STORING (applications, shortlisted, joined)`; same pattern for other `agg_*` tables."],
        ["R-02", f"Trigram indexes: {sum(1 for t in M.m['tables'] for ix in t['indexes'] if ix.get('opclass') == 'gin_trgm_ops')} inverted indexes on STRING columns carry `opclass: gin_trgm_ops` in the dictionary (Indexes sheet, last column); {len(NO_OPS)} lack it. A plain inverted index on a STRING column is not the trigram index the queries need.", "The DDL generator emits `USING GIN (col gin_trgm_ops)` from the `opclass` column; JSONB inverted indexes have an empty `opclass` and use plain `CREATE INVERTED INDEX`."],
        ["R-03", "Covering indexes on `job_application` (and others) do not store `deleted_at`, so every soft-delete-aware query (Q3, Q4) adds an index join per returned row.", "Add `deleted_at` to STORING of `idx_job_application_job_requirement_id_status_applied` and `idx_job_application_candidate_profile_id_applied_at`, or make them partial on `deleted_at IS NULL`."],
        ["R-04", "`idx_app_user_last_login_at` is a sequential-key index on a high-volume table (hot range at login rate).", "Hash-shard it or remove it; see section 6."],
        ["R-05", "`idx_job_requirement_title_search_trgm` is not partial, so closed/deleted jobs enlarge the posting lists (Q2).", "Make it partial `WHERE is_live` (trigram + partial is supported) or accept and filter."],
        ["R-06", "Q5 sorts by `last_active_at`, which is in no usable index.", "Add `(current_city_id, last_active_at DESC) STORING (current_job_title, total_experience_months) WHERE deleted_at IS NULL AND is_searchable` if the B70 baseline confirms the prediction."],
        ["R-07", "Viewer-scoped views (`vw_recruiter_job_requirement`, `vw_candidate_visible_to_user`) are pair views: without the viewer predicate they fan out over all viewers. Custom session variables are unverified on v26.2 (crdb_capabilities.md row 10), so the viewer is an explicit function/query argument (`fn_recruiter_job_requirement(viewer)`, `viewer_app_user_id = $1`).", "Keep the viewer predicate mandatory (CI lint rejects unfiltered references, statement_timeout per pool role) and probe the viewer-filtered plans in B70; both views have a single definition."],
        ["R-08", "`idx_candidate_activity_log_activity_type_id_occurred_at` and similar (enum, timestamp) indexes on very_high tables concentrate writes on few prefixes (section 6).", "Hash-shard or lead with `candidate_profile_id`."],
    ]))
    return "\n".join(L)


if __name__ == "__main__":
    n, out = build_catalogue()
    print("catalogue", n, "rows ->", out)
    md = build_md()
    p = os.path.join(ROOT, "docs", "11_performance_indexing_plan.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(md)
    print("wrote", p, len(md), "chars")
