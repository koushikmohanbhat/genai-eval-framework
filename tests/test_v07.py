"""v0.7: run profiles, external prompt versions, PII detector sets and domain overlays, Day-0 seeding, Promptfoo engine."""
import json

import pytest

from genai_evals import packs
from genai_evals.cli import main as cli
from genai_evals.config import load_cases, load_suite

# ---- run profiles ---------------------------------------------------------------------------------------------------

def test_profiles_change_repeats_judges_cases_and_skip_gates_they_cant_evaluate(tmp_path):
    proj = packs.scaffold("rag-knowledge-assistant", tmp_path / "p")
    s = proj / "suite.yaml"
    s.write_text(s.read_text().replace("profiles:\n", """profiles:
  smoke:
    repeats: 1
    skip_graders: [citations]
    splits: [representative]
    max_cases: 5
""", 1))
    base = load_suite(s)
    smoke = load_suite(s, profile="smoke")
    assert smoke.active_profile["name"] == "smoke"
    skipped = {g["gate"]: g["reason"] for g in smoke.active_profile["skipped_gates"]}
    assert "citations" in skipped["cited"]                       # reads a dropped grader
    assert "stress" in skipped["refuses-correctly"]              # reads a split the profile leaves out
    assert "safety-cases" not in skipped and "latency-budget" not in skipped   # critical_pass / ops metrics stay
    cases = load_cases(smoke)
    crit = [c for c in load_cases(base) if c.critical]
    assert all(c.id in {x.id for x in cases} for c in crit)     # critical cases always run
    assert len([c for c in cases if not c.critical]) <= max(0, 5 - len(crit))

    pr = load_suite(s, profile="pr-check")
    assert pr.repeats == 1 and len(pr.judges) == 1
    rel = load_suite(s, profile="release-gate")
    assert rel.repeats == 3 and len(rel.judges) == 2 and rel.case_filter is None
    with pytest.raises(ValueError, match="profile 'nope'"):
        load_suite(s, profile="nope")


def test_profile_cli_records_profile_in_result(tmp_path, monkeypatch, capsys):
    proj = packs.scaffold("structured-extraction", tmp_path / "p")
    monkeypatch.chdir(proj)
    assert cli(["run", "suite.yaml", "--profile", "pr-check", "--out", "out/pr", "--no-html", "--allow-missing-baseline"]) == 0
    r = json.loads((proj / "out/pr/result.json").read_text())
    assert r["profile"]["name"] == "pr-check" and r["repeats"] == 1
    assert "profile pr-check" in capsys.readouterr().out
    import os
    assert "GENAI_EVALS_PROFILE" not in os.environ                 # restored after the command
    assert cli(["run", "suite.yaml", "--profile", "missing", "--no-html"]) == 2


# ---- external prompt versions ----------------------------------------------------------------------------------------

def test_parse_prompt_version_forms():
    from genai_evals.config import parse_prompt_version
    assert parse_prompt_version("langfuse:claims_agent@7") == {"langfuse:claims_agent": "7"}
    assert parse_prompt_version("arn:aws:bedrock:ap-south-1:111122223333:prompt/ABC123:3") == \
        {"arn:aws:bedrock:ap-south-1:111122223333:prompt/ABC123": "3"}
    assert parse_prompt_version({"vertex:claims": "v2"}) == {"vertex:claims": "v2"}
    assert parse_prompt_version("my-prompt") == {"my-prompt": "unversioned"}
    assert parse_prompt_version(None) == {}


def test_external_prompt_version_is_recorded_without_prompts_lock(tmp_path, monkeypatch):
    proj = packs.scaffold("structured-extraction", tmp_path / "p")
    monkeypatch.chdir(proj)
    tname = load_suite("suite.yaml").targets[0].name
    assert cli(["run", "suite.yaml", "--prompt-version", f"{tname}=langfuse:invoice_extractor@12", "--out", "out/x",
                "--no-html", "--allow-missing-baseline"]) == 0
    r = json.loads((proj / "out/x/result.json").read_text())
    assert r["prompt_versions"][tname]["langfuse:invoice_extractor"] == "12"
    assert all(s["prompt_versions"].get("langfuse:invoice_extractor") == "12" for s in r["samples"])
    assert cli(["run", "suite.yaml", "--prompt-version", "nope=x@1", "--no-html"]) == 2


# ---- PII detector sets ----------------------------------------------------------------------------------------------

