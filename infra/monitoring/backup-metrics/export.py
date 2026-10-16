#!/usr/bin/env python3
"""Backup-health textfile exporter for node_exporter's textfile collector.

Reads the backup-handler's CSV (infra/main-db/handler/handler.sh, one line per run):
    ts_utc,op,kind,status,start_utc,end_utc,duration_s,detail
      e.g. 2026-10-15T18:01:15Z,backup,full-local,ok,2026-10-15T18:01:14Z,2026-10-15T18:01:15Z,1,"job: ..."
           2026-10-15T18:01:16Z,sync,restore,fail,...
`kind` = "<full|incr>-<local|s3>" for op=backup, "restore" for op=sync (and "verify" for op=verify if present).
Writes atomically to $TEXTFILE_DIR/talentra_backup.prom every $INTERVAL seconds. Pure stdlib.
Backup size = bytes under $BACKUP_DIR (the shared nodelocal extern volume), recomputed every $SIZE_INTERVAL seconds.
"""
import csv
import os
import sys
import time
from calendar import timegm
from time import strptime

CSV_PATH = os.environ.get("BACKUP_CSV", "/logs/backup_timing.csv")
OUT_DIR = os.environ.get("TEXTFILE_DIR", "/textfile")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
SIZE_INTERVAL = int(os.environ.get("SIZE_INTERVAL", "600"))
FMT = "%Y-%m-%dT%H:%M:%SZ"


def epoch(s):
    try:
        return float(timegm(strptime(s.strip(), FMT)))
    except Exception:
        return None


def split_kind(op, kind):
    if op == "backup" and "-" in kind:
        typ, dest = kind.split("-", 1)
        return typ, dest
    return kind, "backup-cluster" if op == "sync" else "-"


def lbl(**kw):
    return "{" + ",".join('%s="%s"' % (k, str(v).replace("\\", "\\\\").replace('"', "'")) for k, v in kw.items()) + "}"


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def scan():
    series = {}   # (op, type, dest) -> dict
    rows = 0
    if not os.path.exists(CSV_PATH):
        return series, rows, False
    with open(CSV_PATH, newline="", encoding="utf-8", errors="replace") as fh:
        for rec in csv.reader(fh):
            if len(rec) < 7 or rec[0] == "ts_utc":
                continue
            ts, op, kind, status, _start, end, dur = rec[:7]
            typ, dest = split_kind(op, kind)
            t_end = epoch(end) or epoch(ts)
            try:
                d = float(dur)
            except ValueError:
                d = 0.0
            s = series.setdefault((op, typ, dest), {
                "last_run": 0.0, "last_ok": 0.0, "ok": 0, "fail": 0, "consec_fail": 0,
                "last_success": None, "last_run_ok": None, "last_run_dur": 0.0, "last_ok_dur": None, "last_ok_size": None,
            })
            rows += 1
            if t_end and t_end >= s["last_run"]:
                s["last_run"] = t_end
                s["last_run_ok"] = 1 if status == "ok" else 0
                s["last_run_dur"] = d
            if status == "ok":
                s["ok"] += 1
                s["consec_fail"] = 0
                if t_end and t_end >= s["last_ok"]:
                    s["last_ok"] = t_end
                    s["last_ok_dur"] = d
                    if len(rec) > 8 and rec[8].strip().isdigit():   # optional 9th column: size_bytes
                        s["last_ok_size"] = int(rec[8])
            else:
                s["fail"] += 1
                s["consec_fail"] += 1
    return series, rows, True


def render(series, present, size_bytes):
    o = []
    add = o.append

    def head(name, help_, typ="gauge"):
        add("# HELP %s %s" % (name, help_))
        add("# TYPE %s %s" % (name, typ))

    head("talentra_backup_last_success_timestamp_seconds", "Unix time of the last successful run.")
    for (op, typ, dest), s in sorted(series.items()):
        if s["last_ok"]:
            add("talentra_backup_last_success_timestamp_seconds%s %.0f" % (lbl(op=op, type=typ, dest=dest), s["last_ok"]))
    head("talentra_backup_last_run_timestamp_seconds", "Unix time of the last run (any status).")
    for (op, typ, dest), s in sorted(series.items()):
        add("talentra_backup_last_run_timestamp_seconds%s %.0f" % (lbl(op=op, type=typ, dest=dest), s["last_run"]))
    head("talentra_backup_last_run_success", "1 if the most recent run succeeded, 0 if it failed.")
    for (op, typ, dest), s in sorted(series.items()):
        add("talentra_backup_last_run_success%s %d" % (lbl(op=op, type=typ, dest=dest), s["last_run_ok"] or 0))
    head("talentra_backup_last_duration_seconds", "Duration of the last SUCCESSFUL run (RTO budget input).")
    for (op, typ, dest), s in sorted(series.items()):
        if s["last_ok_dur"] is not None:
            add("talentra_backup_last_duration_seconds%s %g" % (lbl(op=op, type=typ, dest=dest), s["last_ok_dur"]))
    head("talentra_backup_last_run_duration_seconds", "Duration of the last run, any status.")
    for (op, typ, dest), s in sorted(series.items()):
        add("talentra_backup_last_run_duration_seconds%s %g" % (lbl(op=op, type=typ, dest=dest), s["last_run_dur"]))
    head("talentra_backup_last_size_bytes", "Size of the last successful run (only if the handler writes the optional size_bytes column).")
    for (op, typ, dest), s in sorted(series.items()):
        if s["last_ok_size"] is not None:
            add("talentra_backup_last_size_bytes%s %d" % (lbl(op=op, type=typ, dest=dest), s["last_ok_size"]))
    head("talentra_backup_consecutive_failures", "Failed runs since the last success.")
    for (op, typ, dest), s in sorted(series.items()):
        add("talentra_backup_consecutive_failures%s %d" % (lbl(op=op, type=typ, dest=dest), s["consec_fail"]))
    head("talentra_backup_runs_in_log", "Runs recorded in the CSV (it is append-only; this behaves like a counter until rotated).")
    for (op, typ, dest), s in sorted(series.items()):
        add("talentra_backup_runs_in_log%s %d" % (lbl(op=op, type=typ, dest=dest, status="ok"), s["ok"]))
        add("talentra_backup_runs_in_log%s %d" % (lbl(op=op, type=typ, dest=dest, status="fail"), s["fail"]))
    head("talentra_backup_csv_present", "1 if the handler CSV log is readable by the exporter.")
    add("talentra_backup_csv_present %d" % (1 if present else 0))
    if size_bytes is not None:
        head("talentra_backup_share_bytes", "Bytes stored under the shared backup (nodelocal) volume.")
        add("talentra_backup_share_bytes %d" % size_bytes)
    head("talentra_backup_exporter_last_scan_timestamp_seconds", "Unix time of the last exporter scan.")
    add("talentra_backup_exporter_last_scan_timestamp_seconds %.0f" % time.time())
    return "\n".join(o) + "\n"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "talentra_backup.prom")
    size_bytes, last_size = None, 0.0
    once = "--once" in sys.argv
    while True:
        try:
            if os.path.isdir(BACKUP_DIR) and time.time() - last_size >= SIZE_INTERVAL:
                size_bytes, last_size = dir_size(BACKUP_DIR), time.time()
            series, _rows, present = scan()
            tmp = out + ".tmp"
            with open(tmp, "w") as fh:
                fh.write(render(series, present, size_bytes))
            os.replace(tmp, out)
        except Exception as exc:  # never die; the stale-timestamp alert covers a stuck exporter
            print("scan failed: %r" % (exc,), file=sys.stderr, flush=True)
        if once:
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
