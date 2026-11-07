"""The adapter conformance kit: every built-in passes, and it catches what it says it catches."""
import json

import pytest

from genai_evals import conformance as cf
from genai_evals.cli import main as cli
from genai_evals.sources.base import Source, register

SPECS = cf.builtin_specs()


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: getattr(s, "type", getattr(s, "provider", "?")))
def test_builtin_adapters_conform(spec):
    res = cf.assert_conformant(spec)
    checks = {r.check for r in res}
    assert checks >= (set(cf.SOURCE_CHECKS) if isinstance(spec, cf.SourceConformance) else set(cf.PROVIDER_CHECKS))


def test_live_path_checks_cover_cloudwatch_and_memory_sources():
    res = cf.run([s for s in SPECS if isinstance(s, cf.SourceConformance) and s.client_factory])
    by = {(r.adapter, r.check): r.status for r in res}
    for a in ("aws_agentcore_observability", "aws_bedrock_invocation_logs", "aws_agentcore_evaluations", "aws_agentcore_memory"):
        assert by[(a, "pagination")] == by[(a, "retries")] == cf.PASS


class _Fragile(Source):
    kind = "spans"

    def samples(self):
        from genai_evals.models import Sample
        out = []
        for r in self.records():
            out.append(Sample(case_id=r["id"], target="t", input=r["body"]["q"], output=r["body"]["a"], origin=f"source:{self.spec.name}",
                              metadata={"start_ns": r["ts"]}))
        return out


@pytest.fixture
def fragile():
    from genai_evals.sources.base import REGISTRY
    register("conf_fragile")(_Fragile)
    yield
    REGISTRY.pop("conf_fragile", None)


def test_kit_catches_crashes_order_dependence_and_bad_timestamps(tmp_path, fragile):
    f = tmp_path / "f.jsonl"
    f.write_text("".join(json.dumps({"id": f"c{i}", "ts": 1700000000 + i, "body": {"q": "q", "a": "a"}}) + "\n" for i in range(4)))
    res = {r.check: r for r in cf.run([cf.SourceConformance("conf_fragile", f, source_class=_Fragile)])}
    assert res["missing_fields"].status == cf.FAIL and "KeyError" in res["missing_fields"].detail
    assert res["timestamps"].status == cf.FAIL                        # seconds, not epoch ns
    assert res["pagination"].status == res["retries"].status == cf.NOT_COVERED
    assert res["formats"].status == res["empty"].status == cf.PASS


def test_conformance_cli_matrix(capsys):
    assert cli(["conformance", "--adapter", "otlp", "--adapter", "openai"]) == 0
    out = capsys.readouterr().out
    assert "| `otlp` |" in out and "| `openai` |" in out and "not covered by the spec" in out


def test_adapter_docs_are_up_to_date():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("build_adapter_docs", root / "scripts" / "build_adapter_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (root / "docs" / "adapters.md").read_text() == mod.render(), "run: python scripts/build_adapter_docs.py"