def test_detector_sets_and_checksums():
    from genai_evals import pii
    hipaa = pii.build({"sets": ["us_hipaa"]})
    names = lambda text, dets: [f.type for f in pii.detect(text, detectors=dets)]  # noqa: E731
    assert names("SSN 123-45-6789, MBI 1EG4-TE5-MK73, NPI 1234567893, call (212) 555-0147 from 10.20.30.40", hipaa) == \
        ["US_SSN", "US_MBI", "US_NPI", "US_PHONE", "IP_ADDRESS"]
    assert names("SSN 000-12-3456 or 1234567893 without the NPI keyword", hipaa) == []    # invalid area, no context
    uk = pii.build({"sets": ["uk"]})
    assert names("NHS 943 476 5919, NI AB 12 34 56 C", uk) == ["UK_NHS", "UK_NINO"]
    assert names("NHS 943 476 5918", uk) == []                                          # mod 11 fails
    bank = pii.build({"sets": ["banking"]})
    assert names("IBAN GB82 WEST 1234 5698 7654 32 and card 4111 1111 1111 1111", bank) == ["IBAN", "CARD_NUMBER"]
    assert names("GB82 WEST 1234 5698 7654 33", bank) == []                             # mod 97 fails
    default = pii.build()
    assert {"AADHAAR", "PAN", "EMAIL", "CARD_NUMBER"} <= {d.name for d in default}
    assert "US_SSN" not in {d.name for d in default}                                    # defaults unchanged from v0.6
    custom = pii.build({"sets": ["global"], "custom": [{"name": "POLICY_NO", "pattern": r"\bMOT-\d{4}-\d{6}\b"}]})
    assert names("policy MOT-2026-004512, mail a@b.co", custom) == ["POLICY_NO", "EMAIL"]
    with pytest.raises(ValueError, match="unknown detector set"):
        pii.build({"sets": ["mars"]})
    with pytest.raises(ValueError, match="unknown validator"):
        pii.build({"custom": [{"name": "X", "pattern": "x", "validator": "crc"}]})


def test_suite_pii_drives_tokeniser_and_pii_leak(tmp_path):
    import asyncio

    from genai_evals import pii
    from genai_evals.config import GraderSpec, load_providers
    from genai_evals.dpdp import BuiltinTokenizer
    from genai_evals.graders.base import build_grader
    from genai_evals.models import Case, Sample
    from genai_evals.runner import Runner
    proj = packs.scaffold("structured-extraction", tmp_path / "p")
    s = proj / "suite.yaml"
    s.write_text(s.read_text() + "pii: {sets: [global, us_hipaa]}\n")
    suite = load_suite(s)
    Runner(suite, load_providers(proj / "providers.yaml"), use_cache=False)
    assert "US_SSN" in pii.active_names() and "AADHAAR" not in pii.active_names()
    tok = BuiltinTokenizer("k")
    assert "123-45-6789" not in tok.tokenize("patient SSN 123-45-6789")
    g = build_grader(GraderSpec(name="no_pii", type="pii_leak"), None)
    sc = asyncio.run(g.grade(Sample(case_id="c", target="t", output="SSN is 123-45-6789"), Case(id="c", input="q")))
    assert sc.value == 0.0 and sc.details["leaked_types"] == ["US_SSN"]
    pii.configure(None)


# ---- financial-services overlay --------------------------------------------------------------------------------------

def _fs_run(tmp_path, noncompliant=False):
    from genai_evals.config import load_providers
    from genai_evals.runner import Runner
    dest = packs.scaffold("rag-knowledge-assistant", tmp_path / ("bad" if noncompliant else "good"),
                          overlays=["financial-services"])
    if noncompliant:
        t = dest / "targets" / "offline.yaml"
        t.write_text(t.read_text().replace('callable: "sim_system:answer"}', 'callable: "sim_system:answer", options: {noncompliant: true}}'))
    suite = load_suite(dest / "suite.yaml")
    assert "IBAN" in suite.pii["sets"] or "banking" in suite.pii["sets"]
    assert any(c["name"] == "FOLIO_NO" for c in suite.pii["custom"])
    return Runner(suite, load_providers(dest / "providers.yaml"), use_cache=False).run()


FS_GATES = {"fs-risk-disclaimer", "fs-no-personal-advice", "fs-no-guaranteed-returns"}


def test_financial_services_overlay_passes_a_compliant_system_and_fails_a_noncompliant_one(tmp_path):
    good = _fs_run(tmp_path)
    assert good["passed"], [g["gate"] for g in good["gates"] if not g["passed"]]
    assert FS_GATES <= {g["gate"] for g in good["gates"] if g["passed"]}
    bad = _fs_run(tmp_path, noncompliant=True)
    failed = {g["gate"] for g in bad["gates"] if not g["passed"]}
    assert FS_GATES <= failed and not bad["passed"]


# ---- Day-0 discovery ------------------------------------------------------------------------------------------------

def test_day0_builds_drafts_sheet_and_sme_instructions(tmp_path, monkeypatch, capsys):
    from tests.test_security_and_seed import _docs
    d = _docs(tmp_path)
    (tmp_path / "providers.yaml").write_text("profiles:\n  gen: {provider: mock, model: heuristic-judge-a, options: {mode: judge}}\n")
    on = tmp_path / "online.json"
    proj = packs.scaffold("rag-knowledge-assistant", tmp_path / "p")
    on.write_text(json.dumps(packs.run_offline(proj, tmp_path / "o")["online"], default=str))
    monkeypatch.chdir(tmp_path)
    assert cli(["cases", "day0", "--docs", str(d), "--generator", "gen", "--providers", "providers.yaml",
                "--from-online", str(on), "--production-n", "5", "--max-cases", "12", "--out-dir", "day0"]) == 0
    out = capsys.readouterr().out
    assert "Day 0 package" in out and "day 2: genai-evals cases promote" in out
    rows = [json.loads(x) for x in (tmp_path / "day0/drafts.jsonl").read_text().splitlines() if x.strip()]
    assert sum("sampled" not in r["tags"] for r in rows) >= 8 and sum("sampled" in r["tags"] for r in rows) >= 1
    sheet = (tmp_path / "day0/review.csv").read_text()
    assert sheet.count("\n") >= len(rows) and "decision" in sheet.splitlines()[0]
    assert "keep" in (tmp_path / "day0/SME-INSTRUCTIONS.md").read_text()
    assert (tmp_path / "day0/corpus.jsonl").exists()


