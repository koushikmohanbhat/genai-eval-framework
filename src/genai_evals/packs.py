"""Eval packs: the repeatable unit of an engagement.

A pack is a folder that evaluates one workflow (structured extraction, RAG Q&A, tool-using agent) end to end:
suite and graders, a dataset schema with synthetic starter cases, provider / target / telemetry configuration,
gates and baseline setup, a report template, a runbook, a handover checklist and an explicit validation status.
It runs offline on day 0 and is pointed at the customer's system by configuration only.

    genai-evals pack list
    genai-evals pack info rag-knowledge-assistant
    genai-evals init my-eval --pack rag-knowledge-assistant [--overlay insurance-policy]
    genai-evals pack validate packs/rag-knowledge-assistant      # for pack authors (CI runs this on every pack)
    genai-evals doctor my-eval                                   # is this engagement repeatable and ready to hand over?
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

PACK_FILES = ["pack.yaml", "README.md", "RUNBOOK.md", "HANDOVER.md", "suite.yaml", "providers.yaml", "sources.yaml",
              "online_checks.yaml", "experiment.yaml", "validation.yaml", "sim_system.py", "targets/offline.yaml"]
MANIFEST_KEYS = ["name", "version", "title", "workflow", "summary", "inputs", "targets", "graders", "failure_categories",
                 "gates", "compatibility", "validation", "handover"]
EXTRAS = {"rag": "openai,aws,gcp", "agent": "openai,aws,gcp", "extraction": "openai,aws,gcp"}


# ---- discovery ------------------------------------------------------------------
def packs_root() -> Path:
    """Packs ship inside the wheel (genai_evals/packs); in a source checkout they live in the repo's packs/."""
    installed = Path(str(resources.files("genai_evals"))) / "packs"
    if (installed / "_common").is_dir():
        return installed
    repo = Path(__file__).resolve().parents[2] / "packs"
    if (repo / "_common").is_dir():
        return repo
    raise FileNotFoundError("no packs found: reinstall genai-eval-framework")


def common_dir() -> Path:
    return packs_root() / "_common"


def builtin() -> list[str]:
    return sorted(p.name for p in packs_root().iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "pack.yaml").exists())


def _entry_dirs(group: str, marker: str) -> dict[str, Path]:
    """Folders registered by installed partner packages. In the partner's pyproject.toml:

        [project.entry-points."genai_evals.packs"]
        banking-kyc = "acme_evals.banking_kyc"            # a package whose folder holds pack.yaml
        [project.entry-points."genai_evals.overlays"]
        sap-order-tools = "acme_evals:sap_order_tools"    # module:subfolder holding overlay.yaml

    Built-in names can't be shadowed; an entry that doesn't load, or has no marker file, is skipped with a warning."""
    import importlib
    import sys
    from importlib.metadata import entry_points
    out: dict[str, Path] = {}
    for ep in entry_points(group=group):
        mod, _, sub = ep.value.partition(":")
        try:
            m = importlib.import_module(mod.strip())
            base = Path(m.__file__).parent if m.__file__ else Path(next(iter(m.__path__)))
            d = base / sub.strip() if sub.strip() else base
        except Exception as e:  # noqa: BLE001 - a broken partner package must not break the CLI
            print(f"warning: {group} entry '{ep.name}' failed to load: {e}", file=sys.stderr)
            continue
        if not (d / marker).exists():
            print(f"warning: {group} entry '{ep.name}' -> {d} has no {marker}", file=sys.stderr)
            continue
        out[ep.name] = d
    return out


def external_packs() -> dict[str, Path]:
    return {k: v for k, v in _entry_dirs("genai_evals.packs", "pack.yaml").items() if k not in builtin()}


def external_overlays() -> dict[str, Path]:
    """Overlays from partner packages, by name; each overlay.yaml names the pack it extends (`pack:`)."""
    return _entry_dirs("genai_evals.overlays", "overlay.yaml")


def available() -> list[str]:
    return sorted(set(builtin()) | set(external_packs()))


def resolve(name_or_dir: str | Path) -> Path:
    p = Path(name_or_dir)
    if (p / "pack.yaml").exists():
        return p
    q = packs_root() / str(name_or_dir)
    if (q / "pack.yaml").exists():
        return q
    ext = external_packs().get(str(name_or_dir))
    if ext:
        return ext
    raise FileNotFoundError(f"no pack '{name_or_dir}' (available: {', '.join(available())})")


def overlays_for(m: dict[str, Any], src: Path) -> dict[str, Path]:
    """Every overlay that can be added to this pack: its own, then partner overlays that declare `pack: <name>`."""
    out = {o["name"]: src / "overlays" / o["name"] for o in m.get("overlays", [])}
    for name, d in external_overlays().items():
        target = (yaml.safe_load((d / "overlay.yaml").read_text(encoding="utf-8")) or {}).get("pack")
        if target == m["name"] and name not in out:
            out[name] = d
    return out


def manifest(pack_dir: Path) -> dict[str, Any]:
    return yaml.safe_load((Path(pack_dir) / "pack.yaml").read_text(encoding="utf-8"))


# ---- generated docs (RUNBOOK / HANDOVER come from shared templates + the manifest) ----
def render_doc(template: str, m: dict[str, Any], has_judges: bool = True) -> str:
    from jinja2 import Environment, StrictUndefined
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True, trim_blocks=False, lstrip_blocks=False)
    tpl = env.from_string((common_dir() / template).read_text(encoding="utf-8"))
    targets = [t["file"] for t in m.get("targets", []) if t["file"] != "targets/offline.yaml"]
    gates = {k: {"severity": "block", **v} for k, v in m.get("gates", {}).items()}
    return tpl.render(pack={**m, "gates": gates}, extras=EXTRAS.get(m.get("workflow"), "openai"), has_judges=has_judges,
                      example_target=targets[0] if targets else "targets/http.yaml")


