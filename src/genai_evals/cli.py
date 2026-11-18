"""genai-evals command line."""
from __future__ import annotations

import argparse
import json
import os
import re
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
        mark = "ok  " if g["passed"] else ("INFO" if g["severity"] == "info" else ("WARN" if g["severity"] == "warn"
                                           else ("INVALID" if status == "invalid" else "FAIL")))
        print(f"  [{mark}] {g['gate']} @ {g['target']}: {g['rule']}" + (f"  ({g['message']})" if g["message"] else ""))
    for w in result.get("warnings") or []:
        print(f"  note: {w}")
    for k, v in paths.items():
        print(f"  {k}: {v}")
    if not result["passed"]:
        return 1
    if getattr(args, "fail_on_warn", False) and any(not g["passed"] and g["severity"] != "info" for g in result["gates"]):
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


def cmd_sensitivity(args) -> int:
    import yaml

    from .runner import SuiteError, load_result
    from .sensitivity import sensitivity
    variants: list = list(args.variant or [])
    if args.variants:
        data = yaml.safe_load(Path(args.variants).read_text(encoding="utf-8")) or []
        variants += data.get("variants", []) if isinstance(data, dict) else data
    if not variants:
        raise SystemExit("sensitivity needs --variant TEXT (repeatable) or --variants file.yaml")
    suite = load_suite(args.suite)
    try:
        rep = sensitivity(suite, load_providers(_providers_path(args, args.suite)), load_result(args.result), args.grader,
                          variants, baseline=load_result(args.baseline) if args.baseline else None,
                          max_flip_rate=args.max_flip_rate)
    except (SuiteError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    for r in rep["variants"]:
        k = f"{r['kappa']:.2f}" if r["kappa"] is not None else "n/a"
        fr = f"{r['flip_rate']:.0%}" if r["flip_rate"] is not None else "n/a"
        mo, mv = r["mean_original"], r["mean_variant"]
        print(f"{r['variant']}: {r['flips']}/{r['units']} verdicts flipped ({fr}: {r['pass_to_fail']} pass->fail, "
              f"{r['fail_to_pass']} fail->pass), kappa vs original {k}"
              + (f", mean {mo:.2f} -> {mv:.2f}" if mo is not None and mv is not None else ""))
        for g in r["gates_changed"]:
            print(f"  gate changed: {g}")
    if rep.get("gates_not_compared"):
        print(f"  not compared (pass --baseline to include): {', '.join(rep['gates_not_compared'])}")
    verdict = rep["robust"]
    print(("robust: rewording didn't change the conclusion" if verdict else
           f"sensitive: rewording flipped more than {args.max_flip_rate:.0%} of verdicts or changed a gate; the result "
           "depends on the rubric's wording. Tighten the rubric (or use a checklist) before trusting the gate"))
    return 0 if verdict else 1


def cmd_power(args) -> int:
    from .power import SAFETY_GUIDANCE, plan
    if not (args.delta or args.half_width or args.n):
        raise SystemExit("power needs --delta, --half-width or --n")
    p = plan(args.rate, delta=args.delta, half_width=args.half_width, n=args.n, discordant=args.discordant,
             alpha=args.alpha, power=args.power)
    if args.json:
        print(json.dumps(p, indent=1))
        return 0
    print(f"pass rate {args.rate:.0%}, alpha {args.alpha}, power {args.power:.0%}")
    if "interval" in p:
        print(f"  95% interval of ±{args.half_width * 100:.0f} points: {p['interval']['cases']} cases")
    if "detect" in p:
        d = p["detect"]
        print(f"  detect a change of {args.delta * 100:+.0f} points: {d['unpaired_cases_per_arm']} cases per arm (independent samples)")
        if "paired_cases" in d:
            print(f"    on the same cases, with {args.discordant:.0%} of verdicts differing: {d['paired_cases']} cases (paired)")
        else:
            print("    on the same cases it's usually far fewer: pass --discordant (share of cases whose verdict changes)")
    if "with_n" in p:
        w = p["with_n"]
        print(f"  with {args.n} cases: interval ±{w['interval_half_width'] * 100:.0f} points; smallest detectable change "
              f"{w['mde_unpaired'] * 100:.0f} points unpaired" + (f", {w['mde_paired'] * 100:.0f} paired" if "mde_paired" in w else ""))
    print(f"  for a safety claim, Anthropic suggests about {SAFETY_GUIDANCE['multi_turn']} multi-turn or "
          f"{SAFETY_GUIDANCE['single_turn']} single-turn items")
    return 0


def cmd_regrade(args) -> int:
    from .runner import Runner, SuiteError, load_result
    suite = load_suite(args.suite)
    providers = load_providers(_providers_path(args, args.suite))
    result = load_result(args.result)
    baseline = load_result(args.baseline) if args.baseline else None
    try:
        runner = Runner(suite, providers, use_cache=False, telemetry=_telemetry(args))
        new = runner.regrade(result, graders=args.grader or None, only=args.only, attempts=args.attempts,
                             backoff_s=args.backoff, force=args.force, baseline=baseline)
    except SuiteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    r = new["regrade"]
    print(f"re-graded {r['units']} unit(s) ({r['only']}) for {', '.join(r['graders'])} in {r['attempts']} attempt(s): "
          f"{r['recovered']} recovered, {r['still_transient']} still transient, {r['persistent']} persistent")
    if args.out is None:
        args.out = str(Path(args.result).parent.with_name(Path(args.result).parent.name + "-regraded"))
    return _finish(new, args)


def cmd_calibrate(args) -> int:
    from .calibration import calibrate, compare_judges, consensus, read_reviews, read_sample_info, reviewer_agreement
    from .runner import load_result
    reviews = read_reviews(args.labels)
    labels, _ = consensus(reviews, args.rule)
    human = reviewer_agreement(reviews, args.rule) if any(len(v) > 1 for v in reviews.values()) else None
    result = load_result(args.result)
    validation = None
    if args.validation_labels:
        validation = consensus(read_reviews(args.validation_labels), args.rule)[0]
    if args.compare_to:
        strong = load_result(args.compare_result) if args.compare_result else result
        cmp = compare_judges((result, args.grader), (strong, args.compare_to), labels, margin=args.margin,
                             min_kappa=args.min_kappa, min_n=args.min_n, info=read_sample_info(args.labels),
                             validation=validation,
                             validation_info=read_sample_info(args.validation_labels) if args.validation_labels else None,
                             max_judge_failure=args.max_judge_failure)
        if args.out:
            Path(args.out).write_text(json.dumps(cmp, indent=1), encoding="utf-8")
        if "kappa_cheap" in cmp:
            lo, hi = cmp["kappa_diff_ci"]
            print(f"{args.grader} vs {args.compare_to} on {cmp['n']} labelled units ({cmp['basis']}): kappa "
                  f"{cmp['kappa_cheap']:.2f} vs {cmp['kappa_strong']:.2f} (difference {cmp['kappa_diff']:+.2f}, "
                  f"95% CI {lo:+.2f} to {hi:+.2f}); judge cost ${cmp['judge_cost_cheap']:.4f} vs ${cmp['judge_cost_strong']:.4f}")
        for role in ("cheap", "strong"):
            cv = cmp["coverage"][role]
            if cv["gradable"]:
                print(f"  {cmp[role]}: graded {cv['scored']}/{cv['gradable']} labelled units ({cv['failure_rate']:.0%} failed: "
                      f"{cv['transient_error']} transient errors, {cv['persistent_error']} persistent errors, "
                      f"{cv['unknown']} unknown, {cv['skipped']} skipped, {cv['missing']} missing)"
                      + (f"; {cv['target_error']} target errors left out" if cv["target_error"] else ""))
        if cmp.get("kappa_diff_ci_imputed"):
            ilo, ihi = cmp["kappa_diff_ci_imputed"]
            print(f"  counting ungraded units as disagreements: difference {cmp['kappa_diff_imputed']:+.2f}, 95% CI {ilo:+.2f} to {ihi:+.2f}")
        if cmp.get("note"):
            print("note: " + cmp["note"])
        label = {"approved": "approved: ", "retry_first": "not decided yet (retry first): ",
                 "not_approved": "not approved: "}[cmp.get("decision", "approved" if cmp["approved"] else "not_approved")]
        print(label + cmp["reason"])
        if cmp.get("retry"):
            print("  retry: " + cmp["retry"])
        return 0 if cmp["approved"] else 1
    stress = list(result.get("stress_tags") or ["edge", "adversarial"])
    res = calibrate(result, args.grader, labels, human, sample_info=read_sample_info(args.labels), validation=validation,
                    validation_info=read_sample_info(args.validation_labels) if args.validation_labels else None,
                    stress_tags=stress)
    out = Path(args.out or "calibration.json")
    existing = json.loads(out.read_text()) if out.exists() else {"graders": []}
    existing["graders"] = [g for g in existing.get("graders", []) if g["grader"] != args.grader] + [res]
    out.write_text(json.dumps(existing, indent=1), encoding="utf-8")
    k, ci = res["kappa"], res.get("kappa_ci")
    ci_txt = f" (95% CI {ci[0]:.2f}–{ci[1]:.2f})" if ci else ""
    print(f"{args.grader}: n={res['n']} accuracy={res['accuracy'] if res['accuracy'] is None else round(res['accuracy'], 3)} "
          f"kappa={k if k is None else round(k, 3)}{ci_txt} on the {res['sample']['design']} calibration sample "
          f"(unit: case and target{', interval stratified' if res['sample'].get('stratified_interval') else ''}) -> {out}")
    w = res.get("weighted")
    if w and w.get("kappa") is not None:
        print(f"  production-weighted: kappa {w['kappa']:.2f}, accuracy {w['accuracy']:.0%}, human pass rate "
              f"{w['human_pass_rate']:.0%} (sample: {res['human_pass_rate']:.0%})")
    v = res.get("validation")
    if v and v.get("n"):
        vk = v.get("kappa")
        print(f"  representative validation sample: n={v['n']}, accuracy {v['accuracy']:.0%}, "
              f"kappa {'n/a' if vk is None else f'{vk:.2f}'}, human pass rate {v['human_pass_rate']:.0%}")
    for name, d in (res.get("slices") or {}).items():
        if d.get("n", 0) >= 5:
            dk = d.get("kappa")
            print(f"  slice {name}: n={d['n']}, accuracy {d['accuracy']:.0%}, kappa {'n/a' if dk is None else f'{dk:.2f}'}")
    if human:
        fk = human.get("fleiss_kappa")
        print(f"  reviewers: {human['multi_reviewed']} items with 2+ reviewers, raw agreement "
              f"{human['raw_agreement']:.0%}" + (f", Fleiss' kappa {fk:.2f}" if fk is not None else "") +
              f"; {len(human['disputed'])} disputed")
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


def cmd_labels(args) -> int:
    from . import labels as lb
    from .calibration import consensus, read_reviews, reviewer_agreement
    if args.action == "sheet":
        from .runner import load_result
        if not args.grader:
            raise SystemExit("labels sheet needs --grader")
        design = args.design or ("random" if args.no_stratify else "stratified")
        items = lb.sample_items(load_result(args.path), args.grader, n=args.n, seed=args.seed, design=design)
        reviewers = [r.strip() for r in (args.reviewers or "").split(",") if r.strip()]
        out = lb.write_sheet(items, args.out or "labels-sheet.csv", reviewers)
        print(f"wrote {out}: {len(items)} items x {max(1, len(reviewers))} reviewer(s), {design} sample. Judge verdicts are "
              "not included, so labels stay independent.")
        if design == "stratified":
            print("  this sample is for calibration (balanced, by tag); for a production estimate also label a random one: "
                  "--design random")
        return 0
    if args.action == "queue":
        from .runner import load_result
        items = lb.review_queue(load_result(args.path), [args.grader] if args.grader else None, limit=args.n)
        reviewers = [r.strip() for r in (args.reviewers or "").split(",") if r.strip()]
        out = lb.write_sheet(items, args.out or "review-queue.csv", reviewers)
        counts: dict[str, int] = {}
        for it in items:
            k = it["stratum"].split("|")[1]
            counts[k] = counts.get(k, 0) + 1
        print(f"wrote {out}: {len(items)} item(s) the judges were unsure about ("
              + (", ".join(f"{n} {k}" for k, n in counts.items()) or "none") + "). A targeted sample: use it to decide "
              "these cases and fix rubrics, not to calibrate.")
        return 0
    if args.action == "guide":
        if not args.grader:
            raise SystemExit("labels guide needs --grader")
        suite = load_suite(args.path)
        text = lb.labelling_guide(suite, args.grader)
        out = Path(args.out or f"labelling-guide-{args.grader}.md")
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}: fill in an example at each level before reviewers start")
        return 0
    reviews = read_reviews(args.path)
    ag = reviewer_agreement(reviews, args.rule)
    labels, disputed = consensus(reviews, args.rule)
    if args.out:
        lb.write_consensus(labels, args.out)
    fk, ra = ag["fleiss_kappa"], ag["raw_agreement"]
    print(f"{ag['items']} items, {ag['multi_reviewed']} with 2+ reviewers; raw agreement "
          f"{'n/a' if ra is None else f'{ra:.0%}'}; Fleiss' kappa {'n/a' if fk is None else f'{fk:.2f}'}; "
          f"{ag['labelled']} labelled by {args.rule}, {len(disputed)} disputed")
    for r, d in ag["reviewers"].items():
        print(f"  {r}: {d['n']} items, {d['agreement_with_consensus']:.0%} agree with consensus")
    for d in disputed[:20]:
        print(f"  disputed: {d['case_id']} [{d['target']}] pass {d['pass']} / fail {d['fail']}")
    for w in ag["warnings"]:
        print(f"  warning: {w}")
    if args.out:
        print(f"wrote consensus labels to {args.out}")
    return 1 if args.fail_on_dispute and disputed else 0