# ---- Promptfoo as an execution engine --------------------------------------------------------------------------------

SIMPLE_SUITE = """
name: faq
dataset:
  - {id: q1, input: "What is the capital of France?", reference: {contains: Paris}, tags: [geo]}
  - {id: q2, input: "What is 2+2?", reference: {contains: "4"}, critical: true, tags: [math]}
  - {id: q3, input: "Name a primary colour", reference: {contains: red}, tags: [art]}
  - {id: q4, input: "What is H2O?", reference: {contains: water}, tags: [science]}
  - {id: q5, input: "Opposite of hot?", reference: {contains: cold}, tags: [words]}
targets:
  - {name: assistant, kind: model, profile: echo}
graders:
  - {name: mentions_answer, type: contains}
gates:
  - {name: accuracy, rule: "mean(mentions_answer) >= 0.0"}
  - {name: critical, rule: "critical_pass()"}
"""


def test_promptfoo_engine_runs_promptfoo_then_gates_its_results(tmp_path, monkeypatch, capsys):
    import sys

    from genai_evals.report.writers import writer
    (tmp_path / "suite.yaml").write_text(SIMPLE_SUITE)
    (tmp_path / "providers.yaml").write_text("profiles:\n  echo: {provider: mock, model: echo}\n")
    monkeypatch.chdir(tmp_path)
    assert cli(["run", "suite.yaml", "--providers", "providers.yaml", "--out", "native", "--no-html"]) in (0, 1)
    native = json.loads((tmp_path / "native/result.json").read_text())
    (tmp_path / "canned.json").write_text(writer("promptfoo").render(native))
    # a stand-in for `promptfoo eval -c cfg -o out`: checks it got the exported config, writes Promptfoo's results
    fake = tmp_path / "fake_promptfoo.py"
    fake.write_text("import sys, shutil, pathlib\n"
                    "a = sys.argv[1:]\n"
                    "assert a[0] == 'eval' and pathlib.Path(a[a.index('-c') + 1]).exists()\n"
                    f"shutil.copy({str(tmp_path / 'canned.json')!r}, a[a.index('-o') + 1])\n"
                    "sys.exit(100)\n")
    rc = cli(["run", "suite.yaml", "--providers", "providers.yaml", "--engine", "promptfoo",
              "--promptfoo-cmd", f"{sys.executable} {fake}", "--out", "pf", "--no-html"])
    r = json.loads((tmp_path / "pf/result.json").read_text())
    assert r["mode"] == "engine:promptfoo" and r["engine"]["name"] == "promptfoo"
    assert (tmp_path / "pf/promptfooconfig.yaml").exists()
    assert {g["gate"] for g in r["gates"]} == {"accuracy", "critical"}
    assert rc == (0 if r["passed"] else 1)
    nat = {(s["case_id"], s["grader"]): s["value"] for s in native["scores"]}
    assert {(s["case_id"], s["grader"]): s["value"] for s in r["scores"]} == nat


def test_promptfoo_engine_refuses_suites_it_cannot_run(tmp_path, monkeypatch, capsys):
    proj = packs.scaffold("tool-agent", tmp_path / "p")
    monkeypatch.chdir(proj)
    assert cli(["run", "suite.yaml", "--engine", "promptfoo", "--promptfoo-cmd", "false", "--no-html"]) == 2
    assert "agent, HTTP and Python targets stay on the native engine" in capsys.readouterr().err


def test_promptfoo_export_writes_tagged_and_reference_assertions_per_case():
    from pathlib import Path

    from genai_evals.config import load_providers
    from genai_evals.interop import export_config
    ex = Path(__file__).resolve().parents[1] / "examples/promptfoo-engine"
    cfg, _ = export_config(load_suite(ex / "suite.yaml"), load_providers(ex / "providers.yaml"))
    by = {t["description"]: t for t in cfg["tests"]}
    metrics = lambda t: {a["metric"] for a in t.get("assert", [])}  # noqa: E731
    assert metrics(by["math-1"]) == {"mentions_answer", "math_answer"}       # tag-scoped grader only on its cases
    assert metrics(by["geo-1"]) == {"mentions_answer"}
    assert next(a for a in by["geo-1"]["assert"] if a["metric"] == "mentions_answer")["value"] == "Paris"
    assert {a["metric"] for a in cfg["defaultTest"]["assert"]} == {"no_secret"}   # applies to every case
