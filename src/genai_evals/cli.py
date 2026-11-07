"""genai-evals command line."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import check_suite, load_providers, load_sources, load_suite


def _providers_path(args, suite_path: str | None = None) -> str | None:
    if getattr(args, "providers", None):
        return args.providers
    if os.environ.get("GENAI_EVALS_PROVIDERS"):
        return os.environ["GENAI_EVALS_PROVIDERS"]
    if suite_path:
        cand = Path(suite_path).parent / "providers.yaml"
        if cand.exists():
            return str(cand)
    return "providers.yaml" if Path("providers.yaml").exists() else None


def _kv(items: list[str] | None) -> dict[str, str]:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        if not v:
            raise SystemExit(f"expected key=value, got '{it}'")
        out[k] = v
    return out


def _telemetry(args):
    if not getattr(args, "otel", None):
        return None
    from .otel import Telemetry, configure
    configure("console" if args.otel == "console" else "otlp", None if args.otel in ("console", "otlp") else args.otel)
    return Telemetry()


def _finish(result: dict[str, Any], args) -> int:
    from .report import markdown_summary, write_outputs
    out = Path(args.out or f".genai-evals/runs/{result['run_id']}")
    paths = write_outputs(result, out, html=not args.no_html)
    if args.store:
        from .store import store_for
        store_for(args.store).save(result)
    if getattr(args, "publish", None):
        from .publish import publish
        paths["published"] = json.dumps(publish(result, args.publish.split(","), out_dir=out, region=args.region, project=args.project))
    if getattr(args, "summary_md", None):
        Path(args.summary_md).write_text(markdown_summary(result), encoding="utf-8")
    verdict = "PASSED" if result["passed"] else "FAILED"
    print(f"\n{verdict}: {result['suite']} ({result['run_id']})")
    for g in result["gates"]:
        status = g.get("status", "pass" if g["passed"] else "fail")
        mark = "ok  " if g["passed"] else ("WARN" if g["severity"] == "warn" else ("INVALID" if status == "invalid" else "FAIL"))
        print(f"  [{mark}] {g['gate']} @ {g['target']}: {g['rule']}" + (f"  ({g['message']})" if g["message"] else ""))
    for k, v in paths.items():
        print(f"  {k}: {v}")
    if not result["passed"]:
        return 1
    if getattr(args, "fail_on_warn", False) and any(not g["passed"] for g in result["gates"]):
        return 1
    return 0


# ---------------------------------------------------------------------------
def cmd_validate(args) -> int:
    suite = load_suite(args.suite)
    providers = load_providers(_providers_path(args, args.suite))
    problems = check_suite(suite, providers)
    from .config import load_cases
    try:
        n = len(load_cases(suite))
    except Exception as e:  # noqa: BLE001
        problems.append(f"dataset: {e}")
        n = 0
    from .graders import REGISTRY
    for g in suite.graders:
        if g.type not in REGISTRY:
            problems.append(f"grader '{g.name}': unknown type '{g.type}'")
    if suite.prompts_dir:
        from .prompts import PromptStore
        problems += [f"prompts: {p}" for p in PromptStore(suite.path(suite.prompts_dir)).check()]
    if problems:
        print("INVALID:\n  - " + "\n  - ".join(problems))
        return 1
    print(f"OK: suite '{suite.name}': {n} cases, {len(suite.targets)} targets, {len(suite.graders)} graders, {len(suite.gates)} gates")
    return 0


def cmd_run(args) -> int:
    from .runner import Runner, load_result
    suite = load_suite(args.suite)
    if args.repeats:
        suite.repeats = args.repeats
    providers = load_providers(_providers_path(args, args.suite))
    baseline = load_result(args.baseline) if args.baseline and Path(args.baseline).exists() else None
    if args.baseline and baseline is None:
        print(f"error: baseline {args.baseline} not found", file=sys.stderr)
        return 2
    if args.allow_missing_baseline:
        for g in suite.gates:
            g.allow_missing_baseline = True
    runner = Runner(suite, providers, prompt_overrides=_kv(args.prompt), use_cache=not args.no_cache, telemetry=_telemetry(args))
    result = runner.run(args.target or None, baseline, _kv(args.baseline_map))
    return _finish(result, args)


def cmd_online(args) -> int:
    from .online import run_online
    from .runner import load_result
    suite = load_suite(args.suite)
    providers = load_providers(_providers_path(args, args.suite))
    sources = load_sources(args.sources)
    baseline = load_result(args.baseline) if args.baseline and Path(args.baseline).exists() else None
    result = run_online(suite, providers, sources, baseline=baseline, telemetry=_telemetry(args))
    for s in result.get("sources", []):
        print("  source " + ", ".join(f"{k}={v}" for k, v in s.items()))
    return _finish(result, args)


def cmd_compare(args) -> int:
    from .comparison import compare_runs
    from .report.experiment import render_comparison_html, render_comparison_md
    from .runner import load_result
    cur, base = load_result(args.current), load_result(args.baseline)
    comp = compare_runs(cur, base, mapping=_kv(args.map), alpha=args.alpha, correction=args.correction)
    if not comp["results"]:
        print("error: no target in the current run matches a target in the baseline (use --map target=baseline_target)", file=sys.stderr)
        return 2
    md = render_comparison_md(comp)
    print(md)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "comparison.md").write_text(md, encoding="utf-8")
        (out / "comparison.html").write_text(render_comparison_html(comp, None), encoding="utf-8")
        (out / "comparison.json").write_text(json.dumps(comp, indent=1, default=str), encoding="utf-8")
        print(f"comparison: {out / 'comparison.html'}")
    return 1 if args.fail_on_regression and any(r["verdict"] == "regresses" for r in comp["results"].values()) else 0


def cmd_adapters(args) -> int:
    from . import plugins
    recs = plugins.load_validation(args.validation) if args.validation else []
    if args.action == "record":
        if not (args.kind and args.name and args.status):
            print("error: record needs --kind, --name and --status", file=sys.stderr)
            return 2
        rec = plugins.record_validation(args.validation or "validation.yaml", args.kind, args.name, args.status,
                                        environment=args.environment or "", evidence=args.evidence or "", by=args.by or "")
        print(f"recorded {rec['kind']} {rec['name']}: {rec['status']} in {args.validation or 'validation.yaml'}")
        return 0
    if args.markdown:
        print(plugins.markdown_table(recs), end="")
        return 0
    rows = [plugins.effective(k, n, recs) for (k, n) in sorted(plugins.catalog(), key=lambda x: (plugins.KINDS.index(x[0]), x[1]))
            if not args.kind or k == args.kind]
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        flag = " (experimental)" if r["experimental"] else ""
        origin = "" if r["origin"] == "builtin" else f"  [{r['origin']}]"
        print(f"  {r['kind']:9s} {r['name']:30s} {r['cloud']:9s} {r['status']}{flag}{origin}")
    return 0


def cmd_conformance(args) -> int:
    from . import conformance as cf
    specs = cf.builtin_specs()
    if args.spec:
        from .targets import import_ref
        specs = import_ref(args.spec, ".")
        specs = specs() if callable(specs) else specs
    if args.adapter:
        specs = [s for s in specs if getattr(s, "type", getattr(s, "provider", None)) in args.adapter]
    if args.kind:
        specs = [s for s in specs if isinstance(s, cf.SourceConformance) == (args.kind == "source")]
    res = cf.run(specs)
    print(cf.matrix_markdown(res))
    bad = [r for r in res if r.status == cf.FAIL]
    for r in bad:
        print(f"FAIL {r.kind} {r.adapter} / {r.check}: {r.detail}")
    if args.json:
        Path(args.json).write_text(json.dumps([r.__dict__ for r in res], indent=1), encoding="utf-8")
    return 1 if bad else 0


def cmd_result(args) -> int:
    from .results import load, validate
    res = load(args.result)
    if args.action == "validate":
        probs = validate(res)
        if probs:
            print(f"{args.result}: {len(probs)} problem(s) against result schema {res.get('schema_version')}")
            for p in probs[:30]:
                print(f"  - {p}")
            return 1
        up = f" (upgraded from {res['upgraded_from']})" if res.get("upgraded_from") else ""
        print(f"{args.result}: valid genai-evals result, schema {res['schema_version']}{up}")
        return 0
    if args.action == "schema":
        from .results import json_schema
        print(json.dumps(json_schema(), indent=1))
        return 0
    from .report.writers import writer
    out = Path(args.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    for fmt in (args.format or "csv").split(","):
        w = writer(fmt.strip())
        data = w.render(res)
        path = out / w.filename
        path.write_bytes(data) if isinstance(data, bytes) else path.write_text(data, encoding="utf-8")
        print(f"{fmt}: {path}")
    return 0


def cmd_experiment(args) -> int:
    from .experiment import default_out, load_experiment, reproduction_check, run_experiment, verify_manifest, write_experiment
    from .report.experiment import render_comparison_html, render_comparison_md
    if args.action == "report":
        d = Path(args.path)
        comp = json.loads((d / "comparison.json").read_text(encoding="utf-8"))
        res_p = d / "run" / "result.json"
        res = json.loads(res_p.read_text(encoding="utf-8")) if res_p.exists() else None
        (d / "comparison.md").write_text(render_comparison_md(comp), encoding="utf-8")
        (d / "comparison.html").write_text(render_comparison_html(comp, res), encoding="utf-8")
        print(render_comparison_md(comp))
        return 0
    if args.action == "rerun":
        d = Path(args.path)
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        exp_path = Path(args.experiment or manifest["experiment_path"])
        from .config import load_providers
        from .experiment import build_suite, providers_path
        exp = load_experiment(exp_path)
        suite, _, _ = build_suite(exp, load_providers(providers_path(exp, args.providers)))
        problems = verify_manifest(manifest, suite)
        if problems and not args.force:
            print("error: inputs changed since this experiment ran, so a re-run would not reproduce it:\n  - "
                  + "\n  - ".join(problems) + "\n(use --force to run anyway)", file=sys.stderr)
            return 2
        exp_file = exp_path
    else:
        exp_file = Path(args.path)
        exp = load_experiment(exp_file)
    result, comp, manifest_new = run_experiment(exp_file, providers_override=args.providers, use_cache=not args.no_cache,
                                                telemetry=_telemetry(args))
    out = Path(args.out) if args.out else default_out(exp.name)
    paths = write_experiment(out, exp_file, result, comp, manifest_new)
    print(render_comparison_md(comp))
    if args.action == "rerun":
        old = json.loads((Path(args.path) / "comparison.json").read_text(encoding="utf-8"))
        rows = reproduction_check(old, comp)
        (out / "reproduction.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        same = sum(1 for r in rows if r["status"] == "reproduced")
        print(f"\nReproduction of {args.path}: {same}/{len(rows)} comparisons reproduced "
              "(new delta inside the original interval, same significance)")
        for r in rows:
            if r["status"] != "reproduced":
                print(f"  [differs] {r['variant']} / {r['grader']}: {r['status']}"
                      + (f" (was {r['old_delta']:+.3f} [{r['old_ci'][0]:+.3f}, {r['old_ci'][1]:+.3f}], now {r['new_delta']:+.3f})"
                         if r.get("old_delta") is not None and r.get("old_ci") else ""))
        paths["reproduction"] = str(out / "reproduction.json")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    if getattr(args, "fail_on_regression", False) and any(r["verdict"] == "regresses" for r in comp["results"].values()):
        return 1
    return 0


def cmd_baseline(args) -> int:
    from .runner import baseline_from_result, load_result
    res = load_result(args.result)
    out = Path(args.out or f".genai-evals/baselines/{res['suite']}/{args.name}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(baseline_from_result(res), indent=1), encoding="utf-8")
    print(f"baseline '{args.name}' -> {out}")
    return 0


def cmd_report(args) -> int:
    from .report import write_outputs
    from .runner import load_result
    paths = write_outputs(load_result(args.result), args.out or Path(args.result).parent)
    print("\n".join(f"{k}: {v}" for k, v in paths.items()))
    return 0


def cmd_prompts(args) -> int:
    from .prompts import PromptStore, push
    store = PromptStore(args.dir, args.lock)
    if args.action == "list":
        lock = store.read_lock().get("prompts", {})
        for pid in store.ids():
            st = lock.get(pid, {}).get("stages", {})
            print(f"{pid}: versions {', '.join(store.versions(pid))}  stages " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())))
        return 0
    if args.action == "check":
        problems = store.check()
        warn = []
        lock = store.read_lock().get("prompts", {})
        for pid, entry in lock.items():
            for stage in ("dev", "staging"):
                v = entry.get("stages", {}).get(stage)
                if v and v != entry.get("stages", {}).get("prod"):
                    ch = store.dpdp_changes(f"{pid}@{v}")
                    if ch["data_elements"] or ch["purposes"]:
                        warn.append(f"{pid}@{v} ({stage}) adds data elements {sorted(ch['data_elements'])} / purposes {sorted(ch['purposes'])} "
                                    "vs prod: update the consent notice, consent ledger and DPDP eval suite before promotion")
        for w in warn:
            print("DPDP: " + w)
        if problems:
            print("FAIL:\n  - " + "\n  - ".join(problems))
            return 1
        if warn and args.strict_dpdp:
            return 2
        print("OK: prompts match prompts.lock")
        return 0
    if args.action == "lock":
        r = store.lock()
        print(f"locked {len(r['added'])} new version(s): {', '.join(r['added']) or '-'}")
        return 0
    if args.action == "promote":
        ch = store.dpdp_changes(f"{args.prompt}@{args.version}", args.stage if args.stage != "prod" else "prod")
        if args.stage == "prod" and (ch["data_elements"] or ch["purposes"]) and not args.ack_dpdp:
            print(f"BLOCKED: {args.prompt}@{args.version} adds data elements {sorted(ch['data_elements'])} / purposes {sorted(ch['purposes'])}. "
                  "Update the consent notice and ledger, then re-run with --ack-dpdp.")
            return 2
        prev = store.promote(args.prompt, args.version, args.stage)
        print(f"{args.prompt}: {args.stage} {prev or '-'} -> {args.version}")
        return 0
    if args.action == "diff":
        print(store.diff(args.a, args.b) or "(no file differences)")
        if args.suite:
            from .report.text import markdown_summary
            from .runner import Runner
            suite = load_suite(args.suite)
            providers = load_providers(_providers_path(args, args.suite))
            pa, va = args.a.split("@")
            _, vb = args.b.split("@")
            base = Runner(suite, providers, prompt_overrides={pa: va}).run(args.target or None)
            cur = Runner(suite, providers, prompt_overrides={pa: vb}).run(args.target or None, baseline=base)
            out = Path(args.out or f".genai-evals/prompt-diff/{pa}_{va}_vs_{vb}")
            from .report import write_outputs
            write_outputs(cur, out)
            from .comparison import compare_runs
            from .report.experiment import render_comparison_html, render_comparison_md
            comp = compare_runs(cur, base, min_n=suite.min_n)
            comp["name"] = f"{pa}@{va} vs @{vb}"
            (out / "comparison.md").write_text(render_comparison_md(comp), encoding="utf-8")
            (out / "comparison.html").write_text(render_comparison_html(comp, None), encoding="utf-8")
            print(markdown_summary(cur))
            print(render_comparison_md(comp))
            print(f"report: {out / 'report.html'} · comparison: {out / 'comparison.html'}")
            return 0 if cur["passed"] else 1
        return 0
    if args.action == "push":
        rp = store.render(args.prompt, args.stage)
        res = push(rp, args.to, dry_run=not args.apply, region=args.region, project=args.project, location=args.location)
        print(json.dumps(res, indent=1, default=str))
        return 0
    raise SystemExit(f"unknown prompts action {args.action}")


def cmd_calibrate(args) -> int:
    from .calibration import calibrate, read_labels
    from .runner import load_result
    res = calibrate(load_result(args.result), args.grader, read_labels(args.labels))
    out = Path(args.out or "calibration.json")
    existing = json.loads(out.read_text()) if out.exists() else {"graders": []}
    existing["graders"] = [g for g in existing.get("graders", []) if g["grader"] != args.grader] + [res]
    out.write_text(json.dumps(existing, indent=1), encoding="utf-8")
    k, ci = res["kappa"], res.get("kappa_ci")
    ci_txt = f" (95% CI {ci[0]:.2f}–{ci[1]:.2f})" if ci else ""
    print(f"{args.grader}: n={res['n']} accuracy={res['accuracy'] if res['accuracy'] is None else round(res['accuracy'], 3)} "
          f"kappa={k if k is None else round(k, 3)}{ci_txt} -> {out}")
    for w in res.get("warnings", []):
        print(f"  warning: {w}")
    if args.min_kappa is not None:
        from .calibration import untrusted_graders
        why = untrusted_graders({args.grader: res}, args.min_kappa, args.min_n).get(args.grader)
        if k is None or why:
            print(f"judge not trusted ({why or 'no kappa'}): gates on '{args.grader}' are marked invalid when "
                  "suite.min_judge_kappa is set")
            return 1
    return 0


def cmd_promptfoo(args) -> int:
    from .interop import import_results, write_config
    if args.action == "export":
        suite = load_suite(args.suite)
        notes = write_config(suite, load_providers(_providers_path(args, args.suite)), args.out)
        print(f"wrote {args.out}" + "".join(f"\n  note: {n}" for n in notes))
        return 0
    samples, scores = import_results(args.results)
    from .config import GraderSpec, SuiteSpec
    from .models import Case
    from .runner import Runner
    names = sorted({s.grader for s in scores})
    suite = SuiteSpec(name=args.name or "promptfoo-import", graders=[GraderSpec(name=n, type="imported") for n in names], tokenize=True)
    r = Runner.__new__(Runner)
    from .config import ProvidersConfig
    r.suite, r.telemetry, r.providers = suite, None, ProvidersConfig()
    from .dpdp import build_tokenizer
    r.tokenizer = build_tokenizer({})
    from .graders import GradeContext
    cases = [Case(id=c, input="") for c in sorted({s.case_id for s in samples})]
    targets = [{"name": t, "kind": "promptfoo", "profile": None, "family": None, "framework": None, "prompt": None} for t in sorted({s.target for s in samples})]
    result = r.finish(cases, targets, samples, scores, GradeContext(suite=suite), None, None, extra={"mode": "import:promptfoo"})
    return _finish(result, args)


def cmd_agentcore(args) -> int:
    from .interop import package_agentcore_evaluator
    suite = load_suite(args.suite)
    g = next((g for g in suite.graders if g.name == args.grader), None)
    if not g:
        raise SystemExit(f"grader '{args.grader}' not in suite")
    out = package_agentcore_evaluator(g.model_dump(), args.out)
    print(f"evaluator package -> {out}")
    return 0


def cmd_check_config(args) -> int:
    providers = load_providers(args.providers)
    problems = []
    for name, p in providers.profiles.items():
        if p.provider == "openai" and p.options.get("store", False):
            problems.append(f"{name}: store=true keeps prompts and outputs as stored responses; set store: false for evals on customer data")
        if p.provider != "mock" and not p.pricing:
            problems.append(f"{name}: no pricing, so cost gates and the quality-cost chart are blank for this profile (warning)")
    for pr in problems:
        print(("WARN: " if "warning" in pr else "FAIL: ") + pr)
    return 1 if any("warning" not in p for p in problems) else 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="genai-evals", description="Evals as code for GenAI apps and agents")
    ap.add_argument("--version", action="version", version=f"genai-evals {__version__}")
    ap.add_argument("--env-file", help="load KEY=VALUE settings from this file (default: ./.env if it exists); "
                    "existing environment variables win")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="scaffold a runnable evals project from an eval pack (runs it offline and writes a sample report)")
    p.add_argument("directory", nargs="?", default="evals")
    p.add_argument("--pack", default="rag-knowledge-assistant", help="eval pack (genai-evals pack list)")
    p.add_argument("--overlay", action="append", help="optional industry overlay of the pack")
    p.add_argument("--force", action="store_true", help="write into a non-empty folder")
    p.add_argument("--no-run", action="store_true", help="don't run the offline demo")
    p.add_argument("--list", action="store_true", help="list packs and exit")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("pack", help="list eval packs, show one, regenerate its docs, or validate a pack folder")
    p.add_argument("action", choices=["list", "info", "validate", "docs"])
    p.add_argument("pack", nargs="?", help="pack name or folder")
    p.add_argument("--no-run", action="store_true", help="validate: skip the offline runs")
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("doctor", help="check a project against the repeatability bar: offline demo, live provider and telemetry, report, gate, handover")
    p.add_argument("directory", nargs="?", default=".")
    p.add_argument("--no-run", action="store_true", help="don't re-run the offline demo")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    def outputs(p):
        p.add_argument("--out", help="output folder (default .genai-evals/runs/<run_id>)")
        p.add_argument("--no-html", action="store_true")
        p.add_argument("--store", help="results store URL, e.g. sqlite:///.genai-evals/results.db or postgresql+psycopg://...")
        p.add_argument("--publish", help="comma list: emf, cloudwatch, cloud_monitoring, otlp")
        p.add_argument("--region", help="AWS region for --publish cloudwatch")
        p.add_argument("--project", help="GCP project for --publish cloud_monitoring")
        p.add_argument("--otel", help="emit OTel spans/metrics: console | otlp | <collector URL>")
        p.add_argument("--summary-md", help="also write the markdown summary here (PR comment)")
        p.add_argument("--fail-on-warn", action="store_true")

    p = sub.add_parser("validate", help="check a suite, its providers, dataset, gates and prompts")
    p.add_argument("suite")
    p.add_argument("--providers")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("run", help="run a suite offline and evaluate gates")
    p.add_argument("suite")
    p.add_argument("--providers")
    p.add_argument("--target", action="append", help="run only these targets")
    p.add_argument("--baseline", help="result.json or baseline JSON to compare against")
    p.add_argument("--baseline-map", action="append", help="target=baseline_target")
    p.add_argument("--prompt", action="append", help="override a prompt version: claims_agent=1.4.0")
    p.add_argument("--repeats", type=int)
    p.add_argument("--allow-missing-baseline", action="store_true",
                   help="let delta()/significant() gates pass when there is no baseline (only for the run that creates the first one)")
    p.add_argument("--no-cache", action="store_true")
    outputs(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("online", help="grade production traffic pulled from native telemetry sources")
    p.add_argument("--sources", required=True)
    p.add_argument("--suite", required=True)
    p.add_argument("--providers")
    p.add_argument("--baseline")
    outputs(p)
    p.set_defaults(fn=cmd_online)

    p = sub.add_parser("compare", help="side-by-side comparison of two runs: quality, cost, latency, critical cases, slices")
    p.add_argument("current")
    p.add_argument("baseline")
    p.add_argument("--map", action="append", help="target=baseline_target")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--correction", choices=["bonferroni", "none"], default="bonferroni")
    p.add_argument("--out", help="also write comparison.html / .md / .json here")
    p.add_argument("--fail-on-regression", action="store_true")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("adapters", help="list adapters with their maturity, or record a live / customer validation")
    p.add_argument("action", nargs="?", choices=["list", "record"], default="list")
    p.add_argument("--kind", choices=["provider", "source", "target", "grader", "store", "report"])
    p.add_argument("--name")
    p.add_argument("--status", choices=["live_tested", "customer_validated"])
    p.add_argument("--environment", help="record: where it was run, e.g. 'customer sandbox, ap-south-1'")
    p.add_argument("--evidence", help="record: a result.json, run id or ticket")
    p.add_argument("--by", help="record: who validated it")
    p.add_argument("--validation", help="validation.yaml of the project (default for record: ./validation.yaml)")
    p.add_argument("--markdown", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_adapters)

    p = sub.add_parser("conformance", help="run the adapter conformance kit (built-in adapters, or your own with --spec)")
    p.add_argument("--spec", help="module:SPECS with SourceConformance / ProviderConformance entries (a partner adapter)")
    p.add_argument("--adapter", action="append")
    p.add_argument("--kind", choices=["source", "provider"])
    p.add_argument("--json", help="also write the results here")
    p.set_defaults(fn=cmd_conformance)

    p = sub.add_parser("result", help="validate a result against the versioned schema, print the schema, or export it")
    p.add_argument("action", choices=["validate", "export", "schema"])
    p.add_argument("result", nargs="?", default="result.json")
    p.add_argument("--format", help="export: comma list of csv, jsonl, promptfoo, json, markdown, junit, html, scorecard, or a plugin")
    p.add_argument("--out", help="export: output folder (default: current folder)")
    p.set_defaults(fn=cmd_result)

    p = sub.add_parser("experiment", help="baseline vs candidate changes on the same cases, compared and saved for reproduction")
    p.add_argument("action", choices=["run", "rerun", "report"],
                   help="run <experiment.yaml> | rerun <experiment folder> | report <experiment folder>")
    p.add_argument("path")
    p.add_argument("--providers")
    p.add_argument("--experiment", help="rerun: experiment file to use if it moved since the original run")
    p.add_argument("--out", help="output folder (default .genai-evals/experiments/<name>/<timestamp>)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--force", action="store_true", help="rerun even if inputs changed")
    p.add_argument("--fail-on-regression", action="store_true", help="exit 1 if any variant regresses")
    p.add_argument("--otel", help="emit OTel spans/metrics: console | otlp | <collector URL>")
    p.set_defaults(fn=cmd_experiment)

    p = sub.add_parser("baseline", help="save a run as a named baseline")
    p.add_argument("action", choices=["promote"])
    p.add_argument("result")
    p.add_argument("--name", default="main")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("report", help="re-render HTML / markdown / JUnit from a result.json")
    p.add_argument("result")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("prompts", help="prompts as code: list, check, lock, promote, diff, push")
    p.add_argument("action", choices=["list", "check", "lock", "promote", "diff", "push"])
    p.add_argument("args", nargs="*")
    p.add_argument("--dir", default="prompts")
    p.add_argument("--lock")
    p.add_argument("--stage", default="prod")
    p.add_argument("--suite", help="diff: run this suite against both versions and gate the candidate")
    p.add_argument("--providers")
    p.add_argument("--target", action="append")
    p.add_argument("--out")
    p.add_argument("--to", choices=["bedrock", "vertex", "langfuse"])
    p.add_argument("--apply", action="store_true", help="push: actually call the registry (default is a dry run)")
    p.add_argument("--region")
    p.add_argument("--project")
    p.add_argument("--location")
    p.add_argument("--strict-dpdp", action="store_true")
    p.add_argument("--ack-dpdp", action="store_true")
    p.set_defaults(fn=_prompts_dispatch)

    p = sub.add_parser("calibrate", help="measure judge agreement with human labels")
    p.add_argument("result")
    p.add_argument("--grader", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--out")
    p.add_argument("--min-kappa", type=float, help="trust the judge only if the kappa 95%% CI lower bound clears this")
    p.add_argument("--min-n", type=int, default=30, help="fewest human labels before the judge can be trusted (default 30)")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("promptfoo", help="export a suite to promptfooconfig.yaml, or import Promptfoo results")
    p.add_argument("action", choices=["export", "import"])
    p.add_argument("path", help="suite.yaml (export) or results.json (import)")
    p.add_argument("-o", "--output", dest="pf_out", default="promptfooconfig.yaml")
    p.add_argument("--providers")
    p.add_argument("--name")
    outputs(p)
    p.set_defaults(fn=_promptfoo_dispatch)

    p = sub.add_parser("agentcore", help="package a grader as an AgentCore Evaluations code-based evaluator")
    p.add_argument("action", choices=["package-evaluator"])
    p.add_argument("suite")
    p.add_argument("--grader", required=True)
    p.add_argument("--out", default="agentcore-evaluator")
    p.set_defaults(fn=cmd_agentcore)

    p = sub.add_parser("check-config", help="production guardrails for providers.yaml")
    p.add_argument("providers")
    p.set_defaults(fn=cmd_check_config)
    return ap


def _prompts_dispatch(args) -> int:
    a = args.args
    if args.action == "promote":
        if len(a) != 2:
            raise SystemExit("usage: genai-evals prompts promote <prompt_id> <version> [--stage prod]")
        args.prompt, args.version = a
    elif args.action == "diff":
        if len(a) != 2:
            raise SystemExit("usage: genai-evals prompts diff <id@a> <id@b> [--suite suite.yaml]")
        args.a, args.b = a
    elif args.action == "push":
        if len(a) != 1 or not args.to:
            raise SystemExit("usage: genai-evals prompts push <prompt_id[@version]> --to bedrock|vertex|langfuse [--apply]")
        args.prompt = a[0]
    return cmd_prompts(args)


def _promptfoo_dispatch(args) -> int:
    if args.action == "export":
        args.suite, args.out = args.path, args.pf_out
    else:
        args.results = args.path
    return cmd_promptfoo(args)


def cmd_init(args) -> int:
    from . import packs
    if args.list:
        return cmd_pack(argparse.Namespace(action="list", pack=None, no_run=True))
    dest = Path(args.directory)
    try:
        packs.scaffold(args.pack, dest, overlays=args.overlay or [], force=args.force)
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    m = packs.manifest(dest)
    print(f"Created {dest}/ from the {m['name']} pack ({m['version']}): {m['title']}")
    if args.overlay:
        print(f"  overlays: {', '.join(args.overlay)}")
    if not args.no_run:
        res = packs.run_offline(dest, dest / "out")
        r = res["offline"]
        print(f"\nOffline run ({len(r['cases'])} synthetic cases, simulated system, mock judges): "
              f"{'all blocking gates pass' if r['passed'] else 'some gates fail'}")
        print(f"  sample report:    {dest / 'out' / 'offline' / 'report.html'}")
        print(f"  sample scorecard: {dest / 'out' / 'offline' / 'scorecard.html'}")
        if "online" in res:
            print(f"  monitoring demo:  {dest / 'out' / 'online' / 'scorecard.html'} (recorded telemetry)")
    t = [x["file"] for x in m.get("targets", []) if x["file"] != "targets/offline.yaml"]
    print(f"""
