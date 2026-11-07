import shutil

import pytest

from genai_evals.prompts import PromptStore, bedrock_payload, langfuse_payload, load


@pytest.fixture
def store(tmp_path, ex_dir):
    shutil.copytree(ex_dir / "prompts", tmp_path / "prompts")
    shutil.copy(ex_dir / "prompts.lock", tmp_path / "prompts.lock")
    return PromptStore(tmp_path / "prompts")


def test_resolve_stage_and_render(store):
    rp = store.render("claims_agent", "prod", {"language": "hi"})
    assert rp.version == "1.3.2" and rp.hash.startswith("sha256:")
    assert "Reply in hi" in rp.system and "verify the policy" in rp.system
    assert {t["name"] for t in rp.tools} == {"lookup_policy", "check_coverage", "create_claim", "escalate_to_human"}
    assert store.render("claims_agent", "staging").version == "1.4.0"
    assert store.render("claims_agent@1.2.0").version == "1.2.0"


def test_check_clean_then_detects_edit_of_locked_version(store):
    assert store.check() == []
    p = store.root / "claims_agent" / "1.3.2" / "system.md.j2"
    p.write_text(p.read_text() + "\nBe brief.\n")
    problems = store.check()
    assert any("changed after it was locked" in x for x in problems)
    with pytest.raises(ValueError):
        store.lock()


def test_new_version_must_be_locked_before_promotion(store):
    shutil.copytree(store.root / "claims_agent" / "1.4.0", store.root / "claims_agent" / "1.5.0")
    meta = store.root / "claims_agent" / "1.5.0" / "prompt.yaml"
    meta.write_text(meta.read_text().replace("version: 1.4.0", "version: 1.5.0"))
    assert any("not in prompts.lock" in x for x in store.check())
    with pytest.raises(KeyError):
        store.promote("claims_agent", "1.5.0", "prod")
    assert "claims_agent@1.5.0" in store.lock()["added"]
    assert store.promote("claims_agent", "1.5.0", "staging") == "1.4.0"
    assert store.check() == []


def test_version_folder_must_match_metadata(store):
    shutil.copytree(store.root / "claims_agent" / "1.4.0", store.root / "claims_agent" / "1.9.0")
    assert any("does not match folder" in x for x in store.check())


def test_dpdp_changes_and_diff(store):
    ch = store.dpdp_changes("claims_agent@1.4.0")
    assert ch["data_elements"] == {"HEALTH_INFO"} and ch["purposes"] == {"injury_assistance"}
    assert store.dpdp_changes("claims_agent@1.3.2") == {"data_elements": set(), "purposes": set()}
    d = store.diff("claims_agent@1.3.2", "claims_agent@1.4.0")
    assert "-Always verify the policy first" in d and "+If anyone is injured" in d


def test_runtime_load_annotates_otel_span(store):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exp = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exp))
    with tp.get_tracer("app").start_as_current_span("chat"):
        from opentelemetry import trace
        # use the explicit provider's span as current
        assert trace.get_current_span().is_recording()
        rp = load("claims_agent", stage="prod", root=store.root)
    attrs = exp.get_finished_spans()[0].attributes
    assert attrs["prompt.id"] == "claims_agent" and attrs["prompt.version"] == rp.version and attrs["prompt.hash"] == rp.hash


def test_registry_payloads(store):
    rp = store.render("policy_qa")
    b = bedrock_payload(rp, "model-x")
    assert b["variants"][0]["templateType"] == "TEXT" and b["variants"][0]["modelId"] == "model-x" and "{{ question }}" in b["variants"][0]["templateConfiguration"]["text"]["text"]
    lf = langfuse_payload(rp, "production")
    assert lf["labels"] == ["production"] and lf["config"]["version"] == "1.0.0"
