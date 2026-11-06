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
        from .store import ResultStore
        ResultStore(args.store).save(result)
    if getattr(args, "publish", None):
        from .publish import publish
        paths["published"] = json.dumps(publish(result, args.publish.split(","), out_dir=out, region=args.region, project=args.project))
    if getattr(args, "summary_md", None):
        Path(args.summary_md).write_text(markdown_summary(result), encoding="utf-8")
    verdict = "PASSED" if result["passed"] else "FAILED"
    print(f"\n{verdict}: {result['suite']} ({result['run_id']})")
    for g in result["gates"]:
        mark = "ok  " if g["passed"] else ("WARN" if g["severity"] == "warn" else "FAIL")
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
        print(f"note: baseline {args.baseline} not found; gates using delta() treat the change as 0")
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
    from .report.text import _f
    from .runner import compare_results, load_result
    cur, base = load_result(args.current), load_result(args.baseline)
    comp = compare_results(cur, base, mapping=_kv(args.map))
    for t, gs in comp.items():
        print(f"\n{t}")
        for g, d in gs.items():
            if d.get("n"):
                print(f"  {g:28s} Δ {d['delta']:+.3f}  CI [{d['ci_low']:+.3f}, {d['ci_high']:+.3f}]  significant={_f(d['significant'])}"
                      f"  regressed={d.get('regressed', [])[:5]} {d.get('warning', '')}")
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
            print(markdown_summary(cur))
            print(f"report: {out / 'report.html'}")
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
    k = res["kappa"]
    print(f"{args.grader}: n={res['n']} accuracy={res['accuracy'] if res['accuracy'] is None else round(res['accuracy'], 3)} "
          f"kappa={k if k is None else round(k, 3)} -> {out}")
    if args.min_kappa is not None and (k is None or k < args.min_kappa):
        print(f"judge below threshold (kappa < {args.min_kappa}): gates on '{args.grader}' will be marked invalid when suite.min_judge_kappa is set")
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
    r.suite, r.telemetry = suite, None
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

    p = sub.add_parser("init", help="scaffold a starter evals project (providers, suite, sources, dataset, prompt, CI)")
    p.add_argument("directory", nargs="?", default="evals")
    p.add_argument("--force", action="store_true", help="overwrite files that already exist")
    p.set_defaults(fn=cmd_init)

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

    p = sub.add_parser("compare", help="paired comparison of two runs")
    p.add_argument("current")
    p.add_argument("baseline")
    p.add_argument("--map", action="append", help="target=baseline_target")
    p.set_defaults(fn=cmd_compare)

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
    p.add_argument("--min-kappa", type=float)
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
    from .scaffold import scaffold

    written, skipped = scaffold(Path(args.directory), force=args.force)
    for f in written:
        print(f"  created {f}")
    for f in skipped:
        print(f"  kept    {f} (exists; use --force to overwrite)")
    print(f"""
Next steps:
  cd {args.directory}
  genai-evals run suite.yaml            # runs offline with mock profiles, no keys needed
  cp .env.example .env                  # then fill in your own regions, projects, model ids, ARNs
  # edit suite.yaml: point targets and judges at your live profiles (see README.md in {args.directory})
""")
    return 0


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

        from .runner import SuiteError
        if isinstance(e, YAMLError):
            print(f"error: invalid YAML: {e}\nhint: quote values that contain ${{VAR}} inside {{...}} or [...]", file=sys.stderr)
            return 2
        if isinstance(e, (SuiteError, KeyError, FileNotFoundError, ValidationError, MissingEnvError)):
            print(f"error: {e}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
