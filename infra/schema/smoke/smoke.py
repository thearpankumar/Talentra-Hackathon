"""Phase-1 smoke test of risky CockroachDB mechanisms on a real cluster.
Usage: python smoke.py [host] [port]   (insecure local cluster, scratch db 'smoke')
Prints a markdown results table to stdout."""
import sys, time, threading, uuid
import psycopg

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 26267


def conn(user="root", db="smoke", autocommit=True):
    return psycopg.connect(f"postgresql://{user}@{HOST}:{PORT}/{db}?sslmode=disable", autocommit=autocommit)


results = []


def test(name):
    def deco(fn):
        t = time.time()
        try:
            note = fn() or ""
            results.append((name, "PASS", note, time.time() - t))
        except Exception as e:  # noqa
            results.append((name, "FAIL", f"{type(e).__name__}: {str(e).splitlines()[0][:230]}", time.time() - t))
        return fn
    return deco


adm = conn(db="defaultdb")
adm.execute("DROP DATABASE IF EXISTS smoke CASCADE")
adm.execute("CREATE DATABASE smoke")
c = conn()
c.execute("CREATE ROLE IF NOT EXISTS sm_app NOLOGIN")
c.execute("CREATE ROLE IF NOT EXISTS sm_ro NOLOGIN")
c.execute("CREATE ROLE IF NOT EXISTS sm_login LOGIN")
c.execute("GRANT sm_app TO sm_login")
c.execute("GRANT sm_ro TO sm_login")
c.execute("GRANT ALL ON DATABASE smoke TO sm_app, sm_ro")


@test("custom session variable SET app.user_id / current_setting")
def _():
    x = conn()
    x.execute("SET app.user_id = 'abc'")
    v = x.execute("SELECT current_setting('app.user_id', true)").fetchone()[0]
    assert v == 'abc', v
    v2 = x.execute("SELECT current_setting('app.nope', true)").fetchone()[0]
    return f"value ok; missing_ok returns {v2!r}"


@test("SET LOCAL app.x inside txn reverts at commit; set_config(...,true)")
def _():
    x = conn(autocommit=False)
    x.execute("SET LOCAL app.viewer_id = 'u1'")
    v = x.execute("SELECT current_setting('app.viewer_id', true)").fetchone()[0]
    x.execute("SELECT set_config('app.org_id','o1',true)")
    v2 = x.execute("SELECT current_setting('app.org_id', true)").fetchone()[0]
    x.commit()
    after = x.execute("SELECT current_setting('app.viewer_id', true)").fetchone()[0]
    assert v == 'u1' and v2 == 'o1'
    return f"inside={v}/{v2}, after commit={after!r}"


@test("SET ROLE to member role and back; current_user changes")
def _():
    x = conn("sm_login")
    x.execute("SET ROLE sm_ro")
    u = x.execute("SELECT current_user, session_user").fetchone()
    x.execute("RESET ROLE")
    return f"as {u}"


c.execute("CREATE TABLE doc (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), owner TEXT NOT NULL, body TEXT)")
c.execute("INSERT INTO doc(owner, body) VALUES ('u1','a'),('u2','b')")
c.execute("GRANT SELECT, INSERT, UPDATE ON doc TO sm_app")


@test("RLS: ENABLE + policy using current_setting; non-owner sees only own rows")
def _():
    c.execute("ALTER TABLE doc ENABLE ROW LEVEL SECURITY")
    c.execute("CREATE POLICY p_own ON doc FOR ALL TO sm_app USING (owner = current_setting('app.viewer_id', true)) WITH CHECK (owner = current_setting('app.viewer_id', true))")
    x = conn("sm_login")
    x.execute("SET ROLE sm_app")
    x.execute("SET app.viewer_id = 'u1'")
    n = x.execute("SELECT count(*) FROM doc").fetchone()[0]
    x.execute("SET app.viewer_id = 'u2'")
    n2 = x.execute("SELECT count(*) FROM doc").fetchone()[0]
    try:
        x.execute("INSERT INTO doc(owner) VALUES ('u1')")
        bad = "insert for other owner NOT blocked"
    except Exception:
        bad = "with-check blocks cross-owner insert"
    assert n == 1 and n2 == 1, (n, n2)
    return f"u1 sees {n}, u2 sees {n2}; {bad}"