def cmd_cases(args) -> int:
    from . import cases as cs
    from . import taxonomy as tx
    from .config import load_cases
    if args.action == "tags":
        for d in tx.describe(args.workflow):
            print(f"  {d['tag']:<22} {d['group']:<10} {d['description']}")
        return 0
    if args.action == "seed":
        from .seed import chunk, read_docs, seed
        if not args.docs:
            raise SystemExit("cases seed needs --docs <folder or file>")
        if not args.generator:
            raise SystemExit("cases seed needs --generator <provider profile> (a strong model; in-region for customer data)")
        from .seed import plan, sample_passages
        n = args.max_cases or args.n_seed
        passages = chunk(read_docs(args.docs), words=args.chunk_words)
        docs = len({p.get("source") for p in passages})
        groups = len({(p.get("folder"), p.get("type")) for p in passages})
        use = sample_passages(passages, args.max_passages, args.sample_strategy, args.seed)
        providers = load_providers(_providers_path(args, args.path))
        est = plan(use, providers, args.generator, n=n, seed_=args.seed)
        cost = f"${est['estimated_cost_usd']:.2f}" if est["estimated_cost_usd"] is not None else "unknown (no pricing on the profile)"
        print(f"{len(passages)} passage(s) from {docs} document(s) in {groups} folder/type group(s); seeding from "
              f"{len(use)} ({args.sample_strategy}). Plan: {est['cases']} case(s) in {est['calls']} generator call(s), "
              f"~{est['input_tokens'] + est['output_tokens']:,} tokens, estimated {cost}")
        if args.budget_usd is not None and est["estimated_cost_usd"] is not None and est["estimated_cost_usd"] > args.budget_usd:
            print(f"  the plan is over the ${args.budget_usd:.2f} budget: it will be trimmed to fit")
        if args.dry_run:
            print("  dry run: nothing sent to the generator")
            return 0
        corpus = Path(args.corpus_out or "corpus.jsonl")
        corpus.parent.mkdir(parents=True, exist_ok=True)
        cs.write_jsonl([{k: p[k] for k in ("id", "title", "text")} for p in passages], corpus, append=False)
        tok = None if args.no_tokenize else cs.default_tokenizer()
        rep: dict = {}
        try:
            rows = seed(use, providers, args.generator, n=n, tokenizer=tok, seed_=args.seed, budget_usd=args.budget_usd,
                        report=rep)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        out = cs.write_jsonl(rows, args.out or "drafts.jsonl", append=args.append)
        kinds: dict[str, int] = {}
        for r in rows:
            kinds[r["metadata"]["kind"]] = kinds.get(r["metadata"]["kind"], 0) + 1
        spent = f", spent ${rep['spent_usd']:.2f}" if rep.get("spent_usd") is not None else ""
        print(f"wrote all {len(passages)} passage(s) to {corpus}; wrote {len(rows)} draft case(s) to {out} ("
              + ", ".join(f"{k} {kd}" for kd, k in kinds.items()) + f") in {rep.get('calls')} call(s){spent}")
        if args.budget_usd is not None and rep.get("planned_calls", est["calls"]) < est["calls"]:
            print(f"  the ${args.budget_usd:.2f} budget allowed {rep['planned_calls']} of {est['calls']} planned call(s)")
        if rep.get("stopped_by_budget"):
            print(f"  stopped at the ${args.budget_usd:.2f} budget (actual spend reached it)")
        if len(rows) < n:
            print(f"  note: asked for {n}, got {len(rows)} (duplicates and unusable rows dropped, or the budget); "
                  "add documents or run again with --append")
        print("  next: genai-evals cases review-sheet " + str(out) + " --out review.csv   (an expert marks keep / fix / drop)")
        print("        genai-evals cases promote " + str(out) + " --sheet review.csv --into data/cases.jsonl")
        print("  seeded cases are synthetic and written from the documents: a starting point, not the representative "
              "set. Replace them with production samples (cases sample) as traffic arrives.")
        return 0
    if args.action == "review-sheet":
        n = cs.write_review_sheet(args.path, args.out or "review.csv")
        print(f"wrote {args.out or 'review.csv'}: {n} draft(s). Fill in decision (keep / fix / drop), the corrected_* "
              "columns for fixes, and reviewer; then cases promote --sheet.")
        return 0
    if args.action == "grid":
        import yaml

        from .grid import grid_cases
        spec = yaml.safe_load(Path(args.path).read_text(encoding="utf-8")) or {}
        rows = grid_cases(spec, design=args.design, sample=args.sample, seed=args.seed)
        out = Path(args.out or "drafts.jsonl")
        with out.open("a" if args.append else "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        dims = spec.get("dimensions") or {}
        total = 1
        for v in dims.values():
            total *= len(v)
        print(f"wrote {len(rows)} draft case(s) to {out} ({args.design}"
              + (f", sample of {args.sample}" if args.sample else "") + f"; {total} combinations of "
              + " x ".join(f"{k} ({len(v)})" for k, v in dims.items()) + "). Each is tagged <dimension>=<value>; write "
              "references, then cases promote.")
        return 0
    if args.action == "health":
        from .health import result_health
        from .runner import load_result
        h = result_health(load_result(args.path))
        if args.json:
            print(json.dumps(h, indent=1))
        else:
            for n in h["notes"] or ["no never-passing cases, no saturated graders, no capability cases ready to graduate"]:
                print("  " + n)
        return 0
    if args.action == "check-refs":
        from .health import check_references
        suite = load_suite(args.path)
        rep = check_references(suite, load_providers(_providers_path(args, args.path)), judges=args.judges,
                               graders=args.grader)
        if args.json:
            print(json.dumps(rep, indent=1))
        else:
            print(f"graded {rep['cases']} case references with {len(rep['graders'])} grader(s)"
                  f"{'' if rep['judges_included'] else ' (model graders skipped; --judges to include them)'}: "
                  f"{rep['checked']} passed, {len(rep['rejected'])} rejected")
            for r in rep["rejected"]:
                print(f"  REJECTED {r['case_id']} by {r['grader']}: {r['why']}")
            if rep["no_reference"]:
                print(f"  no usable reference: {', '.join(rep['no_reference'][:10])}{' ...' if len(rep['no_reference']) > 10 else ''}")
            if rep["not_checkable"]:
                print("  not checkable from a reference: " + ", ".join(f"{g} ({n})" for g, n in rep["not_checkable"].items()))
        return 1 if rep["rejected"] else 0
    if args.action == "graduate":
        from .health import graduate_dataset, graduation
        from .runner import load_result
        g = graduation([load_result(p) for p in [args.path] + (args.also or [])])
        print(f"{len(g['ready'])} of {g['capability_cases']} capability case(s) passed every trial in all {g['runs']} run(s)"
              + (": " + ", ".join(g["ready"]) if g["ready"] else ""))
        if args.apply:
            if not args.into:
                raise SystemExit("graduate --apply needs --into <dataset.jsonl>")
            n = graduate_dataset(args.into, g["ready"])
            print(f"  set purpose: regression on {n} row(s) in {args.into}")
        return 0
    if args.action == "coverage":
        suite = load_suite(args.path)
        from .packs import suite_coverage
        cov = suite_coverage(suite, load_cases(suite))
        if args.json:
            print(json.dumps(cov, indent=1))
        else:
            w = f" ({cov['representative_weighted']} weighted)" if cov["representative_weighted"] else ""
            print(f"{cov['cases']} cases ({cov['workflow']} workflow): representative {cov['representative']}{w}, "
                  f"stress {cov['stress']}")
            for e in cov["evidence"]:
                print(f"  representative evidence: {e}")
            for g, d in cov["by_group"].items():
                pres = ", ".join(f"{t} ({cov['present'][t]})" for t in d["present"]) or "-"
                print(f"  {g:<10} enough:  {pres}")
                if d["thin"]:
                    print(f"  {'':<10} thin:    " + ", ".join(f"{t} {cov['thin'][t]['cases']}/{cov['thin'][t]['needed']}" for t in d["thin"]))
                if d["missing"]:
                    print(f"  {'':<10} missing: {', '.join(d['missing'])}")
            for t in cov["thin_gates"]:
                if t["severity"] != "block":
                    print(f"  note: {t['severity']} gate {t['gate']} will be invalid ({t['slice']}: {t['cases']} < min_n {t['min_n']})")
            for nt in cov.get("notes") or []:
                print(f"  note: {nt}")
            for pr in cov["problems"]:
                print(f"  to do: {pr}")
        return 1 if (args.strict and cov["problems"]) else 0
    if args.action == "harvest":
        from .runner import load_result
        tok = None if args.no_tokenize else cs.default_tokenizer()
        rows = cs.harvest(load_result(args.path), graders=args.grader, below=args.below, limit=args.limit, tokenizer=tok)
        out = cs.write_jsonl(rows, args.out or "drafts.jsonl", append=args.append)
        print(f"wrote {len(rows)} draft case(s) to {out}" + ("" if tok else " (NOT tokenised)") +
              ". Review each one, write its reference, then `genai-evals cases promote`.")
        return 0
    if args.action == "sample":
        import yaml as _yaml

        from .runner import load_result
        tok = None if args.no_tokenize else cs.default_tokenizer()
        rows, prov = cs.sample_representative(load_result(args.path), n=args.n, seed=args.seed, by_tag=args.by_tag,
                                              tokenizer=tok, population=args.population)
        out = cs.write_jsonl(rows, args.out or "representative-drafts.jsonl", append=args.append)
        pfile = Path(str(out) + ".provenance.yaml")
        pfile.write_text(_yaml.safe_dump({"representative_sample": {k: v for k, v in prov.items() if v is not None}},
                                         sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"wrote {len(rows)} draft representative case(s) to {out}, and their provenance to {pfile}.")
        if prov["source"] != "production":
            print("  note: sampled from recorded fixture telemetry, so `doctor` won't count these as production cases")
        if prov["represents"] != "production traffic":
            print(f"  note: this set represents {prov['represents']}")
        print("  next: an SME writes each reference, `genai-evals cases promote ... --into <dataset>`, then copy the "
              "provenance block into suite.yaml")
        return 0
    if args.action == "generate":
        if not args.generator:
            raise SystemExit("cases generate needs --generator <provider profile> (use a strong model)")
        suite = load_suite(args.path)
        existing = load_cases(suite)
        tok = None if args.no_tokenize else cs.default_tokenizer()
        rows = cs.generate(suite, load_providers(_providers_path(args, args.path)), args.generator, tags=args.tag,
                           n=args.n, workflow=args.workflow or tx.workflow_of(suite), existing=existing, tokenizer=tok)
        out = cs.write_jsonl(rows, args.out or "drafts.jsonl", append=args.append)
        print(f"wrote {len(rows)} draft case(s) to {out} for: {', '.join(sorted({r['tags'][0] for r in rows})) or 'nothing'}. "
              "Drafts don't count until an expert reviews them: write a reference, then `genai-evals cases promote`.")
        return 0
    if not args.into:
        raise SystemExit("cases promote needs --into <dataset.jsonl>")
    ids = [i.strip() for i in (args.ids or "").split(",") if i.strip()] or None
    reviewers = None
    if args.sheet:
        rs = cs.apply_review_sheet(args.path, args.sheet, reviewer=args.reviewer)
        print(f"review sheet: {len(rs['kept'])} kept, {len(rs['fixed'])} fixed, {len(rs['dropped'])} dropped")
        for pr in rs["problems"]:
            print(f"  not applied: {pr}")
        ids, reviewers = rs["promote"], rs["reviewers"]
        if not ids:
            print("nothing to promote")
            return 1 if rs["problems"] else 0
    done, refused = cs.promote(args.path, args.into, ids=ids, reviewer=args.reviewer,
                               allow_empty_reference=args.allow_empty_reference, schema=args.schema, reviewers=reviewers)
    print(f"promoted {len(done)} case(s) into {args.into}" + (f": {', '.join(done)}" if done else ""))
    for r in refused:
        print(f"  not promoted: {r}")
    return 0 if done or not refused else 1


def cmd_promptfoo(args) -> int:
    from .interop import write_config
    if args.action == "export":
        suite = load_suite(args.suite)
        notes = write_config(suite, load_providers(_providers_path(args, args.suite)), args.out)
        print(f"wrote {args.out}" + "".join(f"\n  note: {n}" for n in notes))
        return 0
    from .config import GraderSpec, SuiteSpec, load_cases
    from .interop import import_results_with_cases
    from .models import Case
    from .runner import Runner
    samples, scores, info = import_results_with_cases(args.results)
    names = sorted({s.grader for s in scores})
    graders = [GraderSpec(name=n, type="imported") for n in names]
    known: dict[str, Case] = {}
    if args.suite:
        # gate and report Promptfoo results with a genai-evals suite: its gates, min_n, report template, and the tags /
        # critical flags of its dataset for cases with the same id
        base = load_suite(args.suite)
        suite = base.model_copy(update={"graders": graders, "targets": [], "judges": [], "name": args.name or base.name})
        try:
            known = {c.id: c for c in load_cases(base)}
        except Exception:  # noqa: BLE001
            known = {}
        missing = sorted({g for gt in base.gates for g in re.findall(r"\b([a-z_][a-z0-9_]*)\(([a-z_][a-z0-9_]*)\)", gt.rule)
                          for g in [g[1]] if g not in names and g not in ("latency_ms", "cost_usd", "tokens", "steps")})
        if missing:
            print("note: gates refer to graders not in the Promptfoo results (they will be invalid): " + ", ".join(missing))
    else:
        suite = SuiteSpec(name=args.name or "promptfoo-import", graders=graders, tokenize=True)
    r = Runner.__new__(Runner)
    from .config import ProvidersConfig
    r.suite, r.telemetry, r.providers = suite, None, ProvidersConfig()
    from .dpdp import build_tokenizer
    r.tokenizer = build_tokenizer({})
    from .graders import GradeContext
    cases = []
    for cid in sorted({s.case_id for s in samples}):
        k, i = known.get(cid), info.get(cid, {})
        cases.append(Case(id=cid, input=(k.input if k else i.get("input") or ""),
                          tags=sorted(set(k.tags if k else []) | set(i.get("tags") or [])),
                          critical=bool((k.critical if k else False) or i.get("critical"))))
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

    p = sub.add_parser("sensitivity", help="re-grade with reworded rubrics and report whether verdicts and gates change")
    p.add_argument("result", help="result.json to re-grade (samples are reused; the system is not re-run)")
    p.add_argument("--suite", required=True)
    p.add_argument("--grader", required=True, help="judge grader to reword")
    p.add_argument("--variant", action="append", help="reworded rubric / policy text (repeatable)")
    p.add_argument("--variants", help="YAML list of variants: text, or parameter dicts such as {name, rubric, anchors}")
    p.add_argument("--max-flip-rate", type=float, default=0.1, help="robust if at most this share of verdicts flip (default 0.1)")
    p.add_argument("--baseline", help="baseline result.json for delta gates")
    p.add_argument("--providers")
    p.add_argument("--out", help="write the report as JSON")
    p.set_defaults(fn=cmd_sensitivity)

    p = sub.add_parser("power", help="how many cases a conclusion needs: interval width, detectable change, paired or not")
    p.add_argument("--rate", type=float, required=True, help="expected pass rate, e.g. 0.85")
    p.add_argument("--delta", type=float, help="change to detect, e.g. 0.05 (5 points)")
    p.add_argument("--half-width", type=float, help="wanted 95%% interval half-width, e.g. 0.05")
    p.add_argument("--n", type=int, help="cases you have: report the interval and the smallest detectable change")
    p.add_argument("--discordant", type=float, help="paired: share of cases whose verdict differs between the systems")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--power", type=float, default=0.8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_power)

    p = sub.add_parser("regrade", help="retry graders on units whose grading failed (transient judge errors by default), "
                                       "without re-running the system, then recompute gates")
    p.add_argument("result", help="result.json of the run to fix")
    p.add_argument("--suite", required=True, help="the suite that produced the result (graders must be unchanged)")
    p.add_argument("--grader", action="append", help="grader to re-run (repeatable; default: every grader with failures)")
    p.add_argument("--only", choices=["transient", "errors", "unscored"], default="transient",
                   help="transient: rate limits, timeouts, 5xx (default); errors: every grader error; unscored: errors and "
                        "units with no score")
    p.add_argument("--attempts", type=int, default=3, help="tries per unit; transient errors are retried (default 3)")
    p.add_argument("--backoff", type=float, default=2.0, help="seconds before the first retry, doubling each time (default 2)")
    p.add_argument("--force", action="store_true", help="re-grade even if the grader's config changed since the run")
    p.add_argument("--baseline", help="baseline result.json for delta gates")
    p.add_argument("--providers")
    p.add_argument("--out", help="output folder (default: next to the result, '<folder>-regraded')")
    p.add_argument("--no-html", action="store_true")
    p.add_argument("--store")
    p.add_argument("--summary-md")
    p.add_argument("--fail-on-warn", action="store_true")
    p.set_defaults(fn=cmd_regrade)

    p = sub.add_parser("calibrate", help="measure judge agreement with human labels")
    p.add_argument("result")
    p.add_argument("--grader", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--out")
    p.add_argument("--min-kappa", type=float, help="trust the judge only if the kappa 95%% CI lower bound clears this")
    p.add_argument("--min-n", type=int, default=30, help="fewest human labels before the judge can be trusted (default 30)")
    p.add_argument("--rule", choices=["majority", "unanimous"], default="majority",
                   help="how several reviewers' labels are combined (adjudicated rows always win)")
    p.add_argument("--compare-to", metavar="GRADER",
                   help="judge ladder: approve --grader (cheaper judge) only if it agrees with humans about as well as GRADER")
    p.add_argument("--compare-result", help="result.json holding --compare-to, if it is a different run on the same cases")
    p.add_argument("--margin", type=float, default=0.05, help="largest acceptable drop in kappa for the cheaper judge (default 0.05)")
    p.add_argument("--max-judge-failure", type=float, default=0.05,
                   help="judge ladder: the most the cheaper judge may fail to grade (errors, skips, missing scores) (default 0.05)")
    p.add_argument("--validation-labels", help="labels on a representative random sample (`labels sheet --design random`), "
                                               "reported separately; a trusted judge must clear --min-kappa on it too. "
                                               "With --compare-to, the ladder's approval is decided on this sample")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("cases", help="grow the eval set: coverage by standard tags, generate or harvest drafts, promote reviewed ones")
    p.add_argument("action", choices=["tags", "coverage", "sample", "generate", "harvest", "promote", "check-refs",
                                      "health", "graduate", "grid", "seed", "review-sheet"],
                   help="tags | coverage <suite.yaml> | sample <online result.json> | generate <suite.yaml> | "
                        "harvest <result.json> | promote <drafts.jsonl> | check-refs <suite.yaml> | health <result.json> | "
                        "graduate <result.json> [--also <result.json>] [--apply --into data.jsonl] | grid <grid.yaml> | "
                        "seed [suite.yaml] --docs <folder> --generator <profile> | review-sheet <drafts.jsonl>")
    p.add_argument("path", nargs="?", default="suite.yaml")
    p.add_argument("--workflow", choices=["rag", "agent", "extraction", "single_turn"], help="override the suite's workflow")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true", help="coverage: exit 1 if anything is missing")
    p.add_argument("--generator", help="generate: provider profile that writes the drafts (a strong model)")
    p.add_argument("--tag", action="append", help="generate: coverage tag(s) to write cases for (default: the missing ones)")
    p.add_argument("--n", type=int, default=5, help="generate: drafts per tag (default 5); sample: cases to draw")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--by-tag", action="store_true", help="sample: stratify by tag (proportional), with weights")
    p.add_argument("--population", help="sample: what the result covers, e.g. 'all web-chat claim sessions'")
    p.add_argument("--grader", action="append", help="harvest: only samples failing these graders (default: any)")
    p.add_argument("--below", type=float, help="harvest: also samples scoring below this value")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--no-tokenize", action="store_true", help="harvest / generate: don't tokenise personal data (not recommended)")
    p.add_argument("--out", help="drafts file (default drafts.jsonl)")
    p.add_argument("--append", action="store_true")
    p.add_argument("--into", help="promote: dataset to add reviewed cases to")
    p.add_argument("--ids", help="promote: comma list (default: every draft)")
    p.add_argument("--reviewer", help="promote: who reviewed them")
    p.add_argument("--schema", help="promote: check promoted rows against this case schema")
    p.add_argument("--allow-empty-reference", action="store_true")
    p.add_argument("--judges", action="store_true", help="check-refs: also run model graders (costs judge calls)")
    p.add_argument("--design", choices=["full", "pairwise"], default="full",
                   help="grid: every combination (full) or every pair of values across two dimensions (pairwise)")
    p.add_argument("--sample", type=int, help="grid: a random sample of this many combinations")
    p.add_argument("--docs", help="seed: folder or file of the customer's documents (txt, md, html; pdf, docx with the docs extra)")
    p.add_argument("--corpus-out", help="seed: where to write the passages (default corpus.jsonl; the RAG pack reads data/corpus.jsonl)")
    p.add_argument("--chunk-words", type=int, default=180, help="seed: passage size in words (default 180)")
    p.add_argument("--n-seed", type=int, default=40, help=argparse.SUPPRESS)
    p.add_argument("--max-cases", type=int, help="seed: draft cases to write (default 40: ~50%% answerable, 15%% multi-passage, "
                                                 "15%% edge, 20%% unanswerable); generator calls scale with this, not the repository")
    p.add_argument("--max-passages", type=int, default=400, help="seed: passages to sample from (default 400)")
    p.add_argument("--sample-strategy", choices=["stratified", "per-doc", "random"], default="stratified",
                   help="seed: stratified across folders and file types (default), round-robin per document, or random")
    p.add_argument("--budget-usd", type=float, help="seed: stop before this spend (needs pricing on the generator profile)")
    p.add_argument("--dry-run", action="store_true", help="seed: show the plan and estimated cost, send nothing")
    p.add_argument("--sheet", help="promote: a filled-in review sheet (cases review-sheet): keep / fix / drop per draft")
    p.add_argument("--also", action="append", help="graduate: more result files that must also show the case passing")
    p.add_argument("--apply", action="store_true", help="graduate: set purpose: regression on ready cases in --into")
    p.add_argument("--providers")
    p.set_defaults(fn=cmd_cases)

    p = sub.add_parser("labels", help="human labels: reviewer sheet, labelling guide, agreement and consensus")
    p.add_argument("action", choices=["sheet", "queue", "guide", "agreement"],
                   help="sheet <result.json> | queue <result.json> (items judges were unsure about) | guide <suite.yaml> | "
                        "agreement <reviews.csv>")
    p.add_argument("path")
    p.add_argument("--grader")
    p.add_argument("--n", type=int, default=50, help="sheet: items to sample (default 50)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--design", choices=["stratified", "random"],
                   help="sheet: stratified (calibration: by tag, balanced on the judge's verdict; default) or random (a "
                        "representative validation sample)")
    p.add_argument("--no-stratify", action="store_true", help="sheet: same as --design random")
    p.add_argument("--reviewers", help="sheet: comma list; one row per item per reviewer")
    p.add_argument("--rule", choices=["majority", "unanimous"], default="majority")
    p.add_argument("--fail-on-dispute", action="store_true", help="agreement: exit 1 while any item is disputed")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_labels)

    p = sub.add_parser("promptfoo", help="export a suite to promptfooconfig.yaml, or import Promptfoo results")
    p.add_argument("action", choices=["export", "import"])
    p.add_argument("path", help="suite.yaml (export) or results.json (import)")
    p.add_argument("-o", "--output", dest="pf_out", default="promptfooconfig.yaml")
    p.add_argument("--providers")
    p.add_argument("--name")
    p.add_argument("--suite", help="import: evaluate this suite's gates (and use its dataset's tags / critical flags) on the results")
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
