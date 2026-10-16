"""`uv run engine ...` command line interface."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .config import config_json_schema, dump_config, load_config, parse_config, validate_against_schema


def _fmt_eta(s: float | None) -> str:
    if s is None:
        return "?"
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _svc():
    from .services import Services
    return Services()


def _print_findings(findings: list[dict[str, Any]]) -> None:
    print("Scenarios already ingested (same / similar fingerprint):")
    for f in findings:
        print(f"  - {f['scenario_id']}: {f['kind']} (sim {f['similarity']}) matches {f['matched_scenario_id']} "
              f"run={f['matched_run_id']} rows={f['rows']} source={f['source']}")


def cmd_run(a: argparse.Namespace) -> int:
    from .runner import ConfigError, NeedsConfirmation
    svc = _svc()
    if a.config:
        cfg = load_config(a.config)
        vid, _ = svc.save_config(dump_config(cfg, "yaml"), "yaml", f"imported {a.config}", "cli")
    else:
        vid = svc.active_version_id()
    row = svc.state.get_config(vid)
    cfg = parse_config(row["content"], row["fmt"])  # type: ignore[index]
    decisions = {}
    for d in a.decision or []:
        k, _, v = d.partition("=")
        decisions[k] = v
    try:
        try:
            run_id = svc.runner.create_run(cfg, vid, decisions, {"workers": a.workers, "batch_size": a.batch_size,
                                                                  "throttle_rows_per_sec": a.throttle})
        except NeedsConfirmation as e:
            _print_findings(e.findings)
            if sys.stdin.isatty() and not a.non_interactive:
                ans = input("Choose for all flagged scenarios [skip/append/replace]: ").strip()
                decisions = {f["scenario_id"]: ans for f in e.findings}
                run_id = svc.runner.create_run(cfg, vid, decisions, {"workers": a.workers, "batch_size": a.batch_size})
            else:
                print("Re-run with --decision <scenario_id>=skip|append|replace (or --decision '*'=append).", file=sys.stderr)
                return 3
    except ConfigError as e:
        print("Config invalid:\n  " + "\n  ".join(e.issues), file=sys.stderr)
        return 2
    print(f"run {run_id} started (Ctrl-C = pause; resume with `engine resume {run_id}`)")
    return _drive(svc, run_id)


def _drive(svc, run_id: str) -> int:
    ctl = svc.runner.get_control(run_id)
    signal.signal(signal.SIGINT, lambda *_: (print("\npausing..."), ctl.pause.set()))
    result: dict[str, str] = {}
    th = threading.Thread(target=lambda: result.setdefault("s", svc.runner.execute(run_id, ctl)), daemon=True)
    th.start()
    t_last = 0.0
    while th.is_alive():
        th.join(1.0)
        if time.time() - t_last >= 2:
            p = svc.runner.progress_snapshot(run_id)
            pct = 100 * p["total_done"] / p["total_planned"] if p["total_planned"] else 0
            print(f"\r{p['status']:>9} {p['total_done']:>10,}/{p['total_planned']:,} ({pct:5.1f}%)  {p.get('rate', 0):>9,.0f} rows/s  ETA {_fmt_eta(p.get('eta_s'))}   ", end="", flush=True)
            t_last = time.time()
    p = svc.runner.progress_snapshot(run_id)
    print(f"\n{result.get('s')}: {p['total_done']:,} rows in {p['elapsed']:.1f}s  ({p['total_done'] / max(p['elapsed'], 1e-9):,.0f} rows/s avg)")
    return 0 if result.get("s") in ("completed", "paused") else 1


def cmd_resume(a: argparse.Namespace) -> int:
    svc = _svc()
    print(f"resuming {a.run_id}")
    return _drive(svc, a.run_id)


def cmd_runs(a: argparse.Namespace) -> int:
    for r in _svc().list_runs(30):
        print(f"{r['id']}  {r['status']:<11} {r['total_done']:>10,}/{r['total_planned']:<10,} {r['elapsed']:.0f}s {r['error'] or ''}")
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    rep = _svc().runner.validate_run(a.run_id)
    print(json.dumps(rep, indent=2, default=str))
    return 0 if rep["ok"] else 1


def cmd_purge(a: argparse.Namespace) -> int:
    print(json.dumps(_svc().runner.purge_run(a.run_id), indent=2))
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    svc = _svc()
    cfg = load_config(a.config)
    issues = validate_against_schema(cfg, svc.runner.get_schema())
    print(f"{len(cfg.scenarios)} scenarios, {cfg.total_records:,} records; fingerprints computed")
    for i in issues:
        print("  !", i)
    return 1 if issues else 0


def cmd_estimate(a: argparse.Namespace) -> int:
    from .runner import estimate
    svc = _svc()
    print(json.dumps(estimate(svc.runner, load_config(a.config), a.sample), indent=2))
    return 0


def cmd_memory(a: argparse.Namespace) -> int:
    svc = _svc()
    f = svc.runner.check_memory(load_config(a.config))
    _print_findings(f) if f else print("no previously ingested scenario matches")
    return 0


def cmd_schema(a: argparse.Namespace) -> int:
    sch = _svc().runner.get_schema()
    if a.json:
        print(json.dumps(sch.to_dict(), indent=2))
    else:
        for n in sch.order():
            t = sch.tables[n]
            print(f"{n}: pk={t.pk} cols={len(t.columns)} fks={[(f.columns[0], f.ref_table) for f in t.fks]} uniques={t.uniques}")
    return 0


def cmd_schema_json(a: argparse.Namespace) -> int:
    out = json.dumps(config_json_schema(), indent=2)
    if a.out:
        Path(a.out).write_text(out)
    else:
        print(out)
    return 0


def cmd_config(a: argparse.Namespace) -> int:
    svc = _svc()
    if a.action == "list":
        act = svc.active_version_id()
        for v in svc.state.config_versions(50):
            print(f"{'*' if v['id'] == act else ' '} v{v['id']}  {v['fmt']}  {v['author']:<8} {v['size']:>8}B  {v['note']}")
    elif a.action == "use":
        svc.select_version(int(a.arg))
        print("active config version:", a.arg)
    elif a.action == "show":
        print(svc.current()["content"])
    elif a.action == "import":
        cfg = load_config(a.arg)
        vid, issues = svc.save_config(dump_config(cfg, "yaml"), "yaml", f"imported {a.arg}", "cli")
        print(f"saved as version {vid}", issues or "")
    elif a.action == "export":
        Path(a.arg).write_text(svc.current()["content"], encoding="utf-8")
        print("written", a.arg)
    return 0


def cmd_gen(a: argparse.Namespace) -> int:
    from .scenario_graph import ScenarioGenerator
    svc = _svc()
    gen = ScenarioGenerator(svc)
    r = gen.start("cli", a.theme, a.count, a.scale)
    if not r.get("proposal_id"):
        print("nothing generated:", r)
        return 1
    print(f"generated {r.get('generated')} scenarios (proposal {r['proposal_id']})")
    if a.accept:
        res = gen.resume(r["graph_thread"], "accept")
        print("accepted ->", res)
        if a.out:
            Path(a.out).write_text(svc.current()["content"], encoding="utf-8")
            print("config written to", a.out)
    else:
        print(r["diff_preview"][:800])
        print("(not applied; re-run with --accept, or accept in the UI)")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("ingestion_engine.api:app_factory", factory=True, host=a.host, port=a.port, log_level="info")
    return 0


def cmd_llm(a: argparse.Namespace) -> int:
    from .llm import discover_models, get_llm, probe_model
    from .settings import get_settings
    s = get_settings()
    if a.action == "discover":
        print(json.dumps(discover_models(s), indent=2))
    else:
        ok, msg = probe_model(s.bedrock_model_id, s)
        print(f"probe {s.bedrock_model_id} @ {s.bedrock_region}: {'OK' if ok else 'FAILED'} - {msg}")
        h = get_llm(s)
        print("active:", json.dumps(h.status()))
    return 0


def cmd_pause_note(a: argparse.Namespace) -> int:  # pragma: no cover
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="engine", description="Talentra agentic data ingestion engine")
    sp = p.add_subparsers(dest="cmd", required=True)

    r = sp.add_parser("run", help="ingest a config (default: the active saved config)")
    r.add_argument("config", nargs="?")
    r.add_argument("--workers", type=int)
    r.add_argument("--batch-size", type=int)
    r.add_argument("--throttle", type=int, help="soft cap rows/s")
    r.add_argument("--decision", action="append", help="scenario_id=skip|append|replace ('*' = all flagged)")
    r.add_argument("--non-interactive", action="store_true")
    r.set_defaults(fn=cmd_run)
    s = sp.add_parser("resume"); s.add_argument("run_id"); s.set_defaults(fn=cmd_resume)
    sp.add_parser("runs").set_defaults(fn=cmd_runs)
    s = sp.add_parser("report"); s.add_argument("run_id"); s.set_defaults(fn=cmd_report)
    s = sp.add_parser("purge", help="roll back all rows loaded by a run"); s.add_argument("run_id"); s.set_defaults(fn=cmd_purge)
    s = sp.add_parser("validate"); s.add_argument("config"); s.set_defaults(fn=cmd_validate)
    s = sp.add_parser("estimate", help="dry-run: sample generation + time estimate"); s.add_argument("config")
    s.add_argument("--sample", type=int, default=5000); s.set_defaults(fn=cmd_estimate)
    s = sp.add_parser("memory", help="show which scenarios were already ingested"); s.add_argument("config"); s.set_defaults(fn=cmd_memory)
    s = sp.add_parser("schema"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_schema)
    s = sp.add_parser("config-schema"); s.add_argument("--out"); s.set_defaults(fn=cmd_schema_json)
    s = sp.add_parser("config", help="list|use|show|import|export saved configs")
    s.add_argument("action", choices=["list", "use", "show", "import", "export"]); s.add_argument("arg", nargs="?"); s.set_defaults(fn=cmd_config)
    s = sp.add_parser("gen-scenarios", help="LLM-generate scenarios into the config")
    s.add_argument("--count", type=int, default=10); s.add_argument("--theme", default=""); s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--accept", action="store_true"); s.add_argument("--out"); s.set_defaults(fn=cmd_gen)
    s = sp.add_parser("serve", help="start the FastAPI UI"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    s = sp.add_parser("llm", help="Bedrock discovery / probe"); s.add_argument("action", choices=["discover", "probe"]); s.set_defaults(fn=cmd_llm)
    a = p.parse_args(argv)
    return int(a.fn(a) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
