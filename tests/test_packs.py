"""Eval packs: each validates and runs offline, init scaffolds a runnable project, doctor tracks the repeatability bar."""
import json
import shutil

import pytest

from genai_evals import packs
from genai_evals.cli import main as cli
from genai_evals.config import DatasetError, load_cases, load_suite

PACKS = packs.available()


def test_three_workflow_packs_ship():
    assert set(PACKS) >= {"rag-knowledge-assistant", "tool-agent", "structured-extraction"}


@pytest.mark.parametrize("name", PACKS)
def test_pack_validates_and_runs_offline(name, monkeypatch):
    for k in ("EVAL_TARGETS", "GENAI_EVALS_PROVIDERS", "EVAL_JUDGE_A", "EVAL_JUDGE_B"):
        monkeypatch.delenv(k, raising=False)
    checks = packs.validate_pack(packs.resolve(name))
    bad = [f"{c.name}: {c.detail}" for c in checks if c.ok is False]
    assert not bad, bad
    names = {c.name for c in checks if c.ok}
    assert {"files", "manifest", "suite", "compatibility", "generated docs", "offline run", "online run"} <= names


@pytest.mark.parametrize("name", PACKS)
def test_init_scaffolds_a_runnable_project(name, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for k in ("EVAL_TARGETS", "GENAI_EVALS_PROVIDERS", "EVAL_JUDGE_A", "EVAL_JUDGE_B"):
        monkeypatch.delenv(k, raising=False)
    m = packs.manifest(packs.resolve(name))
    overlays = [o["name"] for o in m.get("overlays", [])]
    args = ["init", "proj", "--pack", name] + sum((["--overlay", o] for o in overlays), [])
    assert cli(args) == 0
    out = capsys.readouterr().out
    root = tmp_path / "proj"
    for f in ("suite.yaml", "providers.live.yaml", ".env.example", ".gitignore", "RUNBOOK.md", "HANDOVER.md", "validation.yaml",
              "ci/github-actions-evals.yml", "baselines/README.md", "out/offline/report.html", "out/offline/scorecard.html"):
        assert (root / f).exists(), f
    assert "sample report" in out and "EVAL_TARGETS=" in out
    r = json.loads((root / "out/offline/result.json").read_text())
    assert r["passed"] and r["pack"].startswith(name) and r["report"]["failure_categories"]
    if overlays:
        assert f"overlays/{overlays[0]}" in (root / "suite.yaml").read_text()
        assert any(c["id"] for c in r["cases"] if c["id"].split("-")[0] in ("ins", "gst"))
    assert "Failure categories" in (root / "out/offline/report.html").read_text()
    monkeypatch.chdir(root)
    assert cli(["run", "suite.yaml", "--out", "out/again", "--no-cache", "--allow-missing-baseline"]) == 0
    assert cli(["init", str(root), "--pack", name]) == 2                     # refuses a non-empty folder


def test_dataset_schema_errors_name_the_case(tmp_path):
    proj = packs.scaffold("rag-knowledge-assistant", tmp_path / "p")
    data = proj / "data" / "starter.jsonl"
    rows = [json.loads(x) for x in data.read_text().splitlines()]
    rows[0]["reference"].pop("gold_passages")
    rows[1]["unexpected"] = 1
    data.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with packs.offline_env():
        with pytest.raises(DatasetError) as e:
            load_cases(load_suite(proj / "suite.yaml"))
    assert "case kb-q01" in str(e.value) and "gold_passages" in str(e.value) and "case kb-q02" in str(e.value)


def test_targets_switch_by_env_var(tmp_path, monkeypatch):
    proj = packs.scaffold("tool-agent", tmp_path / "p")
    monkeypatch.setenv("EVAL_TARGETS", "targets/tool-loop.yaml")
    monkeypatch.setenv("EVAL_TARGET_PROFILE", "openai")
    s = load_suite(proj / "suite.yaml")
    assert s.targets[0].framework == "tool_loop" and s.targets[0].tools_module == "tools"
    monkeypatch.setenv("EVAL_TARGETS", "targets/missing.yaml")
    with pytest.raises(FileNotFoundError):
        load_suite(proj / "suite.yaml")


def test_unknown_pack_or_overlay(tmp_path):
    with pytest.raises(FileNotFoundError):
        packs.scaffold("nope", tmp_path / "a")
    with pytest.raises(ValueError, match="no overlay"):
        packs.scaffold("tool-agent", tmp_path / "b", overlays=["insurance-policy"])


def test_doctor_tracks_the_repeatability_bar(tmp_path, monkeypatch):
    proj = packs.scaffold("rag-knowledge-assistant", tmp_path / "p")
    checks = {c.name: c for c in packs.doctor(proj)}
    assert checks["offline demo"].ok is True
    for n in ("provider connected", "telemetry connected", "report", "release gate", "judges calibrated", "handover", "adapters validated"):
        assert checks[n].ok is False and checks[n].next, n

    # simulate the evidence a finished engagement leaves behind
    res = packs.run_offline(proj, tmp_path / "o")
    live = json.loads(json.dumps(res["offline"], default=str))
    live["adapters"] = [{"kind": "provider", "name": "openai", "status": "fixture_tested"}]
    (proj / "out/live").mkdir(parents=True)
    (proj / "out/live/result.json").write_text(json.dumps(live))
    for f in ("report.html", "scorecard.html"):
        shutil.copy(tmp_path / "o/offline" / f, proj / "out/live" / f)
    on = json.loads(json.dumps(res["online"], default=str))
    for s in on["sources"]:
        s["fixture"] = False
    on["adapters"] = [{"kind": "source", "name": "otlp", "status": "fixture_tested"}]
    (proj / "out/online").mkdir()
    (proj / "out/online/result.json").write_text(json.dumps(on))
    assert cli(["baseline", "promote", str(proj / "out/live/result.json"), "--out", str(proj / "baselines/main.json")]) == 0
    (proj / ".github/workflows").mkdir(parents=True)
    shutil.copy(proj / "ci/github-actions-evals.yml", proj / ".github/workflows/evals.yml")
    cal = {g: {"kappa": 0.8, "kappa_ci": [0.6, 0.9], "n": 40} for g in ("faithfulness", "relevance", "safe_content")}
    (proj / "calibration.json").write_text(json.dumps(cal))
    suite = proj / "suite.yaml"
    suite.write_text(suite.read_text().replace("# calibration: calibration.json", "calibration: calibration.json"))
    h = proj / "HANDOVER.md"
    h.write_text(h.read_text().replace("- [ ]", "- [x]").replace("Owner: ____", "Owner: Customer platform team"))
    from genai_evals.plugins import record_validation
    record_validation(proj / "validation.yaml", "provider", "openai", "live_tested", environment="customer sandbox")
    record_validation(proj / "validation.yaml", "source", "otlp", "customer_validated", environment="customer prod")
    checks = packs.doctor(proj, run=False)
    assert all(c.ok is not False for c in checks), [(c.name, c.detail) for c in checks if c.ok is False]
    monkeypatch.chdir(proj)
    assert cli(["doctor", ".", "--no-run"]) == 0


def test_pack_cli(capsys):
    assert cli(["pack", "list"]) == 0
    assert "tool-agent" in capsys.readouterr().out
    assert cli(["pack", "info", "structured-extraction"]) == 0
    out = capsys.readouterr().out
    assert "Failure categories" in out and "[fixture_tested]" in out and "india-gst" in out