Next steps (RUNBOOK.md has the full path):
  cd {dest}
  genai-evals run suite.yaml --allow-missing-baseline      # offline again, any time
  cp .env.example .env                                     # then point the pack at the customer's system:
      EVAL_TARGETS={t[0] if t else 'targets/http.yaml'}           # one of: {', '.join(t)}
      GENAI_EVALS_PROVIDERS=providers.live.yaml               # live judges and models
  genai-evals doctor                                       # what's left before handover
""")
    return 0


def cmd_pack(args) -> int:
    from . import packs, plugins
    if args.action == "list":
        for n in packs.available():
            m = packs.manifest(packs.resolve(n))
            ov = ", ".join(o["name"] for o in m.get("overlays", [])) or "none"
            print(f"  {n:26s} {m['version']:7s} {m['title']}  (overlays: {ov})")
        return 0
    if not args.pack:
        print("error: name a pack or pack folder", file=sys.stderr)
        return 2
    d = packs.resolve(args.pack)
    m = packs.manifest(d)
    if args.action == "docs":
        print("wrote " + ", ".join(packs.write_generated_docs(d)) + f" in {d}")
        return 0
    if args.action == "info":
        print(f"{m['title']} ({m['name']} {m['version']}, workflow: {m['workflow']})\n  {m['summary']}\n")
        print("Targets (set EVAL_TARGETS):")
        for t in m["targets"]:
            print(f"  {t['file']:30s} {t['system']}")
        print("\nGraders: " + ", ".join(m["graders"]))
        print("Failure categories:")
        for k, v in m["failure_categories"].items():
            print(f"  {k}: {v}")
        print("Gates:")
        for k, g in m["gates"].items():
            print(f"  {k}{' (warn)' if g.get('severity') == 'warn' else ''}: {g['rule']}")
        recs = plugins.load_validation(d / "validation.yaml") if (d / "validation.yaml").exists() else []
        print("\nCompatibility (adapter status):")
        for kind, key in (("provider", "providers"), ("source", "sources"), ("store", "stores")):
            items = [f"{n} [{plugins.effective(kind, n, recs)['status']}]" for n in m["compatibility"].get(key, [])]
            print(f"  {key}: {', '.join(items)}")
        print("Validation:")
        for k, v in m["validation"].items():
            print(f"  {k}: {v}")
        if m.get("overlays"):
            print("Overlays: " + "; ".join(f"{o['name']}: {o['description']}" for o in m["overlays"]))
        return 0
    checks = packs.validate_pack(d, run=not args.no_run)
    print(f"pack {m['name']} {m['version']} ({d})")
    print(packs.checks_text(checks))
    return 1 if any(c.ok is False for c in checks) else 0


def cmd_doctor(args) -> int:
    from . import packs
    checks = packs.doctor(Path(args.directory), run=not args.no_run)
    if args.json:
        print(json.dumps([c.__dict__ for c in checks], indent=1))
    else:
        done = sum(1 for c in checks if c.ok)
        total = sum(1 for c in checks if c.ok is not None)
        print(f"Engagement readiness for {Path(args.directory).resolve().name}: {done}/{total}")
        print(packs.checks_text(checks))
    return 0 if all(c.ok is not False for c in checks) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from .config import MissingEnvError, load_env_file

    env_file = args.env_file or (".env" if Path(".env").exists() else None)
    if env_file:
        if not Path(env_file).exists():
            print(f"error: env file {env_file} not found", file=sys.stderr)
            return 2
        load_env_file(env_file)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001
        from pydantic import ValidationError
        from yaml import YAMLError

        from .config import DatasetError
        from .runner import SuiteError
        if isinstance(e, DatasetError):
            print(f"error: {e}", file=sys.stderr)
            return 2
        if isinstance(e, YAMLError):
            print(f"error: invalid YAML: {e}\nhint: quote values that contain ${{VAR}} inside {{...}} or [...]", file=sys.stderr)
            return 2
        if isinstance(e, (SuiteError, KeyError, FileNotFoundError, ValidationError, MissingEnvError)):
            print(f"error: {e}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
