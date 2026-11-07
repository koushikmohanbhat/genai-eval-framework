"""Plugin contracts and registry, adapter maturity, validation records, portable results and stores."""
import json
from types import SimpleNamespace

import pytest

from genai_evals import plugins
from genai_evals.cli import main as cli
from genai_evals.config import load_providers, load_suite
from genai_evals.results import ResultSchemaError, upgrade, validate
from genai_evals.runner import Runner


def test_catalog_covers_every_builtin_and_levels_are_honest():
    from genai_evals.conformance import SourceConformance, builtin_specs
    from genai_evals.sources import REGISTRY
    names = set(plugins.names("source"))
    assert set(REGISTRY) <= names                       # every registered source is in the catalog
    specs = {s.type for s in builtin_specs() if isinstance(s, SourceConformance)}
    provs = {s.provider for s in builtin_specs() if not isinstance(s, SourceConformance)}
    for (kind, name), a in plugins.catalog().items():
        assert a.maturity in plugins.LEVELS[:2], f"{name}: built-ins can't claim live testing"
        if kind == "source" and a.maturity == "fixture_tested":
            assert name in specs, f"{name} claims fixture_tested without a conformance spec"
        if kind == "provider" and a.maturity == "fixture_tested" and name != "mock":
            assert name in provs, f"{name} claims fixture_tested without a conformance spec"
        plugins.load(kind, name)                          # every ref imports


def test_entry_point_plugins_register_without_forking(monkeypatch):
    class AcmeSource:
        contract_version, maturity, cloud = "1", "fixture_tested", "azure"
    class FutureSource:
        contract_version = "2"
    mod = SimpleNamespace(AcmeSource=AcmeSource, FutureSource=FutureSource)
    monkeypatch.setitem(__import__("sys").modules, "acme_evals_plugin", mod)
    eps = {"genai_evals.sources": [SimpleNamespace(name="azure_monitor", value="acme_evals_plugin:AcmeSource", dist=SimpleNamespace(name="acme-evals")),
                                   SimpleNamespace(name="future", value="acme_evals_plugin:FutureSource", dist=None),
                                   SimpleNamespace(name="otlp", value="acme_evals_plugin:AcmeSource", dist=None)]}
    monkeypatch.setattr(plugins, "_entry_points", lambda g: eps.get(g, []))
    plugins.catalog.cache_clear()
    try:
        assert plugins.load("source", "azure_monitor") is AcmeSource
        a = plugins.info("source", "azure_monitor")
        assert a.origin == "plugin:acme-evals" and a.cloud == "azure" and a.maturity == "fixture_tested"
        assert plugins.info("source", "otlp").origin == "builtin"          # plugins never shadow built-ins
        with pytest.raises(RuntimeError, match="contract 2"):
            plugins.load("source", "future")
        with pytest.raises(KeyError):
            plugins.load("source", "nope")
    finally:
        plugins.catalog.cache_clear()


def test_validation_records_raise_status_and_show_in_reports(tmp_path, ex_dir):
    v = tmp_path / "validation.yaml"
    plugins.record_validation(v, "provider", "mock", "live_tested", environment="partner sandbox", evidence="run_1", by="me")
    recs = plugins.load_validation(v)
    e = plugins.effective("provider", "mock", recs)
    assert e["status"] == "live_tested" and e["builtin_status"] == "fixture_tested" and e["records"][0]["environment"] == "partner sandbox"
    assert plugins.effective("source", "otlp", recs)["status"] == "fixture_tested"
    v.write_text("adapters: [{kind: source, name: otlp, status: fixture_tested}]\n")
    with pytest.raises(ValueError, match="live_tested or customer_validated"):
        plugins.load_validation(v)
    with pytest.raises(KeyError):
        plugins.record_validation(tmp_path / "x.yaml", "source", "made_up", "live_tested")

    suite = load_suite(ex_dir / "policy_qa.yaml")
    suite.validation = str(tmp_path / "rec.yaml")
    plugins.record_validation(tmp_path / "rec.yaml", "provider", "mock", "customer_validated", environment="acme prod")
    r = Runner(suite, load_providers(ex_dir / "providers.yaml"), use_cache=False).run()
    assert {"kind": "provider", "name": "mock"}.items() <= next(a for a in r["adapters"] if a["name"] == "mock").items()
    assert next(a for a in r["adapters"] if a["name"] == "mock")["status"] == "customer_validated"
    from genai_evals.report.html import render_report
    from genai_evals.report.text import markdown_summary
    assert "Adapters used" in render_report(r) and "customer validated" in render_report(r)
    assert "provider `mock` (customer validated)" in markdown_summary(r)


def test_results_are_versioned_validated_and_upgraded(ex_dir):
    r = Runner(load_suite(ex_dir / "claims_extraction.yaml"), load_providers(ex_dir / "providers.yaml"), use_cache=False).run()
    assert r["schema"] == "genai-evals/result" and r["schema_version"] == "1.0" and r["framework_version"]
    assert validate(json.loads(json.dumps(r, default=str))) == []
    legacy = {k: v for k, v in json.loads(json.dumps(r, default=str)).items() if k not in ("schema", "schema_version", "framework_version", "adapters", "graders", "mode")}
    for g in legacy["gates"]:
        g.pop("status", None)
    up = upgrade(legacy)
    assert up["upgraded_from"] == "0" and validate(up) == [] and all("status" in g for g in up["gates"])
    with pytest.raises(ResultSchemaError, match="newer"):
        upgrade({**r, "schema_version": "2.0"})
    with pytest.raises(ResultSchemaError):
        upgrade({"schema": "something/else"})
    bad = {**json.loads(json.dumps(r, default=str)), "passed": "yes"}
    assert any("passed" in p for p in validate(bad))


def test_portable_exports_and_file_store(tmp_path, ex_dir, monkeypatch):
    monkeypatch.chdir(ex_dir)
    out = tmp_path / "run"
    assert cli(["run", "claims_extraction.yaml", "--out", str(out), "--no-cache", "--store", f"file://{tmp_path / 'store'}"]) in (0, 1)
    assert cli(["result", "validate", str(out / "result.json")]) == 0
    assert cli(["result", "export", str(out / "result.json"), "--format", "csv,jsonl,promptfoo", "--out", str(tmp_path / "x")]) == 0
    rows = (tmp_path / "x" / "scores.jsonl").read_text().splitlines()
    r = json.loads((out / "result.json").read_text())
    assert len(rows) == len(r["scores"]) and (tmp_path / "x" / "scores.csv").read_text().startswith("run_id,suite,target")
    from genai_evals.interop.promptfoo import import_results
    samples, scores = import_results(tmp_path / "x" / "promptfoo-results.json")
    assert len(samples) == len(r["samples"]) and scores
    from genai_evals.store import FileStore, store_for
    st = store_for(f"file://{tmp_path / 'store'}")
    assert isinstance(st, FileStore) and st.history(r["suite"])[0]["run_id"] == r["run_id"]
    assert (tmp_path / "store" / f"{r['run_id']}.json").exists()
    with pytest.raises(KeyError):
        store_for("s3x://bucket/prefix")
