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


def available() -> list[str]:
    return sorted(p.name for p in packs_root().iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "pack.yaml").exists())


def resolve(name_or_dir: str | Path) -> Path:
    p = Path(name_or_dir)
    if (p / "pack.yaml").exists():
        return p
    q = packs_root() / str(name_or_dir)
    if (q / "pack.yaml").exists():
        return q
    raise FileNotFoundError(f"no pack '{name_or_dir}' (available: {', '.join(available())})")


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
    known = {o["name"] for o in m.get("overlays", [])}
    for o in overlays or []:
        if o not in known:
            raise ValueError(f"pack '{m['name']}' has no overlay '{o}' (available: {', '.join(sorted(known)) or 'none'})")
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in ("overlays",) or item.name.startswith("__"):
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    for o in overlays or []:
        shutil.copytree(src / "overlays" / o, dest / "overlays" / o, ignore=_IGNORE, dirs_exist_ok=True)
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


def doctor(project: str | Path, run: bool = True) -> list[Check]:
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
                            "keep your source in sources.yaml, remove `fixture:`, `genai-evals online ... --out out/online`"))

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
    judged = [g.name for g in suite.graders if g.type in JUDGE_GRADER_TYPES or g.type == "policy"]
    if judged:
        cal = suite.path(suite.calibration) if suite.calibration else None
        if not cal or not cal.exists():
            checks.append(Check("judges calibrated", False, f"judge-graded: {', '.join(judged)}; no calibration file", "RUNBOOK step 5"))
        else:
            data = json.loads(cal.read_text(encoding="utf-8"))
            bad = untrusted_graders(data, suite.min_judge_kappa or 0.0, suite.min_calibration_n)
            uncal = [g for g in judged if g not in data]
            checks.append(Check("judges calibrated", not bad and not uncal,
                                "; ".join([f"untrusted: {', '.join(bad)}"] * bool(bad) + [f"not calibrated: {', '.join(uncal)}"] * bool(uncal)) or "all judges trusted",
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