@test("RLS status via pg_class.relrowsecurity")
def _():
    r = c.execute("SELECT relrowsecurity FROM pg_class WHERE relname='doc'").fetchone()[0]
    return f"relrowsecurity={r}"


@test("hash-sharded index (secondary) + hash-sharded unique + bucket_count")
def _():
    c.execute("CREATE TABLE ev (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), ts TIMESTAMPTZ NOT NULL DEFAULT now(), k STRING)")
    c.execute("CREATE INDEX idx_ev_ts ON ev (ts) USING HASH WITH (bucket_count = 8)")
    c.execute("CREATE UNIQUE INDEX uq_ev_k ON ev (k) USING HASH WITH (bucket_count = 8)")
    c.execute("INSERT INTO ev(k) SELECT 'k'||g FROM generate_series(1,100) g")
    n = c.execute("SELECT count(*) FROM ev WHERE ts > now() - interval '1 day'").fetchone()[0]
    return f"rows={n}"


@test("partial unique index with deleted_at IS NULL")
def _():
    c.execute("CREATE TABLE usr (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), email STRING(254) NOT NULL, deleted_at TIMESTAMPTZ)")
    c.execute("CREATE UNIQUE INDEX uq_usr_email_live ON usr (email) WHERE deleted_at IS NULL")
    c.execute("INSERT INTO usr(email) VALUES ('a@x.io')")
    try:
        c.execute("INSERT INTO usr(email) VALUES ('a@x.io')")
        raise AssertionError("dup accepted")
    except psycopg.errors.UniqueViolation:
        pass
    c.execute("UPDATE usr SET deleted_at=now()")
    c.execute("INSERT INTO usr(email) VALUES ('a@x.io')")
    return "dup blocked while live; allowed after soft delete"


@test("partial index + STORING + DESC + trigram GIN + JSONB inverted")
def _():
    c.execute("CREATE TABLE ix (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name STRING, st INT4, j JSONB, ts TIMESTAMPTZ)")
    c.execute("CREATE INDEX idx_ix_st ON ix (st, ts DESC) STORING (name) WHERE st > 1")
    c.execute("CREATE INDEX idx_ix_name_trgm ON ix USING GIN (name gin_trgm_ops)")
    c.execute("CREATE INVERTED INDEX idx_ix_j ON ix (j)")
    return "ok"


@test("computed column LEAST/GREATEST on UUID (STORED) for unordered-pair uniqueness")
def _():
    c.execute("""CREATE TABLE pair (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), a UUID NOT NULL, b UUID NOT NULL,
      lo UUID AS (LEAST(a,b)) STORED, hi UUID AS (GREATEST(a,b)) STORED)""")
    c.execute("CREATE UNIQUE INDEX uq_pair ON pair (lo, hi)")
    a, b = uuid.uuid4(), uuid.uuid4()
    c.execute("INSERT INTO pair(a,b) VALUES (%s,%s)", (a, b))
    try:
        c.execute("INSERT INTO pair(a,b) VALUES (%s,%s)", (b, a))
        raise AssertionError("reverse pair accepted")
    except psycopg.errors.UniqueViolation:
        pass
    return "unordered-pair uniqueness works"


