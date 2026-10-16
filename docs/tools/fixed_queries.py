"""The 12 fixed queries (B65) as data + a validator against the dictionary (column existence, required INSERT columns).

python docs/tools/fixed_queries.py   -> validates every statement; used by gen_perf_plan.py and gen_seed_plan.py
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from dictlib import load_model

# id, kind, title, params, sql(list of statements), indexes relied on, plan notes, explain(top ten flag), latency target (ms, laptop p95)
Q = []


def q(**kw):
    Q.append(kw)


q(id="Q1", kind="lookup", title="Live job search by city, newest first (keyset pagination)", explain=True, target_p95_ms=15,
  params="$1 city id, $2 keyset cursor (published_at of last row seen)",
  sql=["""SELECT jr.id, jr.title, jr.job_category_id, jr.employment_type_id, jr.published_at
FROM job_requirement AS jr
WHERE jr.is_live
  AND jr.primary_city_id = $1
  AND jr.published_at < $2
ORDER BY jr.published_at DESC
LIMIT 20"""],
  indexes=["idx_job_requirement_primary_city_id_published_at"],
  plan="Expected: one `scan job_requirement@idx_job_requirement_primary_city_id_published_at` with constraint (primary_city_id = $1 AND published_at < $2) in REVERSE direction and LIMIT 20 pushed down (LIMITED SCAN, ~20 rows). Index is partial (WHERE is_live) and STORING (job_category_id, employment_type_id, title) so no index join. Fails the check if the plan shows `FULL SCAN` or `index join`. Invariant relied on: is_live is false for soft-deleted rows (validation V-JOB-03).")
q(id="Q2", kind="lookup", title="Job title substring search (trigram)", explain=True, target_p95_ms=60,
  params="$1 user search text (lower-cased by the app)",
  sql=["""SELECT jr.id, jr.title, jr.primary_city_id, jr.published_at
FROM job_requirement AS jr
WHERE jr.is_live
  AND jr.title_search LIKE '%' || lower($1) || '%'
ORDER BY jr.published_at DESC
LIMIT 20"""],
  indexes=["idx_job_requirement_title_search_trgm"],
  plan="Expected: `inverted filter` scan on idx_job_requirement_title_search_trgm (trigram GIN, requires USING GIN (title_search gin_trgm_ops); needs >= 3 characters), then index join to job_requirement, filter is_live, top-k sort on published_at. Rows fetched = posting-list size of the rarest trigram, so cost grows with number of jobs (the extrapolation target). The inverted index is NOT partial, so non-live jobs are also fetched then filtered.")
q(id="Q3", kind="performance", title="Employer pipeline board: applications of one requirement in one status", explain=True, target_p95_ms=25,
  params="$1 job requirement id, $2 application status id",
  sql=["""SELECT ja.id, ja.applied_at, ja.match_score, cp.id AS candidate_profile_id, cp.display_name, cp.headline
FROM job_application AS ja
JOIN candidate_profile AS cp ON cp.id = ja.candidate_profile_id
WHERE ja.job_requirement_id = $1
  AND ja.application_status_id = $2
  AND ja.deleted_at IS NULL
ORDER BY ja.applied_at DESC
LIMIT 50"""],
  indexes=["idx_job_application_job_requirement_id_status_applied", "candidate_profile primary key"],
  plan="Expected: scan job_application@idx_job_application_job_requirement_id_status_applied (job_requirement_id, application_status_id, applied_at DESC) STORING (candidate_profile_id, match_score), constraint on the first two columns, LIMIT 50; deleted_at is not in the index, so a filter after an index join (50 PK lookups) appears unless deleted_at is added to STORING (recommendation R-03); then lookup join into candidate_profile by primary key (50 point reads). Row order comes from the index, no sort node.")
q(id="Q4", kind="functional", title="Candidate application history with job title", explain=True, target_p95_ms=15,
  params="$1 candidate profile id",
  sql=["""SELECT ja.id, ja.applied_at, ja.application_status_id, jr.id AS job_requirement_id, jr.title, jr.organisation_id
FROM job_application AS ja
JOIN job_requirement AS jr ON jr.id = ja.job_requirement_id
WHERE ja.candidate_profile_id = $1
  AND ja.deleted_at IS NULL