def _has_judges(pack_dir: Path) -> bool:
    from .graders.judges import JUDGE_GRADER_TYPES
    raw = yaml.safe_load((Path(pack_dir) / "suite.yaml").read_text(encoding="utf-8")) or {}
    return any(g.get("type") in JUDGE_GRADER_TYPES | {"policy"} for g in raw.get("graders", []))


def generated_docs(pack_dir: Path) -> dict[str, str]:
    m, j = manifest(pack_dir), _has_judges(pack_dir)
    return {"RUNBOOK.md": render_doc("RUNBOOK.md.j2", m, j), "HANDOVER.md": render_doc("HANDOVER.md.j2", m, j)}


def write_generated_docs(pack_dir: Path) -> list[str]:
    out = []
    for name, text in generated_docs(pack_dir).items():
        (Path(pack_dir) / name).write_text(text, encoding="utf-8")
        out.append(name)
    return out


# ---- scaffold ---------------------------------------------------------------------
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "out", ".genai-evals", "calibration.json", ".env")


def scaffold(pack: str, dest: str | Path, overlays: list[str] | None = None, force: bool = False) -> Path:
    src = resolve(pack)
    m = manifest(src)
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()) and not force:
        raise FileExistsError(f"{dest} is not empty (use --force to write into it)")
    known = overlays_for(m, src)
    chosen: dict[str, Path] = {}
    for o in overlays or []:
        p = Path(o)
        if (p / "overlay.yaml").exists():          # a folder: a partner's own overlay, not installed
            spec = yaml.safe_load((p / "overlay.yaml").read_text(encoding="utf-8")) or {}
            if spec.get("pack") and spec["pack"] != m["name"]:
                raise ValueError(f"overlay {o} is for pack '{spec['pack']}', not '{m['name']}'")
            chosen[spec.get("name") or p.name] = p
        elif o in known:
            chosen[o] = known[o]
        else:
            raise ValueError(f"pack '{m['name']}' has no overlay '{o}' (available: {', '.join(sorted(known)) or 'none'})")
    overlays = list(chosen)
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in ("overlays",) or item.name.startswith("__"):
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    for o, odir in chosen.items():
        shutil.copytree(odir, dest / "overlays" / o, ignore=_IGNORE, dirs_exist_ok=True)
    c = common_dir()
    shutil.copy2(c / "providers.live.yaml", dest / "providers.live.yaml")
    shutil.copy2(c / "env.example", dest / ".env.example")
    shutil.copy2(c / "gitignore", dest / ".gitignore")
    shutil.copytree(c / "ci", dest / "ci", dirs_exist_ok=True)
    (dest / "baselines").mkdir(exist_ok=True)
    (dest / "baselines" / "README.md").write_text(
        "# Baselines\n\nThe accepted result that pull requests are compared with. Create it after the first approved run:\n\n"
        "```bash\ngenai-evals run suite.yaml --out out/main --allow-missing-baseline\n"
        "genai-evals baseline promote out/main/result.json --out baselines/main.json\n```\n\n"
        "Refresh it after each approved release; the file holds summaries and per-case scores, no raw samples.\n", encoding="utf-8")
    if overlays:
        suite = dest / "suite.yaml"
        text = suite.read_text(encoding="utf-8")
        line = f"overlays: [{', '.join('overlays/' + o for o in overlays)}]\n"
        text = re.sub(r"(?m)^(dataset_schema: .*\n)", lambda mt: mt.group(1) + line, text, count=1)
        suite.write_text(text, encoding="utf-8")
    for name, text in generated_docs(dest).items():
        (dest / name).write_text(text, encoding="utf-8")
    return dest


@contextlib.contextmanager
def offline_env():
    """Force the offline configuration (simulator + mock judges) regardless of the project's .env."""
    keys = ["EVAL_TARGETS", "GENAI_EVALS_PROVIDERS", "EVAL_JUDGE_A", "EVAL_JUDGE_B", "EVAL_TARGET_PROFILE"]
    old = {k: os.environ.pop(k, None) for k in keys}
    os.environ.setdefault("GENAI_EVALS_TOKEN_KEY", "offline-demo-key")
    try:
        yield
    finally:
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


def run_offline(project: Path, out: Path, online: bool = True) -> dict[str, Any]:
    """Run the project's suite (and online checks on the recorded telemetry) offline. Returns the results."""
    from .config import load_providers, load_sources, load_suite
    from .online import run_online
    from .report import write_outputs
    from .runner import Runner
    project = Path(project)
    res: dict[str, Any] = {}
    with offline_env():
        suite = load_suite(project / "suite.yaml")
        providers = load_providers(project / "providers.yaml")
        r = Runner(suite, providers, use_cache=False).run()
        write_outputs(r, out / "offline")
        res["offline"] = r
        if online and (project / "online_checks.yaml").exists():
            o = run_online(load_suite(project / "online_checks.yaml"), providers, load_sources(project / "sources.yaml"))
            write_outputs(o, out / "online")
            res["online"] = o
    return res


# ---- pack validation (for authors and CI) ------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool | None          # None = not applicable / not run
    detail: str = ""
    next: str = ""