@test("chain FK: composite FK (prior_period_id, subscription_id) -> UNIQUE(id, subscription_id)")
def _():
    c.execute("CREATE TABLE sub (id UUID PRIMARY KEY DEFAULT gen_random_uuid())")
    c.execute("""CREATE TABLE per (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), subscription_id UUID NOT NULL REFERENCES sub(id),
      prior_period_id UUID, starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL, CHECK (ends_at > starts_at),
      CONSTRAINT uq_per_id_sub UNIQUE (id, subscription_id),
      CONSTRAINT uq_per_prior UNIQUE (prior_period_id),
      CONSTRAINT fk_per_prior FOREIGN KEY (prior_period_id, subscription_id) REFERENCES per (id, subscription_id))""")
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    c.execute("INSERT INTO sub(id) VALUES (%s),(%s)", (s1, s2))
    p1 = c.execute("INSERT INTO per(subscription_id,starts_at,ends_at) VALUES (%s,'2026-01-01','2026-02-01') RETURNING id", (s1,)).fetchone()[0]
    c.execute("INSERT INTO per(subscription_id,prior_period_id,starts_at,ends_at) VALUES (%s,%s,'2026-02-01','2026-03-01')", (s1, p1))
    for sid, prior, why in ((s2, p1, "cross-subscription prior"), (s1, p1, "second successor")):
        try:
            c.execute("INSERT INTO per(subscription_id,prior_period_id,starts_at,ends_at) VALUES (%s,%s,'2026-03-01','2026-04-01')", (sid, prior))
            raise AssertionError(why + " accepted")
        except (psycopg.errors.ForeignKeyViolation, psycopg.errors.UniqueViolation):
            pass
    return "same-parent + single-successor enforced (NULL prior allowed multiple times)"


@test("slot table non-overlap: PK (gig_id, slot_start) with exploded slots")
def _():
    c.execute("CREATE TABLE gslot (gig_id UUID NOT NULL, slot_start TIMESTAMPTZ NOT NULL, booking_id UUID NOT NULL, PRIMARY KEY (gig_id, slot_start))")
    g = uuid.uuid4()
    c.execute("INSERT INTO gslot SELECT %s, s, %s FROM generate_series('2026-01-01 00:00+00'::timestamptz,'2026-01-01 02:00+00'::timestamptz,interval '1 hour') s", (g, uuid.uuid4()))
    try:
        c.execute("INSERT INTO gslot SELECT %s, s, %s FROM generate_series('2026-01-01 02:00+00'::timestamptz,'2026-01-01 03:00+00'::timestamptz,interval '1 hour') s", (g, uuid.uuid4()))
        raise AssertionError("overlap accepted")
    except psycopg.errors.UniqueViolation:
        pass
    return "overlapping window rejected atomically"


