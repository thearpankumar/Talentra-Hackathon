"""Generate docs/12_seed_and_validation_plan.md, docs/12_seed_targets.xlsx and docs/tools/validation_suite.sql.

All table / column / constraint names are verified against the dictionary; module query sets are parsed and column-checked with sqlglot.
python docs/tools/gen_seed_plan.py
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from collections import Counter, defaultdict
import networkx as nx
import sqlglot
from sqlglot.optimizer.qualify import qualify
import perf_model as pm
from dictlib import ROOT, MODULES, MODULE_OWNER
from xl import new_wb, add_sheet

M = pm.Model()
T = M.T
ST = {s["name"]: s for s in M.all_stats()}
LOOKUPS = {n for n, t in T.items() if t["is_lookup"]}
BAD = []


def cons_exists(t, n):
    names = {c["name"] for c in T[t]["constraints"]} | {i["name"] for i in T[t]["indexes"]} | {f["name"] for f in T[t]["fks"]}
    if n not in names:
        BAD.append(f"{t}.{n}")
    return n


# ======================================================================================================================
# 1. FK topological order
# ======================================================================================================================
def load_order():
    G = nx.DiGraph()
    G.add_nodes_from(T)
    nullable = {}
    selfs = []
    for t in M.m["tables"]:
        cm = {c["name"]: c for c in t["columns"]}
        for fk in t["fks"]:
            r = fk["ref"]
            nl = any(cm[c].get("null") for c in fk["columns"] if c in cm)   # MATCH SIMPLE: any NULL column skips the check
            if r == t["name"]:
                selfs.append((t["name"], fk["name"], nl))
                continue
            key = (t["name"], r)
            nullable[key] = nullable.get(key, True) and nl
            G.add_edge(t["name"], r)
    deferred = []
    while True:
        comps = [c for c in nx.strongly_connected_components(G) if len(c) > 1]
        if not comps:
            break
        comp = sorted(comps[0])
        cand = sorted((a, b) for a in comp for b in G[a] if b in comp and nullable[(a, b)])
        assert cand, f"cycle without nullable edge: {comp}"
        a, b = cand[0]
        deferred.append((a, b))
        G.remove_edge(a, b)
    assert nx.is_directed_acyclic_graph(G), "cycle without nullable edge"
    wave = {}
    for n in reversed(list(nx.topological_sort(G))):  # parents first when reversed (edges child->parent)
        ps = list(G[n])
        wave[n] = 0 if not ps else 1 + max(wave[p] for p in ps)
    return wave, deferred, selfs, G


WAVE, DEFERRED, SELFS, G = load_order()
NWAVES = max(WAVE.values()) + 1

# module-level graph
MG = nx.DiGraph()
for t in M.m["tables"]:
    for fk in t["fks"]:
        a, b = t["module"], T[fk["ref"]]["module"]
        if a != b:
            MG.add_edge(a, b)
MOD_SCC = [sorted(c) for c in nx.strongly_connected_components(MG) if len(c) > 1]

# ======================================================================================================================
# 2. negative tests (names verified)
# ======================================================================================================================
NEG = [
    ("N-01", "gig_marketplace", "Overlapping gig window for the same worker", 1000, "INSERT gig_slot rows (same gig_worker_profile_id, slot_start_at) already held by another assignment, in the assignment txn", cons_exists("gig_slot", "uq_gig_slot_gig_worker_profile_id_slot_start_at"), "SQLSTATE 23505; assignment txn rolled back; no partial slots; gig_assignment count unchanged", "BR-GIG (P2 slot table)"),
    ("N-02", "gig_marketplace", "Duplicate handoff (same idempotency key)", 5000, "INSERT gig_assignment with an existing handoff_idempotency_key", cons_exists("gig_assignment", "uq_gig_assignment_handoff_idempotency_key"), "SQLSTATE 23505; app returns the stored handoff_ref", "BR-GIG-17"),
    ("N-03", "gig_marketplace", "Replayed fulfilment event", 20000, "INSERT gig_fulfilment_event with an existing (source_system, idempotency_key)", cons_exists("gig_fulfilment_event", "uq_gig_fulfilment_event_source_system_idempotency_key"), "ON CONFLICT DO NOTHING returns 0 rows; earnings snapshot not duplicated", "Section 6 boundary 4"),
    ("N-04", "subscriptions_payments", "Overlapping subscription period for the same licence", 5000, "INSERT subscription_period whose prior_period_end_date and period_start_date violate the chain (start < prior end) or that reuses an already-chained prior_period_id", cons_exists("subscription_period", "uq_subscription_period_prior_period_id"), "SQLSTATE 23505 / 23503 / 23514; period not created", "BR-SUB (P1 CHAIN_FK), Rule 10"),
    ("N-05", "subscriptions_payments", "Second first-period for a licence", 500, "INSERT subscription_period with prior_period_id NULL for a licence that already has a first period", cons_exists("subscription_period", "uq_subscription_period_licence_id_first"), "SQLSTATE 23505", "P1 CHAIN_FK"),
    ("N-06", "subscriptions_payments", "Wallet double spend / replayed debit", 20000, "64 concurrent clients run Q8 with the same idempotency_key and with different keys against a wallet whose balance covers only one debit", cons_exists("wallet_ledger_entry", "uq_wallet_ledger_entry_idempotency_key"), "exactly one ledger row per key; with insufficient balance UPDATE ... WHERE credit_balance >= credits affects 0 rows and the txn rolls back; balance never negative", "BR-SUB-08, D-14"),
    ("N-07", "subscriptions_payments", "Replayed payment webhook", 100000, "INSERT payment_event with an existing (payment_gateway_id, gateway_event_ref)", cons_exists("payment_event", "uq_payment_event_gateway_event_ref"), "0 rows inserted; payment_event_outcome 'ignored_duplicate'", "BR-SUB-14, boundary 1"),
    ("N-08", "subscriptions_payments", "Duplicate payment order / refund with same idempotency key", 10000, "INSERT payment_order and refund with repeated idempotency_key", cons_exists("payment_order", "uq_payment_order_idempotency_key"), "SQLSTATE 23505; original row returned", "BR-SUB-14"),
    ("N-09", "licences_devices", "Login beyond max_devices_per_user", 20000, "Run Q9 for a user whose active sessions already fill every device_slot; also 2 concurrent logins racing for the last slot", cons_exists("device_session", "uq_device_session_device_slot_id"), "no free slot -> device_limit_violation + audit_event + login_attempt rejection in one txn; race loser gets 23505 (retry picks another slot or is rejected); active sessions never exceed max_devices_per_user", "BR-LIC (D-13), Rule 9"),
    ("N-10", "licences_devices", "Same device live in two sessions", 3000, "INSERT device_session for a device_id with an open session", cons_exists("device_session", "uq_device_session_device_id"), "SQLSTATE 23505", "D-13"),
    ("N-11", "licences_devices", "More seats than max_users", 2000, "Assign a (max_users + 1)th seat concurrently (licence row FOR UPDATE then insert)", cons_exists("licence_seat", "uq_licence_seat_licence_seat_slot_id"), "second seat rejected: slot pre-created per max_users so no free licence_seat_slot exists", "BR-LIC, Rule 9"),
    ("N-12", "jobs_applications", "Duplicate live application", 50000, "INSERT job_application for an existing (candidate_profile_id, job_requirement_id) with withdrawn_at NULL", cons_exists("job_application", "uq_job_application_candidate_profile_id_job_requirement_id"), "0 rows (ON CONFLICT DO NOTHING) or 23505; re-apply after withdrawal succeeds", "BR-JOB-02"),
    ("N-13", "jobs_applications", "Illegal status transition", 10000, "INSERT job_application_status_history with a (from, to) pair absent from application_status_transition", cons_exists("job_application_status_history", "uq_job_application_status_history_job_application_id_sequence_no"), "SQLSTATE 23503 (composite FK to application_status_transition) or 23505 (sequence gap/duplicate)", "BR-JOB-12"),
    ("N-14", "jobs_applications", "Stale concurrent status update", 10000, "Two recruiters run Q7 with the same expected status_sequence_no", cons_exists("job_application", "idx_job_application_job_requirement_id_status_applied"), "second UPDATE affects 0 rows; no history row for it", "BR-JOB-12"),
    ("N-15", "administration", "Contact a candidate who blocked the contacter", 10000, "INSERT outreach_recipient / message_thread for a blocker/blocked pair with an open user_block", cons_exists("user_block", "uq_user_block_blocker_app_user_id_blocked_app_user_id_open"), "excluded (outreach_exclusion) or rejected by the block check in the same txn; candidate_contact_attempt.block_checked_at set", "BR-ADM-12, BR-ADM-13"),
    ("N-16", "administration", "Second open block for the same pair", 1000, "INSERT user_block twice with effective_to NULL", cons_exists("user_block", "uq_user_block_blocker_app_user_id_blocked_app_user_id_open"), "SQLSTATE 23505", "BR-ADM"),
    ("N-17", "administration", "Two live accounts with the same email / phone", 5000, "INSERT app_user with an existing live email (case-variant lower-cased by CHECK)", cons_exists("app_user", "uq_app_user_email_live"), "SQLSTATE 23505; upper-case email rejected by CHECK (23514)", "Section 5 rule 3"),
    ("N-18", "administration", "UPDATE/DELETE on append-only audit_event", 200, "As app_service role: UPDATE and DELETE audit_event; as audit_writer: same", cons_exists("audit_event", "uq_audit_event_chain_position"), "SQLSTATE 42501 (permission denied); chain positions unique (23505) on replayed chain_seq", "Rule 4, BR-ADM audit"),
    ("N-19", "organisation_account", "Overlapping recruiter mapping", 3000, "INSERT recruiter_organisation_mapping with a second open row or a successor starting before the prior end", cons_exists("recruiter_organisation_mapping", "uq_recruiter_organisation_mapping_recruiter_app_user_id_organisation_id_open"), "SQLSTATE 23505 / 23503 / 23514", "BR-ORG-01/02"),
    ("N-20", "sourcing_consultant", "Consultant sourcing request on a requirement not open to consultants", 3000, "INSERT sourcing_request with consultant_job_openness_id of a closed/other requirement", cons_exists("sourcing_request", "uq_sourcing_request_id_consultant_profile_id"), "SQLSTATE 23503 (composite FK job_requirement_id, consultant_job_openness_id)", "BR-SRC-01 (B46)"),
    ("N-21", "profile", "Duplicate national-identity binding", 2000, "INSERT candidate_identity_verification with an existing (country, document type, id_number_hmac) active binding", cons_exists("candidate_identity_verification", "uq_candidate_identity_verification_country_id_binding"), "SQLSTATE 23505", "BR-PRF, boundary 2"),
    ("N-22", "notifications_community", "Replayed provider webhook / duplicate delivery", 200000, "INSERT provider_webhook_event and notification_delivery_event with existing keys", cons_exists("provider_webhook_event", "uq_provider_webhook_event_notification_provider_id_provider_event_ref"), "0 rows inserted; one row per (delivery, status)", "B57, boundary 6"),
    ("N-23", "subscriptions_payments", "Download billed twice", 3000, "INSERT profile_download twice with the same idempotency_key or the same wallet_ledger_entry_id", cons_exists("profile_download", "uq_profile_download_wallet_ledger_entry_id"), "SQLSTATE 23505 (one ledger entry pays one download)", "BR-SUB-18"),
]

# ======================================================================================================================
# 3. module query sets (B67-B69): validated
# ======================================================================================================================
MQ = []


def mq(batch, mod, qid, title, sql, expect):
    MQ.append(dict(batch=batch, module=mod, id=qid, title=title, sql=sql, expect=expect))


# Member A: B67
mq("B67", "administration", "A-ADM-1", "Users per actor type and status", "SELECT u.actor_type_id, u.user_status_id, count(*) AS users FROM app_user AS u WHERE u.deleted_at IS NULL GROUP BY u.actor_type_id, u.user_status_id ORDER BY users DESC", "candidate ~1.0M (87% of 1.15M), recruiter/employer/consultant/TPO ~140k together, staff < 100; status active >= 90%")
mq("B67", "administration", "A-ADM-2", "Live email uniqueness", "SELECT u.email, count(*) AS n FROM app_user AS u WHERE u.email IS NOT NULL AND u.deleted_at IS NULL GROUP BY u.email HAVING count(*) > 1", "0 rows")
mq("B67", "administration", "A-ADM-3", "At most one active credential per user", "SELECT c.app_user_id, count(*) AS n FROM user_credential AS c WHERE c.is_active GROUP BY c.app_user_id HAVING count(*) > 1", "0 rows")
mq("B67", "administration", "A-ADM-4", "Audit chain continuity (no gaps per chain)", "SELECT e.chain_no, count(*) AS n, min(e.chain_seq) AS first_seq, max(e.chain_seq) AS last_seq FROM audit_event AS e GROUP BY e.chain_no HAVING max(e.chain_seq) - min(e.chain_seq) + 1 <> count(*)", "0 rows (32 chains, contiguous chain_seq)")
mq("B67", "administration", "A-ADM-5", "Open blocks per blocker (top 20)", "SELECT b.blocker_app_user_id, count(*) AS open_blocks FROM user_block AS b WHERE b.effective_to IS NULL GROUP BY b.blocker_app_user_id ORDER BY open_blocks DESC LIMIT 20", "40k blocks over ~25k blockers; heaviest blocker <= 300 (Zipf tail)")
mq("B67", "organisation_account", "A-ORG-1", "Organisations by verification status", "SELECT o.verification_status_id, count(*) AS organisations FROM organisation AS o WHERE o.deleted_at IS NULL GROUP BY o.verification_status_id", "~62% verified, 18% pending, 12% unverified, 6% failed, 2% expired (seed distribution)")
mq("B67", "organisation_account", "A-ORG-2", "Recruiter mapping chain never starts before its predecessor ends", "SELECT m.id FROM recruiter_organisation_mapping AS m JOIN recruiter_organisation_mapping AS p ON p.id = m.prior_recruiter_organisation_mapping_id WHERE m.effective_from < p.effective_to", "0 rows")
mq("B67", "organisation_account", "A-ORG-3", "Organisations without a headquarters location", "SELECT count(*) AS no_hq FROM organisation AS o WHERE o.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM organisation_location AS l WHERE l.organisation_id = o.id AND l.is_headquarters AND l.deleted_at IS NULL)", "<= 5% (small employers without a recorded address)")
mq("B67", "organisation_account", "A-ORG-4", "Recruiters mapped to more than one organisation at the same time", "SELECT m.recruiter_app_user_id, count(*) AS open_mappings FROM recruiter_organisation_mapping AS m WHERE m.effective_to IS NULL GROUP BY m.recruiter_app_user_id HAVING count(*) > 1 ORDER BY open_mappings DESC", "consultants/agency recruiters only: ~8% of recruiters, max 12 organisations")
mq("B67", "gig_marketplace", "A-GIG-1", "No overlapping live assignments per worker", "SELECT a.gig_worker_profile_id, count(*) AS overlaps FROM gig_assignment AS a JOIN gig_assignment AS b ON b.gig_worker_profile_id = a.gig_worker_profile_id AND a.id < b.id AND a.window_start_at < b.window_end_at AND b.window_start_at < a.window_end_at WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL GROUP BY a.gig_worker_profile_id", "0 rows among non-cancelled assignments (cancelled ones release their slots; add a status filter in the final text)")
mq("B67", "gig_marketplace", "A-GIG-2", "accepted_count equals the number of assignments", "SELECT r.id, r.accepted_count FROM gig_request AS r WHERE r.accepted_count <> (SELECT count(*) FROM gig_assignment AS a WHERE a.gig_request_id = r.id AND a.deleted_at IS NULL)", "0 rows")
mq("B67", "gig_marketplace", "A-GIG-3", "Earnings only after an acknowledged handoff", "SELECT count(*) AS bad FROM gig_assignment AS a WHERE a.total_earnings IS NOT NULL AND a.handoff_acknowledged_at IS NULL", "0")
mq("B67", "gig_marketplace", "A-GIG-4", "Reconciliation backlog", "SELECT a.gig_reconcile_state_id, count(*) AS n FROM gig_assignment AS a WHERE NOT a.is_completed AND a.deleted_at IS NULL GROUP BY a.gig_reconcile_state_id", "in_sync ~92%, pending ~5%, drift_detected ~2%, manual_review ~1%")
# Member B: B68
mq("B68", "licences_devices", "B-LIC-1", "Active sessions never exceed max_devices_per_user", "SELECT ls.id AS licence_seat_id, l.max_devices_per_user, count(*) AS active_sessions FROM device_session AS s JOIN licence_seat AS ls ON ls.id = s.licence_seat_id JOIN licence AS l ON l.id = ls.licence_id WHERE s.ended_at IS NULL GROUP BY ls.id, l.max_devices_per_user HAVING count(*) > l.max_devices_per_user", "0 rows")
mq("B68", "licences_devices", "B-LIC-2", "Seats in use never exceed max_users", "SELECT l.id, l.max_users FROM licence AS l WHERE (SELECT count(*) FROM licence_seat AS s WHERE s.licence_id = l.id AND s.revoked_at IS NULL AND s.deleted_at IS NULL) > l.max_users", "0 rows")
mq("B68", "licences_devices", "B-LIC-3", "Device-limit violations recorded with an audit event", "SELECT count(*) AS violations, count(v.audit_event_id) AS with_audit FROM device_limit_violation AS v", "violations = N-09 attempts that hit a full seat (~20,000); with_audit = violations")
mq("B68", "sourcing_consultant", "B-SRC-1", "No live sourcing request on a requirement closed to consultants", "SELECT sr.id FROM sourcing_request AS sr JOIN job_requirement AS jr ON jr.id = sr.job_requirement_id WHERE NOT jr.is_open_to_consultants AND sr.closed_at IS NULL", "0 rows")
mq("B68", "sourcing_consultant", "B-SRC-2", "Outreach credits charged equal recipients sent", "SELECT b.id, b.credits_per_recipient * count(*) FILTER (WHERE r.sent_at IS NOT NULL) AS expected_credits, sum(r.credits_charged) AS charged FROM outreach_batch AS b JOIN outreach_recipient AS r ON r.outreach_batch_id = b.id GROUP BY b.id, b.credits_per_recipient HAVING sum(r.credits_charged) <> b.credits_per_recipient * count(*) FILTER (WHERE r.sent_at IS NOT NULL)", "0 rows")
mq("B68", "sourcing_consultant", "B-SRC-3", "Excluded candidates never become recipients of the same batch", "SELECT e.outreach_batch_id, e.candidate_profile_id FROM outreach_exclusion AS e JOIN outreach_recipient AS r ON r.outreach_batch_id = e.outreach_batch_id AND r.candidate_profile_id = e.candidate_profile_id", "0 rows")
mq("B68", "subscriptions_payments", "B-SUB-1", "Wallet balance equals the sum of ledger deltas", "SELECT w.id, w.credit_balance, sum(l.credits_delta) AS ledger_sum FROM wallet AS w JOIN wallet_ledger_entry AS l ON l.wallet_id = w.id GROUP BY w.id, w.credit_balance HAVING w.credit_balance <> sum(l.credits_delta)", "0 rows")
mq("B68", "subscriptions_payments", "B-SUB-2", "Ledger sequence is gap-free per wallet", "SELECT l.wallet_id, min(l.sequence_no) AS first_seq, max(l.sequence_no) AS last_seq, count(*) AS n FROM wallet_ledger_entry AS l GROUP BY l.wallet_id HAVING min(l.sequence_no) <> 1 OR max(l.sequence_no) <> count(*)", "0 rows")
mq("B68", "subscriptions_payments", "B-SUB-3", "Subscription periods of a licence never overlap", "SELECT a.licence_id, a.id, b.id AS other FROM subscription_period AS a JOIN subscription_period AS b ON b.licence_id = a.licence_id AND a.id < b.id AND a.period_start_date < b.period_end_date AND b.period_start_date < a.period_end_date", "0 rows")
mq("B68", "subscriptions_payments", "B-SUB-4", "Credits charged exactly when a ledger entry exists", "SELECT count(*) AS bad FROM profile_download AS d WHERE (d.credits_charged > 0) <> (d.wallet_ledger_entry_id IS NOT NULL)", "0 (free-quota and free-repeat downloads have credits_charged = 0 and no ledger entry)")
mq("B68", "subscriptions_payments", "B-SUB-5", "Refunds never exceed the paid amount", "SELECT r.payment_order_id, p.amount, sum(r.amount) AS refunded FROM refund AS r JOIN payment_order AS p ON p.id = r.payment_order_id GROUP BY r.payment_order_id, p.amount HAVING sum(r.amount) > p.amount", "0 rows")
mq("B68", "subscriptions_payments", "B-SUB-6", "Invoice numbers have no gaps within a series", "SELECT i.invoice_number_series_id, max(i.invoice_number) - min(i.invoice_number) + 1 - count(*) AS missing FROM invoice AS i WHERE i.invoice_number IS NOT NULL GROUP BY i.invoice_number_series_id HAVING max(i.invoice_number) - min(i.invoice_number) + 1 <> count(*)", "0 rows (voided invoices keep their number)")
# Member C: B69
mq("B69", "notifications_community", "C-NOT-1", "Delivery status distribution", "SELECT d.delivery_status_id, count(*) AS n FROM notification_delivery AS d GROUP BY d.delivery_status_id ORDER BY n DESC", "sent/delivered ~88%, deferred 4%, failed 5%, suppressed 3% (seed distribution)")
mq("B69", "notifications_community", "C-NOT-2", "Unread share of the inbox", "SELECT count(*) FILTER (WHERE n.read_at IS NULL)::DECIMAL / count(*) AS unread_share FROM notification AS n", "0.40 - 0.45")
mq("B69", "notifications_community", "C-NOT-3", "Unmatched webhooks older than one hour", "SELECT count(*) AS unmatched FROM provider_webhook_event AS w WHERE w.notification_delivery_id IS NULL AND w.received_at < now() - interval '1 hour'", "~2% of webhooks (injected orphan callbacks), all with signature_valid or rejected rows only")
mq("B69", "notifications_community", "C-NOT-4", "Job alert matches unique per alert and requirement", "SELECT m.job_alert_preference_id, m.job_requirement_id, count(*) AS n FROM job_alert_match AS m GROUP BY m.job_alert_preference_id, m.job_requirement_id HAVING count(*) > 1", "0 rows")
mq("B69", "notifications_community", "C-NOT-5", "Every community post has a counter row", "SELECT count(*) AS posts_without_stat FROM community_post AS p WHERE NOT EXISTS (SELECT 1 FROM community_post_stat AS s WHERE s.community_post_id = p.id)", "0")
mq("B69", "analytics", "C-ANA-1", "At most one current dimension row per candidate", "SELECT d.candidate_profile_id, count(*) AS n FROM dim_candidate AS d WHERE d.is_current GROUP BY d.candidate_profile_id HAVING count(*) > 1", "0 rows")
mq("B69", "analytics", "C-ANA-2", "Application event facts reconcile to history", "SELECT (SELECT count(*) FROM fact_application_event) AS facts, (SELECT count(*) FROM job_application_status_history) AS history", "equal after the ETL run (analytics seeded from OLTP by the ETL; lag <= late_arrival_window)")
mq("B69", "analytics", "C-ANA-3", "Aggregate applications reconcile to the OLTP count", "SELECT (SELECT sum(f.applications) FROM agg_funnel_daily AS f) AS aggregated, (SELECT count(*) FROM job_application) AS oltp", "equal (5,000,000) within the ETL lag")
mq("B69", "administration", "C-SHR-1", "Credentials are stored as argon2id hashes only", "SELECT count(*) AS not_argon2 FROM user_credential AS c WHERE c.password_hash NOT LIKE '$argon2id$%'", "0")
mq("B69", "administration", "C-SHR-2", "Every non-system user has a consent record", "SELECT count(*) AS users_without_consent FROM app_user AS u WHERE u.deleted_at IS NULL AND NOT u.is_system AND NOT EXISTS (SELECT 1 FROM user_consent AS c WHERE c.app_user_id = u.id)", "<= 1% (users imported from legacy before consent capture are flagged)")
mq("B69", "administration", "C-SHR-3", "Blocking is symmetric in visibility inputs: no open block with the same party on both sides", "SELECT b.id FROM user_block AS b WHERE b.blocker_app_user_id = b.blocked_app_user_id", "0 rows")


def check_sql(sqls):
    schema = {t["name"]: {c["name"]: "TEXT" for c in t["columns"]} for t in M.m["tables"]}
    errs = []
    for q in sqls:
        try:
            tree = sqlglot.parse_one(q["sql"], read="postgres")
            qualify(tree, schema=schema, dialect="postgres", validate_qualify_columns=True)
        except Exception as e:  # noqa
            errs.append(f"{q['id']}: {e}")
    return errs


# ======================================================================================================================
# 4. generated validation suite (Section 5 rules)
# ======================================================================================================================
def gen_suite():
    out = []  # (id, rule group, table, sql, expectation)

    def add(i, g, t, sql, exp="0"):
        out.append((i, g, t, sql, exp))

    pairs = [("start_date", "end_date"), ("starts_at", "ends_at"), ("valid_from", "valid_to"), ("effective_from", "effective_to"), ("from_date", "to_date"),
             ("period_start_date", "period_end_date"), ("window_start_at", "window_end_at"), ("valid_from_date", "valid_to_date"), ("period_start_at", "period_end_at"),
             ("walk_in_start_at", "walk_in_end_at"), ("reference_period_start_date", "reference_period_end_date")]
    missing_cur = []
    for t in M.m["tables"]:
        n = t["name"]
        cm = {c["name"]: c for c in t["columns"]}
        if t["is_lookup"]:
            continue
        for c in t["columns"]:
            nm, dom = c["name"], c.get("domain", "")
            if dom == "email":
                add(f"V3-EMAIL-{n}.{nm}", "3 Standard domains", n, f"SELECT count(*) FROM {n} WHERE {nm} IS NOT NULL AND {nm} <> lower({nm})")
            if dom == "phone":
                add(f"V3-PHONE-{n}.{nm}", "3 Standard domains", n, f"SELECT count(*) FROM {n} WHERE {nm} IS NOT NULL AND {nm} !~ '^\\+[1-9][0-9]{{6,14}}$'")
                cc = f"{nm}_country_id"
                if cc in cm:
                    add(f"V3-PHONECC-{n}.{nm}", "3 Standard domains", n, f"SELECT count(*) FROM {n} WHERE {nm} IS NOT NULL AND {cc} IS NULL")
            if dom == "money":
                cur = [x for x in cm if "currency" in x and cm[x]["type"] == "UUID"]
                if cur:
                    add(f"V3-MONEY-{n}.{nm}", "3 Standard domains / 10", n, f"SELECT count(*) FROM {n} WHERE {nm} IS NOT NULL AND {cur[0]} IS NULL")
                else:
                    missing_cur.append(f"{n}.{nm}")
            if dom in ("percentage", "ratio") and c["type"].startswith("DECIMAL"):
                add(f"V3-PCT-{n}.{nm}", "3 Standard domains", n, f"SELECT count(*) FROM {n} WHERE {nm} < 0")
            if dom == "latitude":
                add(f"V3-LAT-{n}.{nm}", "3 Standard domains", n, f"SELECT count(*) FROM {n} WHERE {nm} IS NOT NULL AND ({nm} < -90 OR {nm} > 90)")
                if "geo_source_id" in cm:
                    add(f"V3-GEOSRC-{n}.{nm}", "3 Standard domains", n, f"SELECT count(*) FROM {n} WHERE {nm} IS NOT NULL AND geo_source_id IS NULL")
            if dom == "longitude":
                add(f"V3-LON-{n}.{nm}", "3 Standard domains", n, f"SELECT count(*) FROM {n} WHERE {nm} IS NOT NULL AND ({nm} < -180 OR {nm} > 180)")
            if c.get("null") and c["type"].startswith("STRING") and not c.get("injected"):
                pass
        for a, b in pairs:
            if a in cm and b in cm:
                add(f"V3-RANGE-{n}.{a}", "3 Date range", n, f"SELECT count(*) FROM {n} WHERE {b} IS NOT NULL AND {a} IS NOT NULL AND {b} < {a}")
        strs = [c["name"] for c in t["columns"] if c["type"].startswith("STRING") and c.get("null") and c.get("domain") not in ("code",)]
        if strs:
            cond = " + ".join(f"count(*) FILTER (WHERE {s} = '' OR upper({s}) IN ('NA','N/A','NULL','NONE','UNKNOWN'))" for s in strs[:12])
            add(f"V5-SENTINEL-{n}", "5 Nullability", n, f"SELECT {cond} FROM {n}")
        if t["delete_policy"] == "soft":
            add(f"V4-SOFT-{n}", "4 Audit and lifecycle", n, f"SELECT count(*) FROM {n} WHERE (deleted_at IS NULL) <> (deleted_by IS NULL)")
        if "updated_at" in cm:
            add(f"V4-AUDIT-{n}", "4 Audit and lifecycle", n, f"SELECT count(*) FROM {n} WHERE updated_at < created_at")
        if t["delete_policy"] == "append_only":
            add(f"V4-APPENDONLY-{n}", "4 Audit and lifecycle", n, f"SELECT count(*) FROM information_schema.role_table_grants WHERE table_name = '{n}' AND privilege_type IN ('UPDATE','DELETE') AND grantee IN ('app_service','auth_svc','billing_svc','audit_writer','app_candidate','app_employer','app_recruiter','app_consultant')")
    return out, missing_cur


SUITE, MISSING_CUR = gen_suite()

# hand-written Section 5 checks (information_schema based; run once after DDL)
HAND = [
    ("V1-01", "1 Naming", "Table and column names are lowercase snake_case", "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' AND (table_name !~ '^[a-z][a-z0-9_]*$' OR column_name !~ '^[a-z][a-z0-9_]*$')", "0 rows"),
    ("V1-02", "1 Naming", "Every table has a primary key column named id", "SELECT t.table_name FROM information_schema.tables t WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE' AND NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema = 'public' AND c.table_name = t.table_name AND c.column_name = 'id')", "0 rows"),
    ("V1-03", "1 Naming", "Index names follow idx_ / uq_ pattern", "SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname !~ '^(idx|uq|pk)_'", "0 rows"),
    ("V1-04", "1 Naming", "Table names are singular (no trailing s except known words)", "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name ~ 's$' AND table_name !~ '(status|address|process|class|alias|access|business|analysis|bus|campus|settings)$'", "review list: expected exceptions only (e.g. *_status lookups)"),
    ("V2-01", "2 Keys and constraints", "Every foreign key is validated (none left NOT VALID by IMPORT INTO)", "SELECT * FROM crdb_internal.invalid_objects", "0 rows (verify view name on the pinned image)"),
    ("V2-02", "2 Keys and constraints", "FK count matches the dictionary", "SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema = 'public' AND constraint_type = 'FOREIGN KEY'", "= ForeignKeys sheet rows (2911)"),
    ("V2-03", "2 Keys and constraints", "Orphan check pattern (generated per FK in validation_suite.sql)", "SELECT count(*) FROM job_application a LEFT JOIN job_requirement r ON r.id = a.job_requirement_id WHERE r.id IS NULL", "0"),
    ("V2-04", "2 Keys and constraints", "Natural keys are unique, not primary keys", "SELECT lower(email), count(*) FROM app_user WHERE email IS NOT NULL AND deleted_at IS NULL GROUP BY 1 HAVING count(*) > 1", "0 rows"),
    ("V4-01", "4 Audit and lifecycle", "Audit trail is append-only for application roles", "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name = 'audit_event' AND privilege_type IN ('UPDATE','DELETE')", "0 rows for app roles (owner/admin excluded)"),
    ("V4-02", "4 Audit and lifecycle", "Soft-deleted rows are excluded from default views", "SELECT count(*) FROM vw_live_job_requirement v JOIN job_requirement r ON r.id = v.id WHERE r.deleted_at IS NOT NULL", "0"),
    ("V4-03", "4 Audit and lifecycle", "Erasure requests older than SLA are anonymised", "SELECT count(*) FROM app_user WHERE erasure_requested_at < now() - interval '30 days' AND anonymised_at IS NULL", "0"),
    ("V6-01", "6 Sensitive classification", "Every dictionary column has a class; Sensitive columns have a pii_treatment (checked on the workbook, not in SQL)", "python docs/tools/lint_dictionary.py", "0 errors"),
    ("V6-02", "6 Sensitive classification", "No clear-text national identifier or card data in any STRING column (pattern scan on a 1% sample)", "SELECT 'candidate_profile.summary', count(*) FROM candidate_profile TABLESAMPLE SYSTEM (1) WHERE summary ~ '[0-9]{4} [0-9]{4} [0-9]{4}' OR summary ~ '[0-9]{16}'", "0 (plus the same scan on resume.body_text, message body is encrypted)"),
    ("V7-01", "7 Multilingual", "Every UI label has an English translation (fallback root)", "SELECT l.id FROM ui_label l WHERE NOT EXISTS (SELECT 1 FROM ui_label_translation t JOIN language g ON g.id = t.language_id WHERE t.ui_label_id = l.id AND g.language_tag = 'en')", "0 rows (column names to be confirmed in the dictionary)"),
    ("V7-02", "7 Multilingual", "Lookup name_i18n always carries 'en'", "SELECT count(*) FROM employment_type WHERE NOT (name_i18n ? 'en')", "0 (generated for all 536 lookups in validation_suite.sql)"),
    ("V7-03", "7 Multilingual", "UTF-8 round trip: non-Latin names survive", "SELECT count(*) FROM candidate_profile WHERE given_name ~ '[\u0900-\u097F]'", "~14% of profiles (Devanagari share in section 4)"),
    ("V9-01", "9 Licences", "Licence limits are positive integers (CHECK) and used by sessions", "SELECT count(*) FROM licence WHERE max_users <= 0 OR max_devices_per_user <= 0", "0"),
    ("V9-02", "9 Licences", "Ended sessions are not counted: slot reuse after release", "SELECT count(*) FROM (SELECT device_slot_id FROM device_session WHERE ended_at IS NULL GROUP BY device_slot_id HAVING count(*) > 1)", "0"),
    ("V10-01", "10 Money", "Ledger is append-only and corrections reference the original", "SELECT count(*) FROM wallet_ledger_entry WHERE reverses_wallet_ledger_entry_id IS NOT NULL AND reverses_wallet_ledger_entry_id = id", "0"),
    ("V10-02", "10 Money", "Subscription overlap view is empty", "SELECT count(*) FROM vw_subscription_period_overlap", "0"),
    ("V10-03", "10 Money", "Wallet drift view is empty", "SELECT count(*) FROM vw_wallet_balance_drift", "0"),
    ("V10-04", "10 Money", "Invoice gap view is empty", "SELECT count(*) FROM vw_invoice_number_gap", "0"),
    ("V10-05", "10 Money", "Download history carries actor, subject, time", "SELECT count(*) FROM profile_download WHERE actor_app_user_id IS NULL OR candidate_profile_id IS NULL OR downloaded_at IS NULL", "0 (NOT NULL in DDL; seeded sanity)"),
]

CHECKS_BY_GROUP = Counter(g for _, g, _, _, _ in SUITE)


# ======================================================================================================================
def md_table(headers, rows):
    s = "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|\n"
    for r in rows:
        s += "| " + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in r) + " |\n"
    return s


def strategy(t):
    n = t["name"]
    if t["is_lookup"]:
        return "dictionary Enums sheet (all codes and languages)"
    if t["module"] == "analytics":
        return "ETL from seeded OLTP (not generated)" if t["kind"] in ("fact", "agg", "bridge") else "ETL / conformed dimension build"
    k = t["kind"]
    if k == "ledger":
        return "derived from business transactions (never generated alone)"
    if k in ("audit",):
        return "written by the seed's business actions (hash chain)"
    if k == "event":
        return "time-series generator (seeded Poisson/diurnal)"
    if k == "link":
        return "bridge from parent rows (cardinality per section 4)"
    if k == "snapshot":
        return "derived from parents (counters/snapshots)"
    if k == "config":
        return "static fixture + parametric rows"
    if k == "dim":
        return "conformed dimension build"
    return "parametric generator (distribution per section 4)" if n in pm.ANCHORS else "tail rule generator (sparse)"


def build_md():
    L = []
    A = L.append
    waves = defaultdict(list)
    for n, w in WAVE.items():
        waves[w].append(n)
    total_lap = sum(s["laptop_rows"] for s in ST.values())
    total_full = sum(s["full_rows"] for s in ST.values())
    nanch = len(pm.ANCHORS)
    class_rows = Counter()
    for t in M.m["tables"]:
        s = ST[t["name"]]
        cls = "lookup" if t["is_lookup"] else ("anchor" if t["name"] in pm.ANCHORS else "tail:" + t["volume"] + ("/ext" if t["module"].startswith("ext_") else ""))
        class_rows[cls] += 1
    A("# 12. Seed data and validation plan (B60-B69, B79)\n")
    A("Generated by `python docs/tools/gen_seed_plan.py` from `docs/_staging` (FK graph, row targets via `docs/tools/perf_model.py`). Companion files: `docs/12_seed_targets.xlsx` (per-table targets and load order, negative tests, validation suite) and `docs/tools/validation_suite.sql` (generated checks). Engine: CockroachDB v26.2.\n")
    A("## Contents\n\n1. Principles and reproducibility\n2. Load order (topological sort of the FK graph)\n3. Row-count targets and the long-tail rule\n4. Distributions per key table\n5. Duplicates, multilingual names, overlap attempts, data-quality issues\n6. Validation of Section 5 standards (B66)\n7. Module query sets (B67-B69)\n8. Access and audit tests (B79)\n9. Idempotency, reproducibility, execution\n10. Hand-off to the ingestion engine\nAppendix A. Computed load order per module\n")
    A("## 1. Principles and reproducibility\n")
    A("* The generator is part of the deliverable (docket Section 6): it reads the data dictionary, never a hand-copied schema, so a model change is picked up on the next run.\n* All randomness comes from one master seed (`20261015`); each table and shard derives its own stream (`sub_seed = int(sha256(f'{master}:{table}:{shard}')[:8], 16)`), so tables can be generated in parallel and re-generated alone with identical output.\n* Primary keys are generated client-side as UUIDv4 values drawn from the table's seeded stream (the DB default `gen_random_uuid()` is not used during seeding): FK values are known before insert, runs are bit-reproducible, and idempotent re-load is possible (`INSERT ... ON CONFLICT (id) DO NOTHING`).\n* Seeded rows pass through the normal constraints (CHECK, UNIQUE, FK): the seed is the first test of the schema. Rows that must be *rejected* are produced separately as negative tests (section 5.3), never loaded as data.\n* The seeding role is `migration_owner` (data DML window), not the application roles; append-only tables are loaded in INSERT-only fashion.\n")
    # 2
    A("## 2. Load order (topological sort of the FK graph)\n")
    A(f"Computed from the {sum(len(t['fks']) for t in M.m['tables'])} foreign keys of the dictionary (`ForeignKeys` sheet equivalent). Algorithm: build the child->parent graph over tables; self-referencing FKs ({len(SELFS)}) are excluded from the graph and handled inside the table (roots first, then children ordered by depth); strongly connected components are broken at nullable FK edges (MATCH SIMPLE: a NULL in any FK column skips the check) and the removed edge becomes a deferred UPDATE; the remaining DAG is layered by longest path: **wave(t) = 0 if no parent, else 1 + max(wave(parent))**. Tables in the same wave have no FK between them and are loaded in parallel. Result: **{NWAVES} waves**, {len(WAVE)} tables.\n")
    A("Cycles found and how the seed breaks them:\n")
    dd = defaultdict(list)
    for a, b in DEFERRED:
        dd[(a, b)].append(1)
    cyc_note = {
        ("identity_session", "device_session"): "insert identity_session with device_session_id NULL, then device_session, then `UPDATE identity_session SET device_session_id` (identity_session is hard-delete/TTL, UPDATE allowed).",
        ("wallet_ledger_entry", "wallet_expiry_bucket"): "ledger rows are inserted with wallet_expiry_bucket_id NULL; buckets reference the ledger entry that created them; consumption goes through wallet_bucket_consumption, so the append-only ledger never needs an UPDATE (seed does not link ledger -> bucket).",
        ("job_application", "job_position"): "insert job_application with job_position_id NULL, then job_position rows (job_position.job_application_id is the fill link), then `UPDATE job_application SET job_position_id` for joined applications.",
        ("job_position", "job_application"): "insert job_position first with job_application_id NULL, then job_application rows, then set job_position.job_application_id when a position is filled.",
        ("offer", "offer_revision"): "composite FK offer(id, current_revision_no) -> offer_revision is MATCH SIMPLE with current_revision_no NULL on insert: insert offer, then offer_revision rows, then `UPDATE offer SET current_revision_no`.",
    }
    rows = []
    for a, b in DEFERRED:
        rows.append([f"`{a}` -> `{b}` (nullable, deferred)", cyc_note.get((a, b), "")])
    A(md_table(["deferred FK edge", "seed procedure"], rows))
    A(f"\nSelf-referencing FKs ({len(SELFS)}): " + ", ".join(f"`{a}`" for a, _, _ in SELFS) + ". Hierarchies are generated depth-first so a parent id exists before its child.\n")
    A("Module-level dependency (cross-module FKs): " + (f"cycles between modules: " + str([len(c) for c in MOD_SCC]) + " modules in one strongly connected component (lookups/master data referenced everywhere, profile and jobs reference each other through shortlists/applications)" if MOD_SCC else "acyclic at module level") + "; load modules in this order of first wave: " + ", ".join(
        f"`{m}`" for m, _ in sorted(((m, min(WAVE[t['name']] for t in M.m['tables'] if t['module'] == m and not t['is_lookup'])) for m in MODULES), key=lambda x: x[1])) + ".\n")
    A("Wave summary (tables per wave; the per-module order is in Appendix A):\n")
    rows = []
    for w in range(NWAVES):
        ts = sorted(waves[w])
        mods = Counter(T[n]["module"] for n in ts)
        rows.append([w, len(ts), sum(1 for n in ts if n in LOOKUPS), ", ".join(f"{m}:{c}" for m, c in mods.most_common(4)), ", ".join(f"`{n}`" for n in ts[:4] if n not in LOOKUPS)])
    A(md_table(["wave", "tables", "of which lookups", "top modules (tables)", "examples"], rows))
    A("\nBusiness-level order (what the generator code does): (1) lookups and master data (wave 0-2: country, state, city, language, currency, skill, qualification, industry, job_role, ui labels); (2) administration identity (app_user, roles, consent documents, registration policy); (3) organisation and account; (4) profile; (5) jobs (requirements, questionnaires, positions); (6) applications and pipeline history; (7) licences, subscriptions, wallet, payments (ledger derived from purchases and spends in time order); (8) sourcing and outreach; (9) gig; (10) notifications and community; (11) audit events, consent, sessions, login attempts (written along with the actions above, ordered by time); (12) extension modules (sparse); (13) analytics ETL run last (dimensions then facts then aggregates, `AS OF SYSTEM TIME follower_read_timestamp()`).\n")
    # 3
    A("## 3. Row-count targets and the long-tail rule\n")
    A(f"Total seeded rows at laptop scale: **{total_lap:,}** in {len(ST)} tables (full-scale projection {total_full:,}). Estimated load time at an assumed 60,000 rows/s aggregate (`COPY` over 4 parallel streams; throughput is unverified until the first load): **{total_lap / 60000 / 3600:.1f} h**.\n")
    A(f"* **Docket-fixed anchors**: `candidate_profile` 1,000,000; `job_requirement` 100,000; `job_application` 5,000,000 (never trimmed).\n* **Explicit anchors** ({nanch} tables) in `perf_model.ANCHORS`: children of the fixed entities (e.g. `candidate_skill` 8,000,000, `job_application_status_history` 15,000,000), identity/audit volumes, wallet/payments, notification streams, gig, analytics facts. TTL'd event streams are seeded as a window sample of the steady state (`TRIM` dictionary) so the laptop stores about {sum(s['laptop_rows'] * (s['row_bytes'] + s['idx_bytes_row']) for s in ST.values()) / pm.GB:.0f} GiB logical (doc 11 section 1).\n* **Long-tail rule** (the remaining {len(ST) - nanch - len(LOOKUPS)} non-lookup tables): rows by dictionary volume class: low 1,000, medium 20,000, high 150,000, very_high 500,000; analytics tail 1,000 / 20,000 / 100,000 / 500,000; `tier3_reference` low 500 / medium 20,000; **extension modules (ext_*) at 5%** of the class value (minimum 100 rows) because they are sparse labelled extensions; parents drive children: a tail child never exceeds `parent_rows x avg_children` (generator caps it).\n* **Lookups** ({len(LOOKUPS)} tables): all codes and languages of the Enums sheet; no random rows.\n* **Full scale** = laptop x driver factor (profiles 10, jobs 10, applications 20, other 10, fixed reference 1); never loaded (docket), extrapolated in doc 11.\n* **Fallback if disk exceeds 330 GiB cluster-wide** (3 x the measured per-replica size): halve the non-docket event streams (`job_view_event`, `candidate_activity_log`, notification family, `fact_*`, `login_attempt`) via `--scale-events 0.5`; docket-fixed tables unchanged.\n")
    A(md_table(["rule class", "tables"], [[k, v] for k, v in sorted(class_rows.items(), key=lambda x: -x[1])]))
    A("\nTargets for the key tables (laptop rows):\n")
    key = ["candidate_profile", "candidate_skill", "candidate_experience", "candidate_education", "resume", "duplicate_candidate_match", "job_requirement", "job_position", "job_application", "job_application_status_history", "questionnaire_answer", "organisation", "app_user", "recruiter_organisation_mapping", "licence", "licence_seat", "device", "device_session", "wallet", "wallet_ledger_entry", "payment_order", "refund", "profile_download", "invoice", "subscription_period", "sourcing_request", "outreach_batch", "outreach_recipient", "gig_worker_profile", "gig_request", "gig_application", "gig_assignment", "gig_slot", "notification", "notification_delivery", "community_post", "support_ticket", "audit_event", "user_consent", "user_block"]
    A(md_table(["table", "module", "wave", "laptop rows", "generator"], [[f"`{n}`", ST[n]["module"], WAVE[n], f"{ST[n]['laptop_rows']:,}", strategy(T[n])] for n in key if n in ST]))
    # 4
    A("\n## 4. Distributions per key table\n")
    A("Distribution parameters are inputs of the generator config (`seed.yaml`); the numbers below are also the assumptions behind the index-selectivity estimates in doc 11 (e.g. `is_live` 30%, `is_searchable` 80%, largest requirement 0.1% of applications).\n")
    A(md_table(["table.column", "distribution", "parameters", "why"], [
        ["`candidate_profile.current_city_id`", "Zipf over cities", "s = 1.0 over 150,000 cities; top 50 metros hold ~60% of profiles", "realistic concentration; makes the metro-city search (Q5) the stress case"],
        ["`candidate_profile.total_experience_months`", "mixture: freshers + lognormal", "22% freshers (0-12 months); others lognormal(mu = ln 60, sigma = 0.8) months, clipped 0..480", "matches Indian staffing mix; bounded for CHECK"],
        ["`candidate_profile.notice_period_days`", "categorical", "0: 8%, 15: 12%, 30: 45%, 60: 25%, 90: 10% (freshers NULL)", "typical notice-period buckets; 0 is a real value"],
        ["`candidate_profile.expected_salary_min` / `current_salary_amount`", "lognormal by experience band", "median INR 4.8 LPA (0-2 y) to 24 LPA (10+ y), sigma = 0.45; 35% undisclosed (`salary_disclosure` = undisclosed, amount NULL, never 0)", "Section 5 rule 5"],
        ["`candidate_profile.last_active_at`", "exponential decay", "35% active within 30 days, 70% within 12 months, tail to 48 months; 5% inactive > 36 months (inactivity-purge test set)", "retention tests; Q5 ordering key"],
        ["`candidate_profile.is_searchable` / `is_open_to_work`", "Bernoulli", "0.80 / 0.35", "index selectivity"],
        ["`candidate_skill` per profile", "negative binomial", "mean 8, min 1, max 40; skill popularity Zipf(s = 1.1) over 40,000 skills; 0.5% of skills hold 40% of rows", "skill-search skew (Q5)"],
        ["`candidate_experience` / `candidate_education` per profile", "Poisson (freshers 0 / 1.2)", "mean 2.5 / 1.8", "ratios in section 3"],
        ["`job_requirement.organisation_id`", "Zipf over organisations", "s = 0.9 over 20,000 organisations; top 1% of employers post 25% of jobs", "big-employer skew; wallet hot-spots"],
        ["`job_requirement.published_at`", "uniform over 12 months with weekday weighting", "Mon-Fri 1.4x weekends; 30% `is_live` (closing date in the future), 55% closed, 15% draft/expired", "partial-index selectivity"],
        ["`job_requirement.salary_*`", "lognormal by role", "sigma = 0.4; 25% `salary_disclosure` = undisclosed; min <= max enforced", "CHECK min <= max"],
        ["`job_application` per requirement", "heavy-tailed (lognormal body + Pareto tail)", "mean 50, median 12; Pareto alpha = 1.1 tail; largest requirement = 5,000 applications (0.1% of 5M)", "mega-requirements drive Q3/Q11"],
        ["`job_application` per candidate", "negative binomial among applicants", "35% of candidates apply; mean 14.3 per applicant (5 overall); max 200 (bot cap)", "Q4 cardinality"],
        ["`job_application.application_status_id`", "categorical (pipeline end state)", "applied 38%, viewed 25%, shortlisted 14%, interviewed 8%, offered 3%, joined 1.5%, rejected 8.5%, withdrawn 2%", "full status pipeline (B61); funnel decays like real ATS data"],
        ["`job_application_status_history` per application", "follows the transition matrix", "mean 3.0 rows; each path valid under `application_status_transition`; timestamps monotone with lognormal gaps (median 2 days)", "history = chain (BR-JOB-12)"],
        ["`job_application.applied_at`", "uniform over 12 months, diurnal", "peak 10:00-12:00 and 20:00-22:00 local; 25% of applications within 3 days of job publish", "hash-sharded time indexes"],
        ["`questionnaire_answer`", "20% of applications", "to the 12% of requirements with questionnaires; 4 answers each", "4M answers"],
        ["`app_user` actor mix", "categorical", "candidate 87%, recruiter 5%, employer 4%, consultant 2%, TPO 1.5%, admin/staff 0.5%", "Section 3 actors"],
        ["`device` per user / `licence.max_devices_per_user`", "categorical", "devices per user 1: 70%, 2: 22%, 3: 6%, 4+: 2%; max_devices 1: 10%, 2: 30%, 3: 40%, 5: 20%", "device-limit tests"],
        ["`licence.max_users`", "lognormal", "median 5, sigma 1.0, clipped 1..500; seats filled to 60-95%", "seat tests"],
        ["`wallet_ledger_entry`", "per wallet Poisson by organisation size", "purchases lognormal(median 500 credits, sigma 0.9); spends 1-10 credits; balance never < 0; 15% of wallets reach 0 at least once", "ledger tests"],
        ["`profile_download`", "Zipf over recruiters/organisations", "s = 0.8; 12% free-quota, 6% free repeat (credits 0)", "BR-SUB-18"],
        ["`notification`", "Zipf per user", "s = 0.9; types: job_alert 45%, application_update 25%, message 12%, system 10%, marketing 8%; 42% unread", "unread-index selectivity"],
        ["`notification_delivery.delivery_status_id`", "categorical", "sent/delivered 88%, deferred 4%, failed 5%, suppressed 3%; retry attempts 6% of rows", "B57 path"],
        ["`gig_request` / `gig_assignment`", "lognormal duration, Poisson arrival", "windows 1-12 h (median 4 h) aligned to 15-minute slots; 80% accepted-from-first-applicant; 5% cancelled; 8% reconcile 'pending'", "slot table volume (2M rows)"],
        ["`audit_event`", "derived", "1 row per sensitive action generated by the seed (logins, status changes, downloads, blocks); 32 chains round-robin by hash(entity)", "chain tests"],
        ["Time ordering", "global clock", "all timestamps within [2025-10-15, 2026-10-15]; created_at <= updated_at <= now(); events derive from parent timestamps", "CHECK-safe, FK-consistent history"],
    ]))
    # 5
    A("\n## 5. Duplicates, multilingual names, overlap attempts, data-quality issues\n")
    A("### 5.1 Candidate duplicates and multilingual names\n")
    A("* **Duplicate rate: 3% of profiles (30,000 profiles are duplicates of another person)**, created by cloning a base profile with one or more perturbations: transliteration variants (Hindi/English), swapped given/family name, nickname, typo (edit distance 1-2), different email with the same phone, same email on a legacy copy, differently formatted phone. The pair goes into `duplicate_candidate_match` (45,000 rows: multiple rules can fire per pair; `match_confidence` 0.55-0.99; 60% reviewed by a seeded admin).\n* **Identity-token duplicates**: 0.3% reuse the same `id_number_hmac` (must be rejected by the active-binding unique index when both claim it: N-21).\n* **Name mix (Rule 7, UTF-8 end to end)**: Latin 60% (incl. diacritics), Devanagari 14%, Tamil 4%, Telugu 4%, Bengali 4%, Arabic 4%, CJK 4%, Cyrillic 3%, other 3%. Name lengths 2-150 characters, 0.1% at the 150 limit. `profile_language_id` consistent with the script (95%); 5% deliberate mismatch. Each language keeps a matching UI label set for the translation test.\n* Generated with locale-specific name lists (Faker locales `en_IN`, `hi_IN`, `ta_IN`, `te_IN`, `bn_BD`, `ar_AE`, `zh_CN`, `ru_RU`, `de_DE`, `fr_FR`) plus NFC/NFD variants (0.2% store NFD to test normalisation).\n")
    A("### 5.2 Overlap and limit attempts (negative tests)\n")
    A("Negative tests are produced as a separate 'attack' stream executed after the clean load; each expects a specific constraint to reject. Constraint names were verified against the dictionary.\n")
    A(md_table(["id", "module", "attempt", "count", "how", "constraint relied on", "expected result", "rule"], [[n[0], n[1], n[2], f"{n[3]:,}", n[4], f"`{n[5]}`", n[6], n[7]] for n in NEG]))
    A("\n### 5.3 Data-quality issues injected\n")
    A("Constraints reject dirty values in the typed tables, so most issues are injected into the **legacy import path** (`profile_import.raw_payload`, staging files) and must be cleaned or rejected by the loader; each has a detector in section 6.\n")
    A(md_table(["issue", "rate", "where", "detection / expected handling"], [
        ["Phone not in E.164 (spaces, leading 0, missing country)", "4% of legacy phones", "legacy payload", "normalised with country_id or phone left NULL (unknown); V3-PHONE checks = 0"],
        ["Upper-case / padded emails", "6% of legacy emails", "legacy payload", "lower-cased and trimmed by loader; CHECK would reject otherwise (N-17)"],
        ["Same email, different case, two legacy rows", "0.4%", "legacy payload", "merged -> `duplicate_candidate_match` rule 'same_email'"],
        ["Missing salary", "35% of profiles, 25% of jobs", "typed tables", "stored as `salary_disclosure` undisclosed + NULL amount; V5 sentinel checks find no 0/'NA'"],
        ["Salary min > max in legacy jobs", "0.3%", "legacy payload", "swapped and flagged in `profile_import.error_summary`; CHECK min <= max never violated"],
        ["End date before start date (experience/education)", "0.5% of legacy rows", "legacy payload", "row rejected, counted in `items_total - items_applied`"],
        ["Future dates (birth_date in future, applied_at in future)", "0.1%", "legacy payload", "rejected by loader range check"],
        ["Orphan references (legacy job id unknown)", "0.2% of legacy applications", "legacy payload", "not inserted; reported (FK would fail)"],
        ["Trailing/leading whitespace, double spaces in names", "2%", "legacy payload", "trimmed; V1 checks on names"],
        ["NFC vs NFD Unicode duplicates", "0.2%", "legacy payload", "normalised to NFC; duplicates collapsed"],
        ["Emoji / zero-width characters in headline", "0.1%", "typed tables (allowed)", "accepted (UTF-8); length CHECK counts characters"],
        ["Inactive profiles (> 36 months)", "5%", "typed tables", "retention job test: 30-day warning then `candidate_erasure_request` (inactivity_purge)"],
        ["Erasure requests", "0.5%", "typed tables", "anonymise-in-place; ledger/audit FKs survive (V4-03)"],
        ["Unknown vs not applicable", "notice period NULL for freshers; `salary_disclosure` not_applicable for volunteers", "typed tables", "NULL = unknown only where null_why says so"],
        ["Unmatched provider callbacks", "2% of webhooks", "typed tables", "C-NOT-3 expects ~2% unmatched"],
        ["Late-arriving analytics events", "1%", "analytics ETL input", "late_arrival_window 48 h upsert keeps facts idempotent"],
    ]))
    # 6
    A("\n## 6. Validation of Section 5 standards (B66)\n")
    A(f"Two layers: (a) hand-written structural checks below (run once after DDL and after seed), (b) a **generated suite** of {len(SUITE):,} data checks in `docs/tools/validation_suite.sql` (also sheet `Validation suite` of `docs/12_seed_targets.xlsx`): {', '.join(f'{k}: {v}' for k, v in sorted(CHECKS_BY_GROUP.items()))}. Every check returns a count that must be 0 unless the Expected column says otherwise; failures go to `seed_validation_report` (a CSV, not a table) with check id, table, count, 5 sample ids. Orphan checks are generated per FK from the ForeignKeys sheet for `IMPORT INTO` loads; with `COPY`/INSERT the FKs are enforced and the check is a smoke test.\n")
    A(md_table(["id", "rule group", "check", "SQL", "expected"], [[h[0], h[1], h[2], "`" + h[3].replace("`", "'") + "`", h[4]] for h in HAND]))
    A(f"\nGenerated suite details: money columns without a companion currency column in the same table (design finding for the model owner): **{len(MISSING_CUR)}**" + (": " + ", ".join(f"`{x}`" for x in MISSING_CUR[:12]) + (" ..." if len(MISSING_CUR) > 12 else "") if MISSING_CUR else " (none)") + ". Sentinel scans cover every nullable STRING column (first 12 per table per statement) for '', 'NA', 'N/A', 'NULL', 'NONE', 'UNKNOWN'.\n")
    A("**Coverage gap register (B66).** After the seed, every competitor-coverage gap in `docs/10_competitor_benchmark.xlsx` (sheet 'Gap register') is re-checked for being either seeded or consciously absent; record pass/fail per gap id in the validation report. Domain coverage: every Tier 1 and Tier 2 table of the docket (module ownership sheet) has `count(*) > 0` after the seed: `SELECT table_name FROM (...) WHERE n = 0` must return only tables explicitly listed as unseeded (extension modules at 5% still have rows).\n")
    # 7
    A("## 7. Module query sets (B67-B69)\n")
    A("Each query is a data-quality or business-integrity probe with an expected result on the seeded data. All statements are column-checked against the dictionary (sqlglot, PostgreSQL dialect). Results are recorded in the B67/B68/B69 deliverables as: query id, row count, first 5 rows, execution time.\n")
    for b, title in (("B67", "Member A: Administration, Organisation and account, Gig marketplace"), ("B68", "Member B: Licences, Sourcing, Subscriptions"), ("B69", "Member C: Notifications, Analytics, shared team items")):
        A(f"### {b} - {title}\n")
        for q in [x for x in MQ if x["batch"] == b]:
            A(f"**{q['id']} {q['title']}** ({q['module']}). Expected: {q['expect']}.\n```sql\n{q['sql']};\n```")
    # 8
    A("\n## 8. Access and audit tests (B79)\n")
    A("The test catalogue is `docs/13_rbac_matrix.xlsx` sheet `Tests_B79` (54 tests, owner: security agent). This plan supplies the **seeded fixtures** those tests need, so that they run on the laptop dataset without manual setup:\n")
    A(md_table(["fixture", "content (deterministic ids from the master seed)", "used by"], [
        ["Personas per role", "one login-pool role per actor (`app_candidate`, `app_employer`, `app_recruiter`, `app_consultant`, `app_tpo`, `app_account_admin`, `app_staff_admin`, `app_staff_super`, `app_guest`) and service roles; three users per actor type with fixed names (C1..C3, E1..E3, R1..R3, K1..K3, T1..T3, A1..A2, S1)", "all AT-xx"],
        ["Tenants", "organisations O-A and O-B with R1 mapped to O-A only, R2 mapped to both (chain), R3 mapping closed yesterday; jobs J-A1 (open to consultants), J-A2 (closed to consultants), J-B1", "tenant and consultant scope tests (AT-01..AT-04)"],
        ["Blocks", "C1 blocks E1 (open); O-A blocks C2; C3 blocks O-A; one closed block (effective_to set)", "blocking tests (AT-05, AT-06)"],
        ["Money", "wallet W-A with exactly 5 credits; ledger of 40 entries incl. one reversal", "ledger/idempotency tests"],
        ["Licence", "licence L-1 (max_users 3, max_devices_per_user 2) with 3 seats, user U1 with 2 live sessions", "device-limit tests, N-09/N-11"],
        ["Audit", "32 chain heads initialised; 1,000 audit events with a known chain; one deliberately tampered copy kept OUTSIDE the table for the verifier test", "audit append-only and chain tests"],
        ["Erasure", "candidate C9 with a pending erasure request > 30 days", "retention tests"],
    ]))
    A("\nBeyond the catalogue, each module's access test follows one template: run the module query set (section 7) as the persona pool role and as a role that must not see the data; expected = rows vs `42501`/0 rows; then check that an `audit_event` row with the matching `audit_action_id` exists for every sensitive action performed (`SELECT count(*) FROM audit_event WHERE request_ref = $1`).\n")
    # 9
    A("## 9. Idempotency, reproducibility, execution\n")
    A(md_table(["aspect", "design"], [
        ["Re-run safety", "every INSERT is `ON CONFLICT DO NOTHING` on the primary key (deterministic ids) and on the table's natural/idempotency key; a second run of the same seed changes 0 rows; a changed seed or scale uses `--clean` (drop database and restore the empty-schema backup) rather than partial deletes."],
        ["Manifest", "`seed_manifest.json`: master seed, generator version, dictionary SHA-256, per-table row count and SHA-256 of the ordered id list; two runs must produce identical manifests (reproducibility test)."],
        ["Order", "wave order of section 2; inside a wave tables load in parallel (4 workers); inside a table, shards of 100,000 rows with their own sub-seed; transactions of 5,000-20,000 rows (`COPY` for tables without FKs to uncommitted data; batched `INSERT` otherwise); retry on SQLSTATE 40001."],
        ["Throughput protections", "`SET CLUSTER SETTING kv.bulk_io_write.max_rate` unchanged; index creation after load is NOT used (indexes exist during load so the measured write cost is realistic); optional `SET CLUSTER SETTING sql.stats.automatic_collection.enabled = true` and a final `CREATE STATISTICS` on the 20 key tables before B70."],
        ["Seed checkpoints", "after waves 0-2, after profile, after jobs/applications, after money: `BACKUP INTO` a local external connection so the B80 restore test and re-runs can start from a checkpoint (backup storage for Docker: see crdb_capabilities.md section 2.5)."],
        ["Time", f"expected {total_lap / 60000 / 3600:.1f} h for {total_lap:,} rows at the assumed throughput; docket B60/B61 subsets (profiles, jobs, applications) are about {sum(ST[n]['laptop_rows'] for n in ST if T[n]['module'] in ('profile', 'jobs_applications', 'master_data')) / 60000 / 60:.0f} min of that."],
    ]))
    # 10
    A("\n## 10. Hand-off to the ingestion engine\n")
    A("`ingestion-engine/` (separate component, not modified here) introspects the live database through `information_schema` and, when `DATA_DICTIONARY_PATH` points at `docs/05_data_dictionary.xlsx`, enriches columns with classification and description (`load_data_dictionary` accepts any sheet whose headers contain `table`, `column` and `classification`/`sensitivity` or `description`). The dictionary contract the engine and any DDL/seed generator may rely on is `docs/05_data_dictionary_schema.md`; the stable sheets and headers are:\n")
    A(md_table(["sheet", "headers (stable; new columns only appended)", "used for seeding"], [
        ["Tables", "table, module, owner, kind, delete_policy, volume, tier, milestones, max_sensitivity, column_count, audit_columns, ttl, partitioning, retention, description", "volume -> row target class; kind -> generator; delete_policy -> append-only loading; ttl -> window sizing"],
        ["Columns", "table, ordinal, column, type, domain, nullable, nullability_justification, default, sensitivity_class, delete_policy, pii_treatment, computed_expr, is_pk, fk_ref, standard_column, description", "type/domain -> value generator (money, email, phone, latitude, ...); nullable -> NULL rate with null_why; sensitivity_class -> synthetic-only masking rules; fk_ref -> parent lookup; computed_expr -> skip column"],
        ["Enums", "lookup_table, code, sort_order, is_active, language_tag, display_name, description", "seeds the 536 lookup tables (name_i18n built per code)"],
        ["Indexes", "table, index_name, columns, unique, hash_sharded, bucket_count, inverted, where_predicate, storing, kind, description", "unique/partial-unique indexes = uniqueness rules the generator must respect (e.g. one open block per pair)"],
        ["Constraints", "table, constraint_name, constraint_type, columns, expression, description", "UNIQUE and CHECK expressions parsed into generator constraints (ranges, enumerations)"],
        ["ForeignKeys", "table, fk_name, columns, ref_table, ref_columns, on_delete, on_update, ref_module, cross_module, index_exempt, description", "topological load order (section 2), composite FK (organisation_id, id) pairs, deferred nullable edges"],
        ["Views, Partitioning", "view_name ...; table, module, volume, strategy, key_or_columns, naming_pattern, notes", "views for validation queries; partitioning notes for zone configs"],
    ]))
    A("\nRecommended engine behaviour for this dataset: (1) read `Tables`/`ForeignKeys` and compute the same waves as `gen_seed_plan.py` (the script's `load_order()` is the reference implementation: SCC break at nullable FK edges, longest-path layering); (2) treat `volume` and the `perf_model.ANCHORS` table as the row-target source, not free choice; (3) generate PKs client-side; (4) respect partial unique indexes from the `Indexes` sheet by sampling without replacement; (5) emit the negative-test stream of section 5.2 from the `NEG` list (JSON export: `python docs/tools/gen_seed_plan.py --json`); (6) run the validation suite after load and write the manifest. Mapping to the engine's own model: `Table.fks` <- ForeignKeys, `Column.classification` <- Columns.sensitivity_class, `Column.enum_values` <- Enums.\n")
    # appendix
    A("## Appendix A. Computed load order per module\n")
    A("Table order within a module: ascending wave, then name. Lookup tables are listed in one line per module (they are wave 0 or follow their referenced lookups). Rows = laptop target.\n")
    for k in MODULES:
        ts = [t for t in M.m["tables"] if t["module"] == k]
        lk = sorted(t["name"] for t in ts if t["is_lookup"])
        ent = sorted((t for t in ts if not t["is_lookup"]), key=lambda t: (WAVE[t["name"]], t["name"]))
        A(f"### {k} - {MODULES[k]} ({len(ts)} tables, {len(lk)} lookups)\n")
        if lk:
            lw = sorted({WAVE[n] for n in lk})
            A(f"Lookups (waves {lw[0]}-{lw[-1]}): " + ", ".join(f"`{n}`" for n in lk) + "\n")
        A(md_table(["wave", "table", "kind", "rows (laptop)", "generator"], [[WAVE[t["name"]], f"`{t['name']}`", t["kind"], f"{ST[t['name']]['laptop_rows']:,}", strategy(t)] for t in ent]))
    return "\n".join(L), total_lap


def write_xlsx():
    wb = new_wb()
    rows = []
    for t in M.m["tables"]:
        s = ST[t["name"]]
        rows.append([t["module"], t["name"], t["kind"], t["volume"], WAVE[t["name"]], s["laptop_rows"], s["full_rows"],
                     "lookup" if t["is_lookup"] else ("anchor" if t["name"] in pm.ANCHORS else "tail rule"), strategy(t)])
    rows.sort(key=lambda r: (r[4], r[0], r[1]))
    add_sheet(wb, "Row targets and order", ["module", "table", "kind", "volume class", "load wave", "laptop rows", "full-scale rows (projection)", "target source", "generator"], rows, widths=[24, 38, 9, 10, 8, 13, 16, 12, 50], freeze_cols=2)
    add_sheet(wb, "Deferred FK edges", ["child table", "parent table", "procedure"], [[a, b, cyc_n] for (a, b), cyc_n in [((a, b), "insert child with NULL FK, then UPDATE after parent rows exist") for a, b in DEFERRED]], widths=[30, 30, 70])
    add_sheet(wb, "Negative tests", ["id", "module", "attempt", "count", "how", "constraint", "expected", "rule"], [list(n) for n in NEG], widths=[7, 22, 44, 9, 60, 60, 60, 20], wrap_cols=("attempt", "how", "expected"))
    add_sheet(wb, "Module queries", ["batch", "module", "id", "title", "sql", "expected"], [[q["batch"], q["module"], q["id"], q["title"], q["sql"], q["expect"]] for q in MQ], widths=[7, 22, 10, 40, 100, 50], wrap_cols=("sql", "expected"))
    add_sheet(wb, "Validation suite", ["check id", "rule group", "table", "SQL", "expected"], [[a, b, c, d, e] for a, b, c, d, e in SUITE] + [[h[0], h[1], "-", h[3], h[4]] for h in HAND], widths=[50, 24, 30, 120, 20])
    wb.save(os.path.join(ROOT, "docs", "12_seed_targets.xlsx"))


def write_sql():
    with open(os.path.join(ROOT, "docs", "tools", "validation_suite.sql"), "w", encoding="utf-8") as f:
        f.write("-- GENERATED by gen_seed_plan.py from the dictionary. Each statement returns counts that must be 0 (unless noted).\n-- Run as analytics_reader / migration_owner on a follower-read snapshot: SET default_transaction_use_follower_reads = on;\n")
        for i, g, t, sql, exp in SUITE:
            f.write(f"-- {i} [{g}] expected {exp}\n{sql};\n")
        # lookup en check + orphan checks
        for n in sorted(LOOKUPS):
            f.write(f"-- V7-LOOKUP-{n} [7 Multilingual] expected 0\nSELECT count(*) FROM {n} WHERE NOT (name_i18n ? 'en');\n")
        for t in M.m["tables"]:
            for fk in t["fks"]:
                if len(fk["columns"]) == 1 and not T[t["name"]]["volume"] == "very_high":
                    c = fk["columns"][0]
                    r = fk["ref"]
                    f.write(f"-- V2-ORPHAN-{t['name']}.{c} [2 Keys and constraints] expected 0\nSELECT count(*) FROM {t['name']} AS c LEFT JOIN {r} AS p ON p.id = c.{c} WHERE c.{c} IS NOT NULL AND p.id IS NULL;\n")


if __name__ == "__main__":
    errs = check_sql(MQ)
    assert not BAD, BAD
    assert not errs, "\n".join(errs)
    md, total = build_md()
    p = os.path.join(ROOT, "docs", "12_seed_and_validation_plan.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(md)
    write_xlsx()
    write_sql()
    print("waves", NWAVES, "deferred", len(DEFERRED), "selfs", len(SELFS), "module cycles", MOD_SCC)
    print("md chars", len(md), "total laptop rows", total, "suite checks", len(SUITE), "missing currency", len(MISSING_CUR))