def validate_pack(pack_dir: str | Path, run: bool = True) -> list[Check]:
    from . import plugins
    from .config import load_cases, load_suite
    from .experiment import load_experiment
    p = Path(pack_dir)
    out: list[Check] = []
    missing = [f for f in PACK_FILES if not (p / f).exists()]
    out.append(Check("files", not missing, "missing: " + ", ".join(missing) if missing else f"{len(PACK_FILES)} required files present"))
    try:
        m = manifest(p)
    except Exception as e:  # noqa: BLE001
        return out + [Check("manifest", False, f"pack.yaml: {e}")]
    mk = [k for k in MANIFEST_KEYS if k not in m]
    out.append(Check("manifest", not mk, "missing keys: " + ", ".join(mk) if mk else f"{m['name']} {m['version']} ({m['workflow']})"))
    probs = []
    for k in ("schema", "starter", "guidance"):
        f = (m.get("inputs", {}).get("dataset") or {}).get(k)
        if not f or not (p / f).exists():
            probs.append(f"inputs.dataset.{k} {f!r} not found")
    probs += [f"target file {t['file']} not found" for t in m.get("targets", []) if not (p / t["file"]).exists()]
    out.append(Check("inputs", not probs, "; ".join(probs)))

    try:
        with offline_env():
            suite = load_suite(p / "suite.yaml")
            cases = load_cases(suite)
        names = {g.name for g in suite.graders}
        probs = [f"grader {g} not in suite.yaml" for g in m.get("graders", []) if g not in names]
        fc = set((suite.report or {}).get("failure_categories", {}))
        probs += [f"failure category '{c}' not in suite.yaml report.failure_categories" for c in m.get("failure_categories", {}) if c not in fc]
        rules = {g.name: g.rule for g in suite.gates}
        probs += [f"gate {n}: pack.yaml rule differs from suite.yaml" if n in rules else f"gate {n} not in suite.yaml"
                  for n, g in m.get("gates", {}).items() if rules.get(n) != g["rule"]]
        out.append(Check("suite", not probs, "; ".join(probs) or f"{len(cases)} starter cases valid against the schema, "
                         f"{len(suite.graders)} graders, {len(suite.gates)} gates"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("suite", False, f"{type(e).__name__}: {e}"))
        suite = None

    probs = []
    for kind, key in (("provider", "providers"), ("source", "sources"), ("store", "stores"), ("target", "targets")):
        probs += [f"unknown {kind} '{n}'" for n in m.get("compatibility", {}).get(key, []) if plugins.info(kind, n) is None]
    out.append(Check("compatibility", not probs, "; ".join(probs) or "every listed adapter is in the catalog"))

    stale = [n for n, text in generated_docs(p).items() if (p / n).read_text(encoding="utf-8") != text] if (p / "HANDOVER.md").exists() else []
    out.append(Check("generated docs", not stale, ("out of date: " + ", ".join(stale)) if stale else "RUNBOOK.md and HANDOVER.md match pack.yaml",
                     "genai-evals pack docs <pack dir>" if stale else ""))

    probs = []
    for o in m.get("overlays", []):
        if not (p / "overlays" / o["name"] / "overlay.yaml").exists():
            probs.append(f"overlay {o['name']} missing overlay.yaml")
            continue
        try:
            with offline_env():
                s2 = load_suite(p / "suite.yaml")
                s2 = s2.model_copy(update={"overlays": [f"overlays/{o['name']}"]})
                from .config import apply_overlays
                load_cases(apply_overlays(s2))
        except Exception as e:  # noqa: BLE001
            probs.append(f"overlay {o['name']}: {e}")
    out.append(Check("overlays", not probs if m.get("overlays") else None, "; ".join(probs) or f"{len(m.get('overlays', []))} overlay(s) load"))

    try:
        load_experiment(p / "experiment.yaml")
        out.append(Check("experiment", True, "experiment.yaml loads"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("experiment", False, str(e)))

    if run and suite is not None:
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "proj"
            shutil.copytree(p, work, ignore=_IGNORE)
            try:
                res = run_offline(work, Path(d) / "out")
                r = res["offline"]
                bad = [g["gate"] for g in r["gates"] if g["severity"] == "block" and not g["passed"]]
                out.append(Check("offline run", not bad, f"blocking gates not passing: {bad}" if bad else
                                 f"all {sum(1 for g in r['gates'] if g['severity'] == 'block')} blocking gates pass on the starter cases"))
                if "online" in res:
                    o = res["online"]
                    n = len({(x["target"], x["case_id"]) for x in o["samples"]})
                    alerts = ", ".join(g["gate"] for g in o["gates"] if not g["passed"])
                    out.append(Check("online run", n > 0, f"{n} recorded sessions graded; " + (f"alerts: {alerts}" if alerts else "no alerts")))
            except Exception as e:  # noqa: BLE001
                out.append(Check("offline run", False, f"{type(e).__name__}: {e}"))
    return out


# ---- doctor: the repeatability bar for one engagement --------------------------------
def _results(project: Path) -> list[tuple[Path, dict[str, Any]]]:
    found = []
    for f in list(project.glob("out/**/result.json")) + list(project.glob(".genai-evals/**/result.json")):
        try:
            found.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            continue
    return sorted(found, key=lambda x: x[1].get("created_at", ""), reverse=True)


def suite_coverage(suite, cases) -> dict[str, Any]:
    """Coverage of the representative set and stress suite for a suite, with gate problems found before a run:
    blocking gates that average both sets, and gates whose tag / split slice has fewer cases than their min_n."""
    from . import taxonomy
    cov = taxonomy.coverage(cases, taxonomy.workflow_of(suite), min_representative=suite.min_representative_cases,
                            stress_tags=suite.stress_tags, min_by_tier=suite.stress_min_cases,
                            min_by_tag=suite.stress_min_by_tag, provenance=suite.representative_sample,
                            min_production_share=suite.min_representative_production_share,
                            safety_claim=suite.safety_claim, security_review=suite.security_review)
    mixed = taxonomy.mixed_gates(suite)
    if mixed and cov["stress"] and cov["representative"]:
        cov["problems"].append("blocking gate(s) average representative and stress cases together: " + ", ".join(mixed)
                               + " (gate the headline with split='representative', and the stress suite separately)")
    thin = taxonomy.thin_gates(suite, cases)
    cov["thin_gates"] = thin
    blocking = [t for t in thin if t["severity"] == "block"]
    if blocking:
        cov["problems"].append("blocking gate(s) will be invalid for lack of cases: " + ", ".join(
            f"{t['gate']} ({t['slice']}: {t['cases']} < min_n {t['min_n']})" for t in blocking)
            + " (add cases, or make the gate severity: info)")
    return cov


def _raw_rows(suite) -> list[dict[str, Any]]:
    from .config import _read_rows
    if isinstance(suite.dataset, list):
        return list(suite.dataset)
    rows = _read_rows(suite.path(suite.dataset)) if suite.dataset else []
    for extra in suite.extra_datasets:
        rows += _read_rows(suite.path(extra))
    return rows


def doctor(project: str | Path, run: bool = True, preflight: bool = False) -> list[Check]:
    """Check one project against the definition of repeatable: offline demo, one provider and one telemetry source
    connected, an understandable report, a meaningful release gate, and a handover with a maintenance path."""
    from .calibration import untrusted_graders
    from .config import load_suite
    from .graders.judges import JUDGE_GRADER_TYPES
    from .plugins import LEVELS
    p = Path(project)
    checks: list[Check] = []
    if not (p / "suite.yaml").exists():
        return [Check("project", False, f"no suite.yaml in {p}", "genai-evals init <dir> --pack <pack>")]

    # 1. offline demo
    if run and (p / "targets" / "offline.yaml").exists():
        with tempfile.TemporaryDirectory() as d:
            try:
                r = run_offline(p, Path(d), online=False)["offline"]
                checks.append(Check("offline demo", True, f"runs offline ({len(r['cases'])} cases, {'all blocking gates pass' if r['passed'] else 'some gates fail'})"))
            except Exception as e:  # noqa: BLE001
                checks.append(Check("offline demo", False, f"{type(e).__name__}: {e}", "fix the error, then `genai-evals run suite.yaml`"))
    else:
        checks.append(Check("offline demo", None, "skipped (no targets/offline.yaml or --no-run)"))

    results = _results(p)
    live = [(f, r) for f, r in results if r.get("mode", "offline") == "offline"
            and any(a["kind"] == "provider" and a["name"] != "mock" for a in r.get("adapters", []))]
    online = [(f, r) for f, r in results if r.get("mode") == "online"
              and any("samples" in s and not s.get("fixture", False) for s in r.get("sources", []))]

    # 2. provider + telemetry
    if live:
        f, r = live[0]
        provs = sorted({a["name"] for a in r["adapters"] if a["kind"] == "provider" and a["name"] != "mock"})
        checks.append(Check("provider connected", True, f"{', '.join(provs)} in {f.relative_to(p)}"))
    else:
        checks.append(Check("provider connected", False, "no run with a live provider yet",
                            "set EVAL_TARGETS / GENAI_EVALS_PROVIDERS in .env, then `genai-evals run suite.yaml --out out/live`"))
    if online:
        f, r = online[0]
        srcs = sorted({s["type"] for s in r["sources"] if "samples" in s and not s.get("fixture")})
        checks.append(Check("telemetry connected", True, f"{', '.join(srcs)} in {f.relative_to(p)}"))
    else:
        checks.append(Check("telemetry connected", False, "no online run on live telemetry yet (fixture runs don't count)",
                            "keep your source in sources.yaml, remove `fixture:`, check it with `genai-evals sources "
                            "preflight sources.yaml` (is payload logging on and readable?), then `genai-evals online ... "
                            "--out out/online`"))
    if preflight and (p / "sources.yaml").exists():
        from .config import load_sources
        from .sources.preflight import preflight as _pre
        found = [f for f in _pre(load_sources(p / "sources.yaml")) if f.status != "skipped"]
        if not found:
            checks.append(Check("telemetry readiness", None, "skipped (every source in sources.yaml reads a fixture)"))
        else:
            bad = [f for f in found if not f.ok]
            checks.append(Check("telemetry readiness", not bad,
                                "; ".join(f"{f.source}: {f.status}: {f.detail}" for f in (bad or found)),
                                "" if not bad else "; ".join(dict.fromkeys(f.fix for f in bad if f.fix))
                                + ". If logging can't be turned on: " + "; or ".join(bad[0].fallbacks[:2])))
    # 2b. telemetry joins: do sampled traces actually have their payload records (Bedrock invocation logs, Vertex
    #     request-response logs)? Content capture is often off by default, and a join that falls back to timestamps can
    #     pair the wrong prompt with a trace.
    if online:
        from .sources.normalize import diagnose_source
        f, r = online[0]
        live_src = [s for s in r["sources"] if "samples" in s and not s.get("fixture")]
        joins = sum(1 for s in live_src for k in s if k.startswith("join:"))
        jprobs = [x for s in live_src for x in diagnose_source(s)]
        if jprobs:
            checks.append(Check("telemetry joins", False, "; ".join(jprobs),
                                "`genai-evals sources check sources.yaml` re-reads the window and prints the join report "
                                "(docs/sources.md, Troubleshooting telemetry joins)"))
        else:
            checks.append(Check("telemetry joins", True,
                                f"{joins} payload join(s) matched by trace or session id; completions present" if joins
                                else "no payload joins configured; completions present in the traces"))
    else:
        checks.append(Check("telemetry joins", None, "skipped (no online run on live telemetry yet)"))

    # 3. understandable report
    suite = load_suite(p / "suite.yaml")
    if live:
        f, r = live[0]
        has_html = (f.parent / "report.html").exists() and (f.parent / "scorecard.html").exists()
        tmpl = bool((r.get("report") or {}).get("title")) and bool((r.get("report") or {}).get("failure_categories"))
        checks.append(Check("report", has_html and tmpl, "engineer report and scorecard with failure categories" if has_html and tmpl else
                            ("report files missing" if not has_html else "suite.yaml has no report title / failure_categories"),
                            "" if has_html and tmpl else "keep the pack's `report:` block in suite.yaml"))
    else:
        checks.append(Check("report", False, "needs a live run first", "see 'provider connected'"))

    # 4. meaningful release gate
    ops = {"latency", "cost", "token_budget"}
    quality_gates = [g for g in suite.gates if g.severity == "block"
                     and any(re.search(rf"\b{re.escape(gr.name)}\b", g.rule) for gr in suite.graders if gr.type not in ops)]
    baselines = list((p / "baselines").glob("*.json")) if (p / "baselines").is_dir() else []
    ci = [w for w in list(p.glob(".github/workflows/*.y*ml")) + list(p.parent.glob(".github/workflows/*.y*ml"))
          if "genai-eval" in w.read_text(encoding="utf-8", errors="ignore")]
    probs = []
    if not quality_gates:
        probs.append("no blocking gate on a quality grader")
    if not baselines:
        probs.append("no baseline in baselines/")
    if not ci:
        probs.append("no CI workflow running genai-evals")
    checks.append(Check("release gate", not probs, "; ".join(probs) or f"{len(quality_gates)} blocking quality gates, baseline {baselines[0].name}, CI {ci[0].name}",
                        "RUNBOOK step 6" if probs else ""))
    # 4b. representative set and stress suite (OpenAI's evaluation best practices; kept apart so edge cases don't
    #     distort the production estimate)
    from .config import load_cases
    try:
        cases = load_cases(suite)
        cov = suite_coverage(suite, cases)
        drafts = sum(1 for c in _raw_rows(suite) if c.get("review_status") == "draft")
        problems = list(cov["problems"])
        rep = (f"{cov['representative']} representative ({cov['representative_production']} from production, "
               + ("provenance recorded" if cov["provenance"] else "no provenance")
               + (f", {cov['representative_weighted']} weighted" if cov["representative_weighted"] else "") + ")")
        short = []
        if cov["representative"] < suite.min_representative_cases:
            short.append(f"representative set below {suite.min_representative_cases}")
        if not cov["provenance"]:
            short.append("no representative_sample provenance")
        if cov["representative"] and cov["representative_production"] / cov["representative"] < suite.min_representative_production_share:
            short.append("too few production cases in it")
        if cov["missing"]:
            short.append(f"{len(cov['missing'])} stress tag(s) missing")
        if cov["thin"]:
            short.append(f"{len(cov['thin'])} thin")
        other = len(problems) - len(short) - (1 if cov["untagged"] else 0)
        if cov["untagged"]:
            short.append(f"{cov['untagged']} untagged")
        if other > 0:
            short.append("gate problems")
        detail = (f"{rep} + {cov['stress']} stress cases; "
                  + (", ".join(short) if problems else "representative set has evidence, stress coverage is sufficient")
                  + (f"; {drafts} draft case(s) awaiting review" if drafts else ""))
        checks.append(Check("eval set", not problems, detail,
                            "" if not problems else "genai-evals cases coverage suite.yaml lists each item (docs/growing-the-eval-set.md)"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("eval set", False, f"{type(e).__name__}: {e}", "fix the dataset"))
    judged = [g.name for g in suite.graders if g.type in JUDGE_GRADER_TYPES or g.type == "policy"]
    if judged:
        cal = suite.path(suite.calibration) if suite.calibration else None
        if not cal or not cal.exists():
            checks.append(Check("judges calibrated", False, f"judge-graded: {', '.join(judged)}; no calibration file", "RUNBOOK step 5"))
        else:
            from .calibration import load_calibration
            data = load_calibration(cal)
            bad = untrusted_graders(data, suite.min_judge_kappa or 0.0, suite.min_calibration_n)
            uncal = [g for g in judged if g not in data]
            no_val = [g for g in judged if g in data and not (data[g].get("validation") or {}).get("n")]
            ok_txt = "all judges trusted" + (f" (on the calibration sample only; no representative validation sample for: "
                                             f"{', '.join(no_val)})" if no_val else ", including on a representative validation sample")
            checks.append(Check("judges calibrated", not bad and not uncal,
                                "; ".join([f"untrusted: {', '.join(bad)}"] * bool(bad) + [f"not calibrated: {', '.join(uncal)}"] * bool(uncal)) or ok_txt,
                                "" if not bad and not uncal else "RUNBOOK step 5"))
    else:
        checks.append(Check("judges calibrated", None, "no judge-graded graders"))

    # 5. handover
    h = p / "HANDOVER.md"
    if h.exists():
        text = h.read_text(encoding="utf-8")
        open_items = len(re.findall(r"(?m)^\s*- \[ \]", text))
        owners = len(re.findall(r"Owner: _+", text))
        checks.append(Check("handover", open_items == 0 and owners == 0,
                            "complete" if open_items == 0 and owners == 0 else f"{open_items} unticked item(s), {owners} owner(s) not named",
                            "" if open_items == 0 and owners == 0 else "walk through HANDOVER.md with the customer"))
    else:
        checks.append(Check("handover", False, "no HANDOVER.md", "genai-evals pack docs ."))
    from .plugins import effective, load_validation
    recs = load_validation(p / "validation.yaml") if (p / "validation.yaml").exists() else []
    used = {(a["kind"], a["name"]) for _, r in (live[:1] + online[:1]) for a in r.get("adapters", []) if a["name"] != "mock"}
    weak = sorted(f"{k} {n}" for k, n in used if LEVELS.index(effective(k, n, recs)["status"]) < LEVELS.index("live_tested"))
    checks.append(Check("adapters validated", (not weak) if used else False,
                        ("not yet recorded as live tested: " + ", ".join(weak)) if weak else ("all adapters in use are live tested or better" if used else "no live runs yet"),
                        "genai-evals adapters record --kind <kind> --name <name> --status live_tested ..." if weak or not used else ""))
    return checks


def checks_text(checks: list[Check]) -> str:
    sym = {True: "pass", False: "todo", None: "skip"}
    lines = []
    for c in checks:
        lines.append(f"  [{sym[c.ok]:4s}] {c.name}: {c.detail}")
        if c.ok is False and c.next:
            lines.append(f"         next: {c.next}")
    return "\n".join(lines)


# ---- `pack new`: a partner package that extends a built-in pack ------------------------------------------------------
NEW_PACK_GRADER = {"rag-knowledge-assistant": "faithfulness", "tool-agent": "task_success",
                   "structured-extraction": "fields"}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s or not s[0].isalpha():
        raise ValueError(f"'{name}': use letters, digits and hyphens, starting with a letter")
    return s


def new_partner_pack(name: str, extends: str, dest: str | Path | None = None, package: str | None = None,
                     force: bool = False) -> Path:
    """Write a partner package that adds an overlay to `extends`: pyproject with the entry point, the overlay
    (starter cases taken from the base pack and retagged, a slice gate and a critical gate), a README and a CI
    workflow that installs it, checks it passes on the guarded offline system and fails on the vulnerable one, and
    builds the wheel. Replace the starter cases with your domain's before you ship it."""
    slug = _slug(name)
    base = resolve(extends)
    m = manifest(base)
    pack_name = m["name"]
    if slug in overlays_for(m, base) or slug in available():
        raise ValueError(f"'{slug}' is already a pack or an overlay of {pack_name}")
    package = _slug(package or f"genai-evals-{slug}")
    module = package.replace("-", "_")
    folder = slug.replace("-", "_")
    dest = Path(dest or package)
    if dest.exists() and any(dest.iterdir()) and not force:
        raise FileExistsError(f"{dest} is not empty (use --force to write into it)")
    odir = dest / module / folder
    odir.mkdir(parents=True, exist_ok=True)
    (dest / ".github" / "workflows").mkdir(parents=True, exist_ok=True)

    import json as _json
    rows = [_json.loads(x) for x in (base / "data" / "starter.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    single = [r for r in rows if not r.get("turns")]
    typical = [r for r in single if "typical" in (r.get("tags") or []) and not r.get("critical")][:3]
    # the attack every pack's offline system can be made to fall for: system prompt leakage, from the pack's own
    # security overlay, with its exact-match grader under a name of this overlay's
    sec = base / "overlays" / "security-guardrails"
    leak_grader, attacks = None, []
    if (sec / "overlay.yaml").exists():
        sspec = yaml.safe_load((sec / "overlay.yaml").read_text(encoding="utf-8")) or {}
        g = next((g for g in sspec.get("graders", []) if g.get("type") == "prompt_leak"), None)
        if g:
            leak_grader = dict(g, name=f"{slug.replace('-', '_')}_no_prompt_leak", tags=["prompt_leak"])
            leak_grader.pop("canary_env", None)
            attacks = [_json.loads(x) for x in (sec / "cases.jsonl").read_text(encoding="utf-8").splitlines()
                       if x.strip() and "prompt_leak" in (_json.loads(x).get("tags") or [])][:2]
    picked = typical + attacks
    with (odir / "cases.jsonl").open("w", encoding="utf-8") as f:
        for i, r in enumerate(picked, 1):
            r = dict(r, id=f"{slug}-{i:02d}", tags=list(dict.fromkeys((r.get("tags") or []) + [slug])))
            f.write(_json.dumps(r, ensure_ascii=False) + "\n")
    grader = NEW_PACK_GRADER.get(pack_name) or m["graders"][0]
    graders_yaml = leak_gate = ""
    if leak_grader and attacks:
        graders_yaml = "graders:\n  - " + _json.dumps(leak_grader) + "\n"
        leak_gate = (f"  - {{name: {slug}-no-prompt-leak, rule: \"mean({leak_grader['name']}, tag='{slug}') >= 1.0\", "
                     f"min_n: {len(attacks)}, reason: \"the system prompt is never revealed\"}}\n")
    (odir / "overlay.yaml").write_text(f"""# {slug}: a partner overlay for the {pack_name} pack, shipped in its own package ({package}).
# The starter cases are copies of the base pack's, retagged '{slug}', so this runs green out of the box.
# Replace them with your domain's cases (keep the '{slug}' tag; mark must-never-fail cases critical: true), add
# graders only where the pack's own don't cover a rule, and raise min_n as the slice grows.
name: {slug}
pack: {pack_name}
description: "TODO: one line on the domain this adds (systems, rules, attacks)"
cases: [cases.jsonl]
{graders_yaml}gates:
  - {{name: {slug}-quality, rule: "mean({grader}, tag='{slug}') >= 0.8", min_n: 3, reason: "the {slug} slice meets the bar"}}
{leak_gate}  - {{name: {slug}-critical, rule: "critical_pass()", reason: "critical cases never fail"}}
""", encoding="utf-8")
    (dest / module / "__init__.py").write_text(f'"""Partner package for genai-evals: registers the `{slug}` overlay '
                                               f'(see pyproject.toml)."""\n', encoding="utf-8")
    from . import __version__
    minor = ".".join(__version__.split(".")[:2])
    (dest / "pyproject.toml").write_text(f"""[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{package}"
version = "0.1.0"
description = "genai-evals overlay for the {pack_name} pack: {slug}"
requires-python = ">=3.10"
dependencies = ["genai-eval-framework>={minor}"]

[project.entry-points."genai_evals.overlays"]
{slug} = "{module}:{folder}"

[tool.setuptools]
packages = ["{module}"]

[tool.setuptools.package-data]
{module} = ["{folder}/*"]
""", encoding="utf-8")
    (dest / "README.md").write_text(f"""# {package}

A [genai-evals](https://github.com/koushikmohanbhat/genai-eval-framework) overlay for the `{pack_name}` pack: `{slug}`.
Domain cases and gates live here, in your own package; the open-source core stays untouched.

```bash
pip install .
genai-evals pack list                                  # {pack_name} ... (overlays: ..., {slug} [partner])
genai-evals init my-eval --pack {pack_name} --overlay {slug}
cd my-eval && genai-evals run suite.yaml --out out/first --allow-missing-baseline
```

## Make it yours

1. Replace `{module}/{folder}/cases.jsonl` with your domain's cases. Keep the `{slug}` tag on each; mark the ones
   that must never fail `critical: true`; include attacks through the data your system reads.
2. Tune the gates in `overlay.yaml`, and raise `min_n` as the slice grows (a gate on 3 cases proves little).
3. Add graders only for rules the pack's own graders don't cover; grader names must not clash with the pack's.
4. Keep CI green: it checks the overlay passes on the pack's guarded offline system and fails on the vulnerable
   one. If a case needs systems the offline simulator doesn't have, note it and test it against your live target.

Ship it from a private repository or index; `genai-evals pack list` marks it `[partner]` once installed.
""", encoding="utf-8")
    (dest / ".github" / "workflows" / "ci.yml").write_text(f"""name: ci
on: [push, pull_request]
permissions: {{contents: read}}
jobs:
  overlay:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0
        with: {{python-version: "3.12"}}
      - run: pip install .
      - run: genai-evals pack list | grep -q "{slug} \\[partner\\]"
      - name: contribution bar (metadata, tags, critical and security cases, no identifiers, guarded / vulnerable)
        env: {{GENAI_EVALS_TOKEN_KEY: ci-only-not-secret}}
        run: genai-evals pack check {module}/{folder}
      - name: passes on the guarded offline system
        env: {{GENAI_EVALS_TOKEN_KEY: ci-only-not-secret}}
        run: |
          genai-evals init /tmp/good --pack {pack_name} --overlay {slug}
          cd /tmp/good && genai-evals run suite.yaml --out out/ci --allow-missing-baseline --no-html
      - name: fails on the vulnerable offline system
        env: {{GENAI_EVALS_TOKEN_KEY: ci-only-not-secret}}
        run: |
          genai-evals init /tmp/bad --pack {pack_name} --overlay {slug}
          cd /tmp/bad
          sed -i 's/callable: "sim_system:\\([a-z_]*\\)"}}/callable: "sim_system:\\1", options: {{vulnerable: true}}}}/' targets/offline.yaml
          grep -q "vulnerable: true" targets/offline.yaml
          if genai-evals run suite.yaml --out out/ci --allow-missing-baseline --no-html; then
            echo "the overlay did not catch the vulnerable system"; exit 1; fi
      - run: pip install build && python -m build --wheel
""", encoding="utf-8")
    return dest


# ---- the partner overlay index and the contribution bar ---------------------------------------------------------------
INDEX_FIELDS = ("name", "extends", "title", "owner", "status", "access", "install", "source", "license")
INDEX_STATUS = ("example", "community", "field-tested")


def overlay_index() -> list[dict[str, Any]]:
    p = common_dir() / "overlay-index.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("overlays", []) if p.exists() else []


def check_index(entries: list[dict[str, Any]] | None = None) -> list[str]:
    problems, seen = [], set()
    for e in overlay_index() if entries is None else entries:
        n = e.get("name", "?")
        miss = [f for f in INDEX_FIELDS if not e.get(f)]
        if miss:
            problems.append(f"{n}: missing {', '.join(miss)}")
        if e.get("status") and e["status"] not in INDEX_STATUS:
            problems.append(f"{n}: status must be one of {', '.join(INDEX_STATUS)}")
        if e.get("extends") and e["extends"] not in builtin():
            problems.append(f"{n}: extends '{e['extends']}', which is not a built-in pack")
        if n in seen or n in builtin():
            problems.append(f"{n}: name already used")
        seen.add(n)
    return problems


def check_overlay(folder: str | Path, run: bool = True) -> list[Check]:
    """The contribution bar for a partner overlay: complete metadata, tagged and critical cases, no real-looking
    identifiers in the cases, a security case, and (run=True) green on the pack's guarded offline system and red on
    its vulnerable one."""
    import json as _json

    from .pii import detect
    d = Path(folder)
    checks: list[Check] = []
    spec = yaml.safe_load((d / "overlay.yaml").read_text(encoding="utf-8")) if (d / "overlay.yaml").exists() else None
    if not spec:
        return [Check("overlay.yaml", False, f"no overlay.yaml in {d}", "genai-evals pack new <name> --extends <pack>")]
    name, pack = spec.get("name"), spec.get("pack")
    meta_ok = bool(name and pack and spec.get("description")) and "TODO" not in str(spec.get("description"))
    checks.append(Check("metadata", meta_ok, f"{name} for {pack}" if meta_ok else "name, pack and a real description are required",
                        "" if meta_ok else "fill name, pack: and description in overlay.yaml"))
    if pack not in available():
        checks.append(Check("base pack", False, f"pack '{pack}' is not installed or built in", "set pack: to a pack from `genai-evals pack list`"))
        return checks
    rows = []
    for f in spec.get("cases") or []:
        rows += [_json.loads(x) for x in (d / f).read_text(encoding="utf-8").splitlines() if x.strip()]
    common = set.intersection(*[set(r.get("tags") or []) for r in rows]) if rows else set()
    common -= {"typical", "edge", "adversarial", "security"}
    slice_tags = sorted(common, key=lambda t: (t != name, t))
    untagged = [r["id"] for r in rows if name not in (r.get("tags") or [])]
    checks.append(Check("cases tagged", bool(slice_tags),
                        f"{len(rows)} case(s), all tagged '{slice_tags[0]}'" if slice_tags else
                        (f"no tag on every case (e.g. missing '{name}' on {', '.join(untagged[:5])})" if rows else "no cases"),
                        "" if slice_tags else f"tag every case '{name}' so gates can address the slice"))
    crit = [r for r in rows if r.get("critical")]
    checks.append(Check("critical cases", bool(crit), f"{len(crit)} critical" if crit else "none marked critical",
                        "" if crit else "mark the cases that must never fail critical: true"))
    sec = [r for r in rows if {"adversarial", "prompt_leak", "indirect_injection", "excessive_agency"} & set(r.get("tags") or [])]
    checks.append(Check("security cases", bool(sec), f"{len(sec)} adversarial / security case(s)" if sec else "none",
                        "" if sec else "add attacks through the data the system reads (docs/security.md)"))
    pii = sorted({f.type for r in rows for f in detect(_json.dumps(r, ensure_ascii=False))})
    checks.append(Check("no real-looking identifiers", not pii,
                        "no identifiers found in the cases" if not pii else f"identifiers in the cases: {', '.join(pii)}",
                        "" if not pii else "use synthetic, invalid-checksum values and confirm none is a real person's; "
                        "never ship customer data in an overlay"))
    gates = spec.get("gates") or []
    slice_gate = any(f"tag='{t}'" in g.get("rule", "") or f'tag="{t}"' in g.get("rule", "") for g in gates for t in slice_tags)
    checks.append(Check("slice gate", slice_gate, "a gate on the overlay's own slice" if slice_gate else
                        f"no gate on tag='{(slice_tags or [name])[0]}'",
                        "" if slice_gate else f"add a gate with tag='{(slice_tags or [name])[0]}' and a min_n"))
    if not run:
        checks.append(Check("guarded / vulnerable", None, "skipped (--no-run)"))
        return checks
    import tempfile

    from .config import load_providers, load_suite
    from .runner import Runner

    def gates_for(vulnerable: bool) -> dict[str, bool]:
        with tempfile.TemporaryDirectory() as tmp, offline_env():
            dest = scaffold(pack, Path(tmp) / "p", overlays=[str(d)])
            t = dest / "targets" / "offline.yaml"
            if vulnerable:
                t.write_text(re.sub(r'callable: "sim_system:([a-z_]+)"}', r'callable: "sim_system:\1", options: {vulnerable: true}}',
                                    t.read_text(encoding="utf-8")), encoding="utf-8")
            r = Runner(load_suite(dest / "suite.yaml"), load_providers(dest / "providers.yaml"), use_cache=False).run()
            own = {g.get("name") for g in gates}
            return {g["gate"]: g["passed"] for g in r["gates"] if g["gate"] in own}
    try:
        good, bad = gates_for(False), gates_for(True)
    except Exception as e:  # noqa: BLE001
        checks.append(Check("guarded / vulnerable", False, f"{type(e).__name__}: {e}", "fix the overlay so the suite loads"))
        return checks
    g_ok = all(good.values())
    checks.append(Check("passes on the guarded system", g_ok, "every overlay gate passes" if g_ok else
                        "failing: " + ", ".join(k for k, v in good.items() if not v),
                        "" if g_ok else "the cases or gates disagree with the pack's offline system: check the references"))
    caught = [k for k, v in bad.items() if not v]
    checks.append(Check("catches the vulnerable system", bool(caught), "fails: " + ", ".join(caught) if caught else
                        "every overlay gate still passes on the vulnerable system",
                        "" if caught else "add a case the unguarded system gets wrong (a planted instruction, a prompt leak)"))
    return checks