ORDER BY ja.applied_at DESC
LIMIT 20"""],
  indexes=["idx_job_application_candidate_profile_id_applied_at"],
  plan="Expected: scan job_application@idx_job_application_candidate_profile_id_applied_at (candidate_profile_id, applied_at DESC) STORING (application_status_id, job_requirement_id) with LIMIT, lookup join job_requirement (PK) for title/organisation. A candidate has ~5 applications on average (5M/1M) so the scan is tiny; deleted_at filter needs an index join as for Q3.")
q(id="Q5", kind="performance", title="Recruiter candidate search: city + skill + experience, honouring blocks and privacy", explain=True, target_p95_ms=250,
  params="$1 city id, $2 skill id, $3/$4 experience months range, $5 recruiter app_user id, $6 recruiter organisation id",
  sql=["""SELECT cp.id, cp.display_name, cp.headline, cp.current_job_title, cp.total_experience_months, cp.last_active_at
FROM candidate_profile AS cp
JOIN candidate_skill AS cs
  ON cs.candidate_profile_id = cp.id AND cs.skill_id = $2 AND cs.deleted_at IS NULL
JOIN privacy_setting AS ps ON ps.app_user_id = cp.app_user_id
WHERE cp.current_city_id = $1
  AND cp.deleted_at IS NULL
  AND cp.is_searchable
  AND ps.is_searchable_by_recruiters
  AND cp.total_experience_months BETWEEN $3 AND $4
  AND NOT EXISTS (SELECT 1 FROM user_block AS ub
                  WHERE ub.blocker_app_user_id = cp.app_user_id
                    AND ub.blocked_app_user_id = $5
                    AND ub.effective_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM privacy_hidden_organisation AS h
                  WHERE h.app_user_id = cp.app_user_id AND h.organisation_id = $6)
ORDER BY cp.last_active_at DESC NULLS LAST
LIMIT 50"""],
  indexes=["idx_candidate_profile_current_city_id_availability_status_id", "idx_candidate_skill_skill_id_years_of_experience", "uq_user_block_blocker_app_user_id_blocked_app_user_id_open", "uq_privacy_hidden_organisation (see dictionary)", "privacy_setting unique on app_user_id"],
  plan="Expected: two candidate sets are built and joined: (a) candidate_profile via the partial index (current_city_id, availability_status_id) WHERE deleted_at IS NULL AND is_searchable STORING (current_job_title, total_experience_months) - prefix scan on current_city_id; (b) candidate_skill via idx_candidate_skill_skill_id_years_of_experience STORING (candidate_profile_id, proficiency_level_id). Optimizer chooses hash join or lookup join by estimated cardinality; anti-joins to user_block and privacy_hidden_organisation are point lookups per surviving candidate. last_active_at is NOT in any index used here, so ORDER BY ... LIMIT is a top-k sort over ALL matches - cost grows linearly with profiles in the city. This is the primary degradation candidate (see B74).")
q(id="Q6", kind="transactional", title="Apply to a job (idempotent, one live application per candidate and requirement)", explain=True, target_p95_ms=40,
  params="$1 job requirement id, $2 candidate profile id, $3 organisation id, $4 job source id, $5 initial status id, $6 actor kind id, $7 idempotency key, $8 resume id",
  sql=["""SELECT jr.is_live, jr.max_applications, jr.organisation_id
FROM job_requirement AS jr
WHERE jr.id = $1""",
       """INSERT INTO job_application (candidate_profile_id, organisation_id, job_requirement_id, job_source_id, application_status_id,
                             status_sequence_no, status_changed_at, applied_at, is_screened_out, resume_id)
VALUES ($2, $3, $1, $4, $5, 1, now(), now(), false, $8)
ON CONFLICT (candidate_profile_id, job_requirement_id) WHERE withdrawn_at IS NULL AND deleted_at IS NULL
DO NOTHING
RETURNING id""",
       """INSERT INTO job_application_status_history (job_application_id, job_requirement_id, sequence_no, to_application_status_id, actor_kind_id, idempotency_key)
