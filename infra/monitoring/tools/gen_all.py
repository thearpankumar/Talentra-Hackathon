#!/usr/bin/env python3
"""Regenerates every provisioned dashboard under grafana/dashboards/.

  custom dashboards  : built from gen_host.py / gen_crdb.py / gen_edge.py
  vendored dashboards: tools/vendor/*.json (downloaded once, committed; NO runtime downloads), adapted here:
        * ${DS_PROMETHEUS} / datasource variables -> fixed datasource uid "prometheus"
        * __inputs/__requires stripped, ids dropped, unique uids, talentra tag + folder
Run:  python tools/gen_all.py
"""
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(os.path.dirname(HERE), "grafana", "dashboards")

import gen_host, gen_crdb, gen_edge  # noqa: E402

PROM = {"type": "prometheus", "uid": "prometheus"}


def vendor(src, folder, uid, title=None, tags=None):
    with open(os.path.join(HERE, "vendor", src), encoding="utf-8") as fh:
        raw = fh.read()
    # datasource substitutions in the raw text (string-level so nested panels/targets are all covered)
    raw = re.sub(r'"\$\{DS_[A-Z_]+\}"', '"prometheus"', raw)
    raw = raw.replace("${DS_PROMETHEUS}", "prometheus").replace("$ds_prometheus", "prometheus").replace("${ds_prometheus}", "prometheus")
    d = json.loads(raw)
    for k in ("__inputs", "__requires", "__elements"):
        d.pop(k, None)
    d["id"] = None
    d["uid"] = uid
    if title:
        d["title"] = title
    d["tags"] = sorted(set((d.get("tags") or []) + ["talentra"] + (tags or [])))
    d["editable"] = False
    d["version"] = 1
    # drop the datasource template variable (ds is pinned) and any 'current' pointing at it
    tl = d.get("templating", {}).get("list", [])
    d["templating"]["list"] = [t for t in tl if not (t.get("type") == "datasource")]

    def fix(o):
        if isinstance(o, dict):
            ds = o.get("datasource")
            if isinstance(ds, str) and ds not in ("-- Grafana --", "-- Mixed --"):
                o["datasource"] = dict(PROM)
            elif isinstance(ds, dict) and ds.get("type") == "prometheus":
                o["datasource"] = dict(PROM)
            for v in o.values():
                fix(v)
        elif isinstance(o, list):
            for v in o:
                fix(v)
    fix(d)
    d["links"] = [
        {"type": "dashboards", "title": "Talentra dashboards", "tags": ["talentra"], "asDropdown": True, "includeVars": False, "keepTime": True, "icon": "external link"},
    ]
    d.setdefault("annotations", {"list": []})
    d["annotations"]["list"] = [a for a in d["annotations"].get("list", []) if a.get("builtIn")] + [{
        "name": "Alerts firing", "datasource": PROM, "enable": True, "iconColor": "red",
        "expr": 'ALERTS{alertstate="firing",severity=~"warning|critical",alertname!="Watchdog"}', "step": "60s", "titleFormat": "{{alertname}}", "textFormat": "{{summary}}", "tagKeys": "severity"}]
    path = os.path.join(OUT, folder)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, uid + ".json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, indent=1)
        fh.write("\n")


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    gen_host.build_all(OUT)
    gen_crdb.build_all(OUT)
    gen_edge.build_all(OUT)
    vendor("node-exporter-full-1860.json", "Host and Containers", "node-exporter-full", "Node Exporter Full", ["node-exporter", "host", "community-1860"])
    for name, uid, title in [("overview", "crdb-console-overview", "CRDB Console: Overview"), ("sql", "crdb-console-sql", "CRDB Console: SQL"), ("storage", "crdb-console-storage", "CRDB Console: Storage"),
                             ("replication", "crdb-console-replication", "CRDB Console: Replication"), ("distributed", "crdb-console-distributed", "CRDB Console: Distributed"),
                             ("runtime", "crdb-console-runtime", "CRDB Console: Runtime"), ("hardware", "crdb-console-hardware", "CRDB Console: Hardware"),
                             ("slow_request", "crdb-console-slow-requests", "CRDB Console: Slow Requests"), ("queues", "crdb-console-queues", "CRDB Console: Queues")]:
        vendor("crdb-%s.json" % name, "CockroachDB", uid, title, ["cockroachdb", "official"])
    n = sum(len(f) for _, _, f in os.walk(OUT))
    print("wrote", n, "dashboards to", OUT)


if __name__ == "__main__":
    main()