@test("device limit: slot table + SELECT FOR UPDATE, 8 concurrent logins, limit 3")
def _():
    c.execute("CREATE TABLE seat (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), max_devices INT4 NOT NULL)")
    c.execute("CREATE TABLE dslot (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), seat_id UUID NOT NULL REFERENCES seat(id), slot_no INT4 NOT NULL, UNIQUE(seat_id, slot_no))")
    c.execute("CREATE TABLE dsess (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), seat_id UUID NOT NULL, slot_id UUID NOT NULL REFERENCES dslot(id), ended_at TIMESTAMPTZ)")
    c.execute("CREATE UNIQUE INDEX uq_dsess_slot ON dsess (slot_id) WHERE ended_at IS NULL")
    seat = c.execute("INSERT INTO seat(max_devices) VALUES (3) RETURNING id").fetchone()[0]
    c.execute("INSERT INTO dslot(seat_id, slot_no) SELECT %s, g FROM generate_series(1,3) g", (seat,))
    ok, denied, errs = [], [], []

    def login():
        x = conn(autocommit=False)
        for _try in range(30):
            try:
                x.execute("SELECT id FROM seat WHERE id=%s FOR UPDATE", (seat,))
                r = x.execute("""SELECT s.id FROM dslot s WHERE s.seat_id=%s AND NOT EXISTS
                    (SELECT 1 FROM dsess d WHERE d.slot_id=s.id AND d.ended_at IS NULL) ORDER BY slot_no LIMIT 1""", (seat,)).fetchone()
                if r is None:
                    x.rollback()
                    denied.append(1)
                    return
                x.execute("INSERT INTO dsess(seat_id, slot_id) VALUES (%s,%s)", (seat, r[0]))
                x.commit()
                ok.append(1)
                return
            except (psycopg.errors.SerializationFailure, psycopg.errors.UniqueViolation):
                x.rollback()
            except Exception as e:
                errs.append(repr(e))
                x.rollback()
                return
    ths = [threading.Thread(target=login) for _ in range(8)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    n = c.execute("SELECT count(*) FROM dsess WHERE ended_at IS NULL").fetchone()[0]
    assert n == 3 and len(ok) == 3 and len(denied) == 5 and not errs, (n, len(ok), len(denied), errs)
    return f"3 granted, 5 denied, active={n}"


@test("row-level TTL: ttl_expire_after creates crdb_internal_expiration")
def _():
    c.execute("CREATE TABLE ttl1 (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), v INT) WITH (ttl_expire_after = '1 minute', ttl_job_cron = '* * * * *')")
    r = c.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='ttl1' AND column_name='crdb_internal_expiration'").fetchone()[0]
    return f"hidden expiration column present={r}"


@test("row-level TTL: ttl_expiration_expression with nullable column (NULL = never expires)")
def _():
    c.execute("""CREATE TABLE ttl2 (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), expires_at TIMESTAMPTZ)
        WITH (ttl_expiration_expression = 'expires_at', ttl_job_cron = '* * * * *', ttl_select_batch_size=100)""")
    c.execute("INSERT INTO ttl2(expires_at) VALUES (now() - interval '1 hour'), (NULL), (now() + interval '1 day')")
    return "created"


@test("TTL job actually deletes expired rows, keeps NULL/future rows (<=150s wait)")
def _():
    i = 0
    for i in range(30):
        n = c.execute("SELECT count(*) FROM ttl2").fetchone()[0]
        if n == 2:
            break
        time.sleep(5)
    left = c.execute("SELECT count(*) FROM ttl2 WHERE expires_at IS NULL").fetchone()[0]
    assert n == 2 and left == 1, (n, left)
    return f"after ~{5*i}s: 2 rows left (NULL and future kept)"


@test("TTL expression computed from column + interval (ended_at + 30 days)")
def _():
    c.execute("""CREATE TABLE ttl3 (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), ended_at TIMESTAMPTZ)
      WITH (ttl_expiration_expression = $$(ended_at + interval '30 days')$$)""")
    return "ok"


@test("CHECK regex ~ (E.164 phone), lower(email), jsonb ?")
def _():
    c.execute("""CREATE TABLE chk (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), phone STRING(16), email STRING(254), j JSONB,
        CONSTRAINT ck_chk_phone CHECK (phone ~ '^\\+[1-9][0-9]{6,14}$'), CONSTRAINT ck_chk_email CHECK (email = lower(email)),
        CONSTRAINT ck_chk_j CHECK (j ? 'en'))""")
    c.execute("""INSERT INTO chk(phone,email,j) VALUES ('+919876543210','a@b.c','{"en":"x"}')""")
    out = []
    for vals in (("9876543210", "a@b.c", '{"en":1}'), ("+919876543210", "A@b.c", '{"en":1}'), ("+919876543210", "a@b.c", '{"fr":1}')):
        try:
            c.execute("INSERT INTO chk(phone,email,j) VALUES (%s,%s,%s)", vals)
            out.append("ACCEPTED")
        except psycopg.errors.CheckViolation:
            out.append("rejected")
    assert out == ["rejected"] * 3, out
    return "regex, lower and jsonb ? checks all reject bad rows"


c.execute("CREATE TABLE led (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), amt INT)")
c.execute("GRANT SELECT, INSERT ON led TO sm_app")


@test("append-only via missing UPDATE/DELETE privilege for non-owner role")
def _():
    x = conn("sm_login")
    x.execute("SET ROLE sm_app")
    x.execute("INSERT INTO led(amt) VALUES (1)")
    r = []
    for sql in ("UPDATE led SET amt=2", "DELETE FROM led"):
        try:
            x.execute(sql)
            r.append("ALLOWED")
        except psycopg.errors.InsufficientPrivilege:
            r.append("denied")
    assert r == ["denied", "denied"], r
    return "UPDATE/DELETE denied by missing privilege"


@test("append-only trigger blocks UPDATE/DELETE even for owner / root")
def _():
    c.execute("""CREATE OR REPLACE FUNCTION fn_block_mutation() RETURNS TRIGGER LANGUAGE PLpgSQL AS $$ BEGIN RAISE EXCEPTION 'append-only table %', TG_TABLE_NAME USING ERRCODE='42501'; END $$""")
    c.execute("CREATE TRIGGER trg_led_no_upd BEFORE UPDATE OR DELETE ON led FOR EACH ROW EXECUTE FUNCTION fn_block_mutation()")
    r = []
    for sql in ("UPDATE led SET amt=3", "DELETE FROM led"):
        try:
            c.execute(sql)
            r.append("ALLOWED")
        except Exception:
            r.append("blocked")
    assert r == ["blocked", "blocked"], r
    return "root blocked by BEFORE UPDATE OR DELETE row trigger"


@test("view owner semantics: view readable by role without base-table grant")
def _():
    c.execute("CREATE TABLE secret (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), pub STRING, hidden STRING)")
    c.execute("INSERT INTO secret(pub, hidden) VALUES ('p','h')")
    c.execute("CREATE VIEW vw_secret AS SELECT id, pub FROM secret")
    c.execute("GRANT SELECT ON vw_secret TO sm_ro")
    x = conn("sm_login")
    x.execute("SET ROLE sm_ro")
    n = x.execute("SELECT count(*) FROM vw_secret").fetchone()[0]
    try:
        x.execute("SELECT hidden FROM secret")
        direct = "base readable!"
    except psycopg.errors.InsufficientPrivilege:
        direct = "base denied"
    assert n == 1 and direct == "base denied"
    return "view readable, base denied"


@test("functions: SQL table function with viewer arg; SECURITY DEFINER keyword")
def _():
    c.execute("""CREATE FUNCTION fn_visible(viewer UUID) RETURNS SETOF secret LANGUAGE SQL STABLE AS $$ SELECT * FROM secret WHERE id <> viewer $$""")
    c.execute("""CREATE FUNCTION fn_sd(p INT) RETURNS INT LANGUAGE SQL SECURITY DEFINER AS $$ SELECT p+1 $$""")
    return "both created"


@test("view using current_setting('app.viewer_id', true) context")
def _():
    c.execute("""CREATE VIEW vw_ctx AS SELECT id, pub FROM secret WHERE current_setting('app.viewer_id', true) IS NOT NULL""")
    return "ok"


@test("hash functions: hmac, sha256, crypt/gen_salt, digest")
def _():
    out = {}
    for name, sql in (("hmac", "SELECT hmac('a'::BYTES,'k','sha256')"), ("sha256", "SELECT sha256('a')"),
                      ("crypt", "SELECT crypt('pw', gen_salt('bf'))"), ("digest", "SELECT digest('a','sha256')")):
        try:
            c.execute(sql).fetchone()
            out[name] = "yes"
        except Exception:
            out[name] = "no"
    return str(out)


@test("vector type + CREATE VECTOR INDEX (feature.vector_index.enabled)")
def _():
    try:
        c.execute("SET CLUSTER SETTING feature.vector_index.enabled = true")
    except Exception as e:
        return f"setting failed: {e}"
    c.execute("CREATE TABLE vec (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), emb VECTOR(8))")
    c.execute("CREATE VECTOR INDEX idx_vec_emb ON vec (emb)")
    return "vector index created"


@test("ALTER ROLE ... SET statement_timeout")
def _():
    c.execute("ALTER ROLE sm_ro SET statement_timeout = '7s'")
    return "ok"


@test("composite FK with ON DELETE SET NULL (col-list)")
def _():
    c.execute("CREATE TABLE par (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), org UUID NOT NULL, UNIQUE(id, org))")
    c.execute("CREATE TABLE chi (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), org UUID NOT NULL, par UUID, FOREIGN KEY (par, org) REFERENCES par(id, org) ON DELETE SET NULL (par))")
    return "accepted"


@test("circular FKs via ALTER TABLE ADD CONSTRAINT; ADD CONSTRAINT IF NOT EXISTS support")
def _():
    c.execute("CREATE TABLE IF NOT EXISTS ca (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), b UUID)")
    c.execute("CREATE TABLE IF NOT EXISTS cb (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), a UUID REFERENCES ca(id))")
    c.execute("ALTER TABLE ca ADD CONSTRAINT fk_ca_b FOREIGN KEY (b) REFERENCES cb(id)")
    try:
        c.execute("ALTER TABLE ca ADD CONSTRAINT IF NOT EXISTS fk_ca_b FOREIGN KEY (b) REFERENCES cb(id)")
        return "ADD CONSTRAINT IF NOT EXISTS supported"
    except Exception:
        return "ADD CONSTRAINT IF NOT EXISTS NOT supported (generator uses DO block + catalog check)"


@test("DO block (PL/pgSQL anonymous) usable for idempotent DDL incl. EXECUTE")
def _():
    c.execute("""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_zzz') THEN EXECUTE 'CREATE TABLE zz (id INT PRIMARY KEY)'; END IF; END $$""")
    return "DO block with EXECUTE DDL works"


@test("idempotent forms: CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE VIEW/FUNCTION/TRIGGER")
def _():
    c.execute("CREATE INDEX IF NOT EXISTS idx_ev_k ON ev (k)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ev_k ON ev (k)")
    c.execute("CREATE OR REPLACE VIEW vw_ev AS SELECT id FROM ev")
    c.execute("CREATE OR REPLACE TRIGGER trg_led_no_upd BEFORE UPDATE OR DELETE ON led FOR EACH ROW EXECUTE FUNCTION fn_block_mutation()")
    return "all accepted"


@test("zone config gc.ttlseconds on table")
def _():
    c.execute("ALTER TABLE led CONFIGURE ZONE USING gc.ttlseconds = 3600")
    return "ok"


@test("PARTITION BY LIST (licence check)")
def _():
    c.execute("CREATE TABLE pt (r STRING NOT NULL, id UUID NOT NULL DEFAULT gen_random_uuid(), PRIMARY KEY (r, id)) PARTITION BY LIST (r) (PARTITION p_a VALUES IN ('a'), PARTITION p_rest VALUES IN (DEFAULT))")
    return "ok"


@test("ALTER DEFAULT PRIVILEGES FOR ROLE; REVOKE ALL FROM PUBLIC")
def _():
    c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE root IN SCHEMA public GRANT SELECT ON TABLES TO sm_ro")
    c.execute("REVOKE ALL ON TABLE doc FROM PUBLIC")
    return "ok"


@test("CREATE ROLE ... LOGIN PASSWORD")
def _():
    c.execute("CREATE ROLE IF NOT EXISTS sm_pw LOGIN PASSWORD 'abcdefghijkl123'")
    return "ok"


@test("stored lower(email) computed column + unique")
def _():
    c.execute("CREATE TABLE em (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), email STRING, email_lc STRING AS (lower(email)) STORED)")
    c.execute("CREATE UNIQUE INDEX uq_em_lc ON em (email_lc)")
    return "ok"


@test("CHECK referencing STORED computed column")
def _():
    c.execute("CREATE TABLE cc (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), a INT, b INT, s INT AS (a+b) STORED, CHECK (s >= 0))")
    return "ok"


print("| # | Mechanism | Result | Notes | s |\n|---|---|---|---|---|")
for i, (n, st, note, dt) in enumerate(results, 1):
    print(f"| {i} | {n} | {st} | {note.replace('|', '/')} | {dt:.1f} |")
print(f"\nTotal {len(results)}; pass {sum(r[1] == 'PASS' for r in results)}; fail {sum(r[1] == 'FAIL' for r in results)}")