SELECT ja.id, $1, 1, $5, $6, $7
FROM job_application AS ja
WHERE ja.candidate_profile_id = $2 AND ja.job_requirement_id = $1 AND ja.withdrawn_at IS NULL AND ja.deleted_at IS NULL
ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING"""],
  indexes=["uq_job_application_candidate_profile_id_job_requirement_id", "uq_job_application_status_history_idempotency_key", "uq_job_application_status_history_job_application_id_sequence_no"],
  plan="Expected: 3 statements in one transaction. INSERT writes the primary row plus 7 secondary index entries (uq candidate+requirement, requirement/status/applied storing, candidate/applied storing, organisation/status/changed_at, two hash-sharded time indexes, partial resume_id) and performs FK checks to candidate_profile, organisation, job_requirement (composite organisation_id,id), job_source, application_status, resume (see FK overhead). Arbiter is the partial unique index: a replay inserts 0 rows and RETURNING is empty (idempotent). No hot row: the requirement counters are updated asynchronously into job_requirement_stat.")
q(id="Q7", kind="transactional", title="Move an application to the next pipeline status (optimistic version)", explain=True, target_p95_ms=40,
  params="$1 application id, $2 expected status_sequence_no, $3 new status id, $4 old status id, $5 actor app_user id, $6 actor kind id, $7 idempotency key",
  sql=["""UPDATE job_application
SET application_status_id = $3, status_sequence_no = status_sequence_no + 1, status_changed_at = now(), updated_at = now(), updated_by = $5
WHERE id = $1 AND status_sequence_no = $2 AND deleted_at IS NULL
RETURNING status_sequence_no, job_requirement_id""",
       """INSERT INTO job_application_status_history (job_application_id, job_requirement_id, sequence_no, prior_sequence_no, from_application_status_id, to_application_status_id, actor_kind_id, idempotency_key)
VALUES ($1, $8, $2 + 1, $2, $4, $3, $6, $7)"""],
  indexes=["job_application primary key", "uq_job_application_status_history_job_application_id_sequence_no_to_application_status_id"],
  plan="Expected: UPDATE by PK (point), 0 rows when another recruiter already moved it (status_sequence_no mismatch) -> client re-reads; else rewrites 4 secondary entries (status in idx_..._requirement_status_applied and candidate_applied storing, organisation_status_changed_at, status_changed_at_hash). History insert checks the composite FK to application_status_transition (from,to) and the self composite FK to the prior history row. $8 = job_requirement_id returned by the UPDATE.")
q(id="Q8", kind="transactional", title="Profile download paid from the wallet (ledger append + balance, idempotent)", explain=True, target_p95_ms=60,
  params="$1 wallet id, $2 credits, $3 idempotency key, $4 actor app_user id, $5 organisation id, $6 candidate profile id, $7 ledger entry type id, $8 download kind id, $9 download source id, $10 resume id",
  sql=["""SELECT w.credit_balance, w.ledger_sequence, w.wallet_status_id
FROM wallet AS w
WHERE w.id = $1
FOR UPDATE""",
       """INSERT INTO wallet_ledger_entry (wallet_id, sequence_no, prev_sequence_no, ledger_entry_type_id, credits_delta, is_debit, balance_after, idempotency_key, actor_app_user_id, has_fk_reference, row_hash)
VALUES ($1, $11 + 1, $11, $7, -$2, true, $12 - $2, $3, $4, false, $13)
RETURNING id""",
       """UPDATE wallet
SET credit_balance = credit_balance - $2, ledger_sequence = ledger_sequence + 1, last_entry_at = now(), updated_at = now()
WHERE id = $1 AND credit_balance >= $2""",
       """INSERT INTO profile_download (organisation_id, actor_app_user_id, candidate_profile_id, resume_id, download_kind_id, download_source_id, wallet_ledger_entry_id, credits_charged, downloaded_at, idempotency_key)
