#!/usr/bin/env python3
"""Smoke-test the running Grafana: datasource health, every provisioned dashboard loads, every panel query executes.

  python tools/check_dashboards.py [--url http://localhost:3300] [--user admin] [--password admin] [--verbose]

Each panel target is sent through Grafana's own /api/ds/query (so macros like $__rate_interval are expanded by
Grafana). Dashboard template variables are replaced by their 'All'/default value. A panel FAILS if Grafana returns an
error; EMPTY (no series) is reported separately because optional integrations (Caddy request metrics, CloudWatch,
ingestion engine, journald) legitimately have no data locally.
Exit code 1 if any datasource is unhealthy or any query errors.
"""
import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:3300")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    auth = "Basic " + base64.b64encode(f"{a.user}:{a.password}".encode()).decode()

    def call(path, body=None):
        req = urllib.request.Request(a.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except Exception:
                return e.code, None


    def resolve_query_var(v, known):
        q = v.get("query")
        q = q.get("query") if isinstance(q, dict) else q
        ds = (v.get("datasource") or {}).get("uid", "prometheus")
        base = a.url + f"/api/datasources/uid/{ds}/resources"
        def sub(t):
            for k, vals in sorted(known.items(), key=lambda kv: -len(kv[0])):
                t = t.replace("${" + k + "}", "|".join(vals)).replace("$" + k, "|".join(vals))
            return t
        q = sub(q or "")
        m = re.match(r"label_values\((.*),\s*([A-Za-z_][A-Za-z0-9_]*)\)\s*$", q.strip())
        try:
            if m:
                sel, label = m.group(1), m.group(2)
                url = base + (f"/api/v1/label/{label}/values?match[]=" + urllib.parse.quote(sel) if ds == "prometheus" else f"/label/{label}/values")
            elif m is None and re.match(r"label_values\(([A-Za-z_][A-Za-z0-9_]*)\)\s*$", q.strip()):
                label = re.match(r"label_values\(([A-Za-z_][A-Za-z0-9_]*)\)", q.strip()).group(1)
                url = base + (f"/api/v1/label/{label}/values" if ds == "prometheus" else f"/label/{label}/values")
            else:   # raw selector + regex capture (official CRDB dashboards: cluster var = sys_uptime{job="cockroachdb"} with regex)
                rx = (v.get("regex") or "").strip("/")
                lab = re.search(r"([A-Za-z_][A-Za-z0-9_]*)=\"\(\[\^\"\]\+\)\"", rx) or re.search(r"([A-Za-z_][A-Za-z0-9_]*)=\\\"\(", rx)
                label = lab.group(1) if lab else "cluster"
                url = base + f"/api/v1/label/{label}/values?match[]=" + urllib.parse.quote(q)
            req = urllib.request.Request(url, headers={"Authorization": auth})
            with urllib.request.urlopen(req, timeout=30) as r:
                vals = json.loads(r.read()).get("data", [])
            return vals or [".*"]
        except Exception:
            return [".*"]

    bad = 0
    print("== datasources")
    st, dss = call("/api/datasources")
    for ds in dss:
        if ds["type"] == "alertmanager":   # the Alertmanager datasource has no /health endpoint; query its API through the Grafana proxy
            s, h = call(f"/api/alertmanager/{ds['uid']}/api/v2/status")
            h = {"status": "OK", "message": "alertmanager API reachable via proxy"} if s == 200 else {"status": "ERR", "message": str(h)[:100]}
            s = 200
        else:
            s, h = call(f"/api/datasources/uid/{ds['uid']}/health")
        ok = s == 200 and (h or {}).get("status") in ("OK", "success", "Success")
        print(f"  {'OK ' if ok else 'BAD'} {ds['name']:14} {ds['type']:14} {(h or {}).get('message', '')}")
        bad += 0 if ok else 1

    st, res = call("/api/search?type=dash-db&limit=500")
    print(f"== dashboards ({len(res)})")
    now = int(time.time() * 1000)
    tot_q = tot_err = tot_empty = 0
    for item in sorted(res, key=lambda x: (x.get("folderTitle", ""), x["title"])):
        st, dj = call(f"/api/dashboards/uid/{item['uid']}")
        if st != 200:
            print(f"  BAD load {item['title']} ({st})")
            bad += 1
            continue
        dash = dj["dashboard"]
        vars_ = {}      # name -> list of candidate values (resolved from the live datasource, like the Grafana UI would)
        for v in dash.get("templating", {}).get("list", []):
            cur = v.get("current", {}) or {}
            if v["type"] in ("custom", "interval", "textbox"):
                val = cur.get("value") or v.get("query", "").split(",")[0]
                vars_[v["name"]] = [val if isinstance(val, str) else ".*"]
            elif v["type"] == "query":
                vars_[v["name"]] = resolve_query_var(v, vars_)
        panels = []

        def walk(ps):
            for p in ps:
                panels.append(p)
                walk(p.get("panels", []))
        walk(dash.get("panels", []))
        n_err = n_empty = n_q = 0
        msgs = []
        jobs = []
        for p in panels:
            if p.get("type") in ("row", "text"):
                continue
            for t in p.get("targets", []):
                expr = t.get("expr")
                if not expr:
                    continue
                for k, vals in sorted(vars_.items(), key=lambda kv: -len(kv[0])):
                    for pat in ('="$' + k + '"', '="${' + k + '}"'):
                        expr = expr.replace(pat, '="' + vals[0] + '"')          # equality matchers get one concrete value
                    expr = expr.replace("${" + k + "}", "(" + "|".join(vals) + ")" if len(vals) > 1 else vals[0]).replace("$" + k, "(" + "|".join(vals) + ")" if len(vals) > 1 else vals[0])
                ds = t.get("datasource") or p.get("datasource") or {"type": "prometheus", "uid": "prometheus"}
                if isinstance(ds, str):
                    ds = {"uid": "prometheus", "type": "prometheus"}
                q = {"refId": t.get("refId", "A"), "datasource": ds, "expr": expr, "intervalMs": 15000, "maxDataPoints": 500, "legendFormat": t.get("legendFormat", "")}
                if ds.get("type") == "loki":
                    q["queryType"] = "range" if p.get("type") in ("logs", "timeseries") else "instant"
                    q["maxLines"] = 20
                else:
                    q["instant"] = True
                    q["range"] = False
                jobs.append((p.get("title"), expr, q))

        def run(job):
            title, expr, q = job
            now_ = int(time.time() * 1000)
            s_, r = call("/api/ds/query", {"queries": [q], "from": str(now_ - 3600_000), "to": str(now_)})
            err, rows = None, 0
            if s_ != 200:
                err = f"HTTP {s_}: {(r or {}).get('message', '')}"[:200]
            else:
                rr = (r or {}).get("results", {}).get(q["refId"], {})
                if rr.get("error"):
                    err = rr["error"][:200]
                rows = sum(1 for f in rr.get("frames", []) if (f.get("data", {}).get("values") or [[]])[0])
            return title, expr, err, rows

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(run, jobs))
        for title, expr, err, rows in results:
            n_q += 1
            if err:
                n_err += 1
                msgs.append(f"      ERR  [{title}] {err} :: {expr[:140]}")
            elif rows == 0:
                n_empty += 1
                if a.verbose:
                    msgs.append(f"      empty [{title}] {expr[:150]}")
        tot_q += n_q
        tot_err += n_err
        tot_empty += n_empty
        flag = "BAD" if n_err else "OK "
        print(f"  {flag} {item.get('folderTitle', ''):20} {item['title']:48} queries={n_q:3} errors={n_err} empty={n_empty}")
        for m in msgs:
            print(m)
        bad += 1 if n_err else 0
    print(f"== total queries={tot_q} errors={tot_err} empty={tot_empty}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