VALUES ($5, $4, $6, $10, $8, $9, $14, $2, now(), $3)"""],
  indexes=["wallet primary key (row lock)", "uq_wallet_ledger_entry_wallet_id_sequence_no", "uq_wallet_ledger_entry_idempotency_key", "uq_profile_download_idempotency_key", "uq_profile_download_wallet_ledger_entry_id"],
  plan="Expected: SELECT ... FOR UPDATE takes an exclusive lock on one wallet row, serialising all spenders of that wallet (hot-row analysis, ceiling ~ 1 / (txn latency)). Ledger insert enforces sequence gap-freedom via uq (wallet_id, sequence_no) and replay safety via uq idempotency_key; $11 = ledger_sequence read in statement 1, $12 = credit_balance, $13 = SHA-256 chain value computed by the app, $14 = id returned by statement 2. A replay violates uq_wallet_ledger_entry_idempotency_key and the app returns the first result. 4 statements, 1 commit, expected 2 consensus rounds (parallel commits).")
q(id="Q9", kind="transactional", title="Login: claim a free device slot under the licence device limit", explain=False, target_p95_ms=40,
  params="$1 app_user id, $2 device id, $3 refresh token hash, $4 auth method id, $5 ip, $6 user agent",
  sql=["""SELECT ls.id, ls.licence_id
FROM licence_seat AS ls
WHERE ls.app_user_id = $1 AND ls.revoked_at IS NULL AND ls.deleted_at IS NULL
FOR UPDATE""",
       """SELECT ds.id
FROM device_slot AS ds
WHERE ds.licence_seat_id = $7 AND NOT ds.is_retired
  AND NOT EXISTS (SELECT 1 FROM device_session AS s WHERE s.device_slot_id = ds.id AND s.ended_at IS NULL)
ORDER BY ds.slot_no
LIMIT 1""",
       """INSERT INTO identity_session (app_user_id, refresh_token_hash, token_family_ref, auth_method_id, mfa_satisfied, issued_at, expires_at, absolute_expires_at, ip_address, user_agent)
VALUES ($1, $3, gen_random_uuid(), $4, false, now(), now() + interval '30 minutes', now() + interval '12 hours', $5, $6)
RETURNING id""",
       """INSERT INTO device_session (identity_session_id, app_user_id, device_id, licence_seat_id, device_slot_id, started_at, last_activity_at, expires_at, ip_address, authentication_method_id)
VALUES ($8, $1, $2, $7, $9, now(), now(), now() + interval '30 minutes', $5, $4)"""],
  indexes=["uq_licence_seat_licence_id_app_user_id", "uq_device_slot_licence_seat_id_slot_no", "uq_device_session_device_slot_id (partial WHERE ended_at IS NULL)", "uq_device_session_device_id (partial)"],
  plan="Expected: lock on the user's licence_seat row (per user, so no cross-user contention), pick the lowest free slot with an anti-join on the partial unique index uq_device_session_device_slot_id, insert. If two concurrent logins pick the same slot the partial unique index rejects the second (40001/23505): the database, not the application, enforces the limit. No free slot: instead of statements 3-4 insert device_limit_violation + audit_event + login_attempt (rejection) in the same transaction. $7 = seat id, $8 = identity_session id, $9 = slot id from the earlier statements.")
q(id="Q10", kind="lookup", title="Unread in-app notifications for a user", explain=False, target_p95_ms=10,
  params="$1 app_user id",
  sql=["""SELECT n.id, n.notification_type_id, n.payload, n.created_at
FROM notification AS n
WHERE n.recipient_app_user_id = $1
  AND n.read_at IS NULL
  AND n.expires_at > now()
ORDER BY n.created_at DESC
LIMIT 20"""],
  indexes=["idx_notification_recipient_app_user_id_created_at_unread"],
  plan="Expected: scan notification@idx_notification_recipient_app_user_id_created_at_unread (partial WHERE read_at IS NULL), constraint recipient_app_user_id = $1, reverse order, LIMIT 20; payload and notification_type_id are not stored in that partial index, so an index join of 20 rows is expected (the non-partial index STORING (notification_type_id, read_at) does not cover payload either). Rows past expires_at (TTL not yet run) are filtered.")
q(id="Q11", kind="performance", title="Requirement funnel: transitions per status in a time window", explain=True, target_p95_ms=40,
  params="$1 job requirement id, $2 window start, $3 window end",
  sql=["""SELECT h.to_application_status_id, count(*) AS transitions
FROM job_application_status_history AS h
WHERE h.job_requirement_id = $1
  AND h.created_at >= $2 AND h.created_at < $3
GROUP BY h.to_application_status_id"""],
  indexes=["idx_job_application_status_history_job_requirement_id_funnel"],
  plan="Expected: covered scan on idx_job_application_status_history_job_requirement_id_funnel (job_requirement_id, to_application_status_id, created_at): span per status value, streaming group-by with no sort. Rows scanned = history rows of ONE requirement in the window: ~200 on average (4 transitions x 50 applications), but mega-requirements (Zipf tail, 100k+ applicants) read hundreds of thousands: the long tail is what degrades.")
q(id="Q12", kind="performance", title="Organisation-agnostic funnel KPI trend by job category (analytics aggregate)", explain=True, target_p95_ms=120,
  params="$1 window start date, $2 window end date, $3 dim_job_category id",
  sql=["""SELECT f.period_date, sum(f.applications) AS applications, sum(f.shortlisted) AS shortlisted, sum(f.joined) AS joined
FROM agg_funnel_daily AS f
WHERE f.period_date >= $1 AND f.period_date < $2
  AND f.dim_job_category_id = $3
GROUP BY f.period_date
ORDER BY f.period_date"""],
  indexes=["idx_agg_funnel_daily_period_date (hash-sharded, 8 buckets)", "PROPOSED: idx_agg_funnel_daily_dim_job_category_id_period_date STORING (applications, shortlisted, joined)"],
  plan="Expected with the existing schema: the only usable index is idx_agg_funnel_daily_period_date, hash-sharded (8 buckets): the range predicate becomes 8 spans, each followed by an index join to the primary index to read dim_job_category_id and the measures (the index stores none of them), then hash/sort aggregate: cost proportional to ALL rows in the date window across ALL categories. The unique grain (applied_dim_date_id UUIDv5, dim_job_category_id, ...) cannot serve a date range because the date key is not ordered. Proposed index (category, period_date) STORING the three measures turns it into a covered range scan (see section 7 analytics justification).")


# ---- validation -----------------------------------------------------------------------------------------------------
def validate():
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.qualify import qualify
    m = load_model()
    T = {t["name"]: t for t in m["tables"]}
    schema = {t["name"]: {c["name"]: "TEXT" for c in t["columns"]} for t in m["tables"]}
    errors = []
    for qq in Q:
        for i, s in enumerate(qq["sql"]):
            s2 = s  # $n placeholders are parsed natively by sqlglot (postgres)
            try:
                tree = sqlglot.parse_one(s2, read="postgres")
            except Exception as e:  # noqa
                errors.append(f"{qq['id']}#{i+1} parse: {e}")
                continue
            if isinstance(tree, exp.Insert):
                tname = tree.this.this.name if isinstance(tree.this, exp.Schema) else tree.this.name
                cols = [c.name for c in tree.this.expressions] if isinstance(tree.this, exp.Schema) else []
                if tname not in T:
                    errors.append(f"{qq['id']}#{i+1} unknown table {tname}"); continue
                have = {c["name"] for c in T[tname]["columns"]}
                for c in cols:
                    if c not in have:
                        errors.append(f"{qq['id']}#{i+1} unknown column {tname}.{c}")
                req = [c["name"] for c in T[tname]["columns"] if not c.get("null") and c.get("default") is None and c["name"] not in ("id", "created_by", "updated_by")
                       and not c.get("computed")]
                miss = [c for c in req if c not in cols]
                if miss:
                    errors.append(f"{qq['id']}#{i+1} INSERT {tname} misses NOT NULL columns without default: {miss}")
                sel = tree.expression
                if isinstance(sel, exp.Select):
                    try:
                        qualify(sel, schema=schema, dialect="postgres", validate_qualify_columns=True)
                    except Exception as e:  # noqa
                        errors.append(f"{qq['id']}#{i+1} select-part: {e}")
                continue
            try:
                qualify(tree, schema=schema, dialect="postgres", validate_qualify_columns=True)
            except Exception as e:  # noqa
                errors.append(f"{qq['id']}#{i+1} qualify: {e}")
    return errors


if __name__ == "__main__":
    errs = validate()
    print("\n".join(errs) if errs else f"all {len(Q)} queries valid ({sum(len(x['sql']) for x in Q)} statements)")
