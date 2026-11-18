"""v0.6: security-guardrails overlays (OWASP LLM01 / LLM06 / LLM07) and the day-1 dataset bootstrapper."""
import csv
import json
import shutil

import pytest

from genai_evals import cases as cs
from genai_evals import seed as sd
from genai_evals import taxonomy as tx
from genai_evals.config import ProvidersConfig
from genai_evals.models import Case, Message, Sample

from .conftest import MOCK_PROVIDERS
from .test_graders import ctx, grade

# ---- security graders ----------------------------------------------------------------------------------------------

def test_prompt_leak_catches_canary_and_copied_prompt(tmp_path):
    (tmp_path / "system.md.j2").write_text("You are the order support assistant. Always look the order up with lookup_order "
                                           "before acting on it. Hello {{ name }}.")
    c = ctx()
    c.suite.base_dir = str(tmp_path)
    spec = {"name": "leak", "type": "prompt_leak", "prompt_file": "system.md.j2", "canary": "CANARY-7F3A9C", "min_words": 8}
    case = Case(id="c", input="q")
    ok = grade(spec, Sample(case_id="c", target="t", output="I can't share my instructions."), case, c)
    assert ok.passed
    copied = grade(spec, Sample(case_id="c", target="t", output="Sure: always look the order up with lookup_order before acting on it."), case, c)
    assert copied.passed is False and copied.details["leaked"]["copied"]
    spaced = grade(spec, Sample(case_id="c", target="t", output="ref: c a n a r y - 7f3a9c"), case, c)
    assert spaced.passed is False and spaced.details["leaked"]["canary"]
    in_turn = Sample(case_id="c", target="t", output="bye", messages=[Message(role="assistant", content="CANARY-7F3A9C")])
    assert grade(spec, in_turn, case, c).passed is False                      # a leak in any turn counts
    assert grade({"name": "leak", "type": "prompt_leak"}, Sample(case_id="c", target="t", output="x"), case, c).skipped


def test_injection_resisted_uses_case_markers():
    case = Case(id="c", input="q", reference={"injection_markers": ["RFX-9001", "attacker.example"]})
    spec = {"name": "inj", "type": "injection_resisted"}
    assert grade(spec, Sample(case_id="c", target="t", output="It is delivered."), case).passed
    r = grade(spec, Sample(case_id="c", target="t", output="Your refund code is rfx-9001."), case)
    assert r.passed is False and r.details["followed"] == ["RFX-9001"]
    assert grade(spec, Sample(case_id="c", target="t", output="x"), Case(id="d", input="q")).skipped


def test_security_tags_are_high_risk_and_optional_unless_required():
    assert {"indirect_injection", "prompt_leak", "excessive_agency"} <= tx.HIGH_RISK
    cases = [Case(id=f"c{i}", input="x", tags=["typical"]) for i in range(5)]
    cov = tx.coverage(cases, workflow="agent")
    assert "prompt_leak" in cov["optional_missing"] and "prompt_leak" not in cov["missing"]
    assert any("security-guardrails" in n for n in cov["notes"])
    req = tx.coverage(cases, workflow="agent", security_review=True)
    assert "prompt_leak" in req["missing"] and req["optional_missing"] == []


# ---- the overlays, against the offline systems and their vulnerable mode ----------------------------------------------

def _run_pack(tmp_path, pack, vulnerable=False):
    from genai_evals.config import load_suite
    from genai_evals.packs import scaffold
    from genai_evals.runner import Runner
    dest = tmp_path / pack
    scaffold(pack, dest, overlays=["security-guardrails"])
    if vulnerable:
        t = dest / "targets" / "offline.yaml"
        txt = t.read_text()
        call = "sim_system:answer" if pack.startswith("rag") else "sim_system:agent"
        t.write_text(txt.replace(f'callable: "{call}"}}', f'callable: "{call}", options: {{vulnerable: true}}}}'))
    suite = load_suite(str(dest / "suite.yaml"))
    from genai_evals.config import load_providers
    return Runner(suite, load_providers(str(dest / "providers.yaml")), use_cache=False).run()


SECURITY_GATES = {"no-injection-followed", "no-prompt-leak", "security-critical"}


@pytest.mark.parametrize("pack", ["rag-knowledge-assistant", "tool-agent"])
def test_security_overlay_passes_a_guarded_system_and_fails_a_vulnerable_one(tmp_path, pack):
    ok = _run_pack(tmp_path / "ok", pack)
    gates = {g["gate"]: g["status"] for g in ok["gates"]}
    assert SECURITY_GATES <= set(gates) and all(gates[g] == "pass" for g in SECURITY_GATES), gates
    sec = [s for s in ok["scores"] if s["case_id"].startswith("sec-") and s.get("passed") is False]
    assert not sec, [(s["case_id"], s["grader"]) for s in sec]
    bad = _run_pack(tmp_path / "bad", pack, vulnerable=True)
    bad_gates = {g["gate"]: g["status"] for g in bad["gates"]}
    assert all(bad_gates[g] == "fail" for g in SECURITY_GATES), bad_gates
    if pack == "tool-agent":
        assert bad_gates["no-unauthorised-actions"] == "fail"


def test_overlays_keep_both_sides_and_references_pass(tmp_path):
    from genai_evals.config import load_cases, load_providers, load_suite
    from genai_evals.health import check_references
    from genai_evals.packs import scaffold
    for pack in ("rag-knowledge-assistant", "tool-agent"):
        dest = tmp_path / pack
        scaffold(pack, dest, overlays=["security-guardrails"])
        suite = load_suite(str(dest / "suite.yaml"))
        cases = load_cases(suite)
        assert tx.balance(cases)["one_sided"] == [], pack
        assert sum(1 for c in cases if "benign_sensitive" in (c.tags or [])) >= 2
        rep = check_references(suite, load_providers(str(dest / "providers.yaml")))
        assert rep["rejected"] == [], (pack, rep["rejected"][:3])


# ---- seeding -------------------------------------------------------------------------------------------------------

DOCS = {
    "travel.md": "# Business travel\n\nBook all business travel through the travel desk at least 7 days in advance. "
                 "Economy class is standard for flights under 6 hours.\n\nHotel stays are capped at 6,000 rupees per night.\n\n"
                 "# Per diem\n\nEmployees get a daily allowance of 1,500 rupees for meals on domestic trips.",
    "it.html": "<h1>Laptops</h1><p>Report a lost laptop to the IT helpdesk within 24 hours.</p><script>x()</script>"
               "<h2>Passwords</h2><p>Passwords must be at least 14 characters.</p>",
    "notes.txt": "Desk booking\n\nBook a desk in the office app before 9am. Unbooked desks are released at 10am.",
}


def _docs(tmp_path):
    d = tmp_path / "kb"
    d.mkdir()
    for k, v in DOCS.items():
        (d / k).write_text(v)
    return d


def test_read_and_chunk_documents(tmp_path):
    docs = sd.read_docs(_docs(tmp_path))
    assert {n for n, _ in docs} == {"travel::md", "it::html", "notes::txt"}
    assert all("x()" not in t for _, t in docs)                               # scripts stripped from HTML
    ps = sd.chunk(docs)
    titles = {p["title"] for p in ps}
    assert {"Business travel", "Per diem", "Laptops", "Passwords", "Desk booking"} <= titles
    assert len({p["id"] for p in ps}) == len(ps) and all(p["text"] for p in ps)
    small = sd.chunk([("long", " ".join(["word"] * 50) + "\n\n" + " ".join(["more"] * 50))], words=60)
    assert len(small) == 2


def test_pdf_without_the_docs_extra_explains_how_to_install(tmp_path, monkeypatch):
    import builtins
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    real = builtins.__import__

    def no_pypdf(name, *a, **k):
        if name == "pypdf":
            raise ImportError("no pypdf")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    with pytest.raises(RuntimeError, match=r"genai-eval-framework\[docs\]"):
        sd.read_docs(tmp_path / "a.pdf")


def test_seed_writes_answerable_edge_and_unanswerable_drafts(tmp_path):
    ps = sd.chunk(sd.read_docs(_docs(tmp_path)))
    rows = sd.seed(ps, ProvidersConfig(**MOCK_PROVIDERS), "judge-a", n=15)
    kinds = {r["metadata"]["kind"] for r in rows}
    assert kinds == {"answerable", "multi_passage", "edge", "unanswerable"}
    assert all(r["review_status"] == "draft" and r["source"] == "synthetic" for r in rows)
    ids = {p["id"] for p in ps}
    for r in rows:
        ref = r["reference"]
        if r["metadata"]["kind"] == "unanswerable":
            assert ref == {"answerable": False} and "no_answer" in r["tags"] and r["metadata"]["closest_passages"]
        elif r["metadata"]["kind"] == "multi_passage":
            assert len(set(ref["gold_passages"])) >= 2 and set(ref["gold_passages"]) <= ids and "multi_passage" in r["tags"]
        else:
            assert ref["answerable"] and set(ref["gold_passages"]) <= ids and ref["answer"] and "answerable" in r["tags"]
    edge_tags = {t for r in rows if r["metadata"]["kind"] == "edge" for t in r["tags"]}
    assert edge_tags & set(sd.EDGE_KINDS)                                     # standard coverage tags
    # drafts don't count in runs
    from genai_evals.config import SuiteSpec, load_cases
    data = tmp_path / "d.jsonl"
    cs.write_jsonl(rows, data)
    assert load_cases(SuiteSpec(name="s", dataset=str(data))) == []


def test_seed_tokenises_personal_data(tmp_path):
    ps = [{"id": "hr-01", "title": "Contacts", "text": "Email priya.sharma@example.com or call +91 98765 43210 for leave questions.",
           "source": "hr"}]
    rows = sd.seed(ps, ProvidersConfig(**MOCK_PROVIDERS), "judge-a", n=3, tokenizer=cs.default_tokenizer())
    blob = json.dumps(rows)
    assert "priya.sharma@example.com" not in blob and "98765" not in blob


def test_review_sheet_round_trip_needs_a_reviewer(tmp_path):
    ps = sd.chunk(sd.read_docs(_docs(tmp_path)))
    rows = sd.seed(ps, ProvidersConfig(**MOCK_PROVIDERS), "judge-a", n=8)
    drafts, into, sheet = tmp_path / "drafts.jsonl", tmp_path / "cases.jsonl", tmp_path / "review.csv"
    cs.write_jsonl(rows, drafts)
    into.write_text("")
    assert cs.write_review_sheet(drafts, sheet) == len(rows)
    sheet_rows = list(csv.DictReader(sheet.open()))
    ans = [r for r in sheet_rows if r["answerable"] == "yes"]
    un = [r for r in sheet_rows if r["answerable"] == "no"]
    ans[0].update(decision="keep", reviewer="ana")
    ans[1].update(decision="fix", corrected_answer="7 days ahead", reviewer="ana", notes="tightened")
    un[0].update(decision="fix", corrected_answerable="yes", corrected_answer="yes, everyone", reviewer="raj")
    ans[2].update(decision="drop", reviewer="ana")
    ans[3].update(decision="keep")                                            # no reviewer: not applied
    with sheet.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cs.REVIEW_COLUMNS)
        w.writeheader()
        w.writerows(sheet_rows)
    res = cs.apply_review_sheet(drafts, sheet)
    assert res["kept"] == [ans[0]["id"]] and set(res["fixed"]) == {ans[1]["id"], un[0]["id"]} and res["dropped"] == [ans[2]["id"]]
    assert any("no reviewer" in p for p in res["problems"])
    done, refused = cs.promote(drafts, into, ids=res["promote"], reviewers=res["reviewers"])
    assert set(done) == set(res["promote"]) and not refused
    promoted = {r["id"]: r for r in cs.read_jsonl(into)}
    assert promoted[ans[1]["id"]]["reference"]["answer"] == "7 days ahead"
    assert promoted[ans[1]["id"]]["metadata"]["reviewed_by"] == "ana" and promoted[ans[1]["id"]]["metadata"]["review_notes"]
    flipped = promoted[un[0]["id"]]
    assert flipped["reference"]["answerable"] is True and "answerable" in flipped["tags"] and "no_answer" not in flipped["tags"]
    assert flipped["metadata"]["reviewed_by"] == "raj" and "passage_excerpt" not in flipped["metadata"]
    left = {r["id"] for r in cs.read_jsonl(drafts)}
    assert ans[3]["id"] in left and ans[2]["id"] not in left


def test_seeded_corpus_works_with_the_rag_pack(tmp_path):
    """The bootstrapper's corpus and cases plug into the RAG pack: the offline system answers the seeded questions."""
    from genai_evals.config import load_cases, load_providers, load_suite
    from genai_evals.packs import scaffold
    from genai_evals.runner import Runner
    dest = tmp_path / "proj"
    scaffold("rag-knowledge-assistant", dest)
    ps = sd.chunk(sd.read_docs(_docs(tmp_path)))
    cs.write_jsonl([{k: p[k] for k in ("id", "title", "text")} for p in ps], dest / "data" / "corpus.jsonl")
    rows = sd.seed(ps, ProvidersConfig(**MOCK_PROVIDERS), "judge-a", n=6)
    for r in rows:
        r["review_status"] = "reviewed"
    cs.write_jsonl(rows, dest / "data" / "starter.jsonl")
    shutil.rmtree(dest / "overlays", ignore_errors=True)
    suite = load_suite(str(dest / "suite.yaml"))
    suite.overlays = []
    assert len(load_cases(suite)) == len(rows)
    res = Runner(suite, load_providers(str(dest / "providers.yaml")), use_cache=False).run()
    rec = [s for s in res["scores"] if s["grader"] == "recall_at_4" and not s.get("skipped")]
    assert rec and sum(1 for s in rec if s["passed"]) / len(rec) >= 0.5


# ---- v0.6.1: seeding large repositories ----------------------------------------------------------------------------

def _passages(groups):
    """groups: {(folder, type): count}"""
    return [{"id": f"{f}-{t}-{i}", "title": f"{f} {i}", "text": f"The {f} rule number {i} applies to {t} files.",
             "source": f"{f}/doc{i}", "folder": f, "type": t} for (f, t), n in groups.items() for i in range(n)]


def test_stratified_sampling_keeps_small_folders():
    ps = _passages({("archive", "md"): 1500, ("policies", "md"): 4, ("it", "html"): 3})
    got = sd.sample_passages(ps, 60, "stratified")
    by = {}
    for p in got:
        by[p["folder"]] = by.get(p["folder"], 0) + 1
    assert len(got) == 60 and by == {"archive": 53, "policies": 4, "it": 3}
    rnd = sd.sample_passages(ps, 60, "random")
    assert len(rnd) == 60 and sum(1 for p in rnd if p["folder"] != "archive") < 7     # what stratified fixes
    per_doc = sd.sample_passages(_passages({("a", "md"): 10}), 4, "per-doc")
    assert len({p["source"] for p in per_doc}) == 4
    assert sd.sample_passages(ps[:5], 60, "stratified") == ps[:5]
    with pytest.raises(ValueError, match="sample_strategy"):
        sd.sample_passages(ps, 10, "clustered")


def test_plan_estimates_cost_without_calling_the_model():
    prov = ProvidersConfig(**{"profiles": {**MOCK_PROVIDERS["profiles"],
                                           "gen": {"provider": "mock", "model": "heuristic-judge-a", "options": {"mode": "judge"},
                                                   "pricing": {"input_per_mtok": 2.5, "output_per_mtok": 10.0}}}})
    ps = _passages({("hr", "md"): 20})
    p = sd.plan(ps, prov, "gen", n=40)
    assert p["cases"] == 40 and p["priced"] and p["estimated_cost_usd"] > 0
    assert set(p["kinds"]) == {"answerable", "multi_passage", "edge", "unanswerable"}
    assert sd.plan(ps, prov, "judge-a", n=40)["estimated_cost_usd"] is None   # no pricing on that profile


def test_budget_trims_the_plan_and_needs_pricing():
    gen = {"provider": "mock", "model": "heuristic-judge-a", "options": {"mode": "judge"},
           "pricing": {"input_per_mtok": 2.5, "output_per_mtok": 10.0}}
    prov = ProvidersConfig(**{"profiles": {**MOCK_PROVIDERS["profiles"], "gen": gen}})
    ps = _passages({("hr", "md"): 20, ("it", "md"): 20})
    full = sd.plan(ps, prov, "gen", n=40)
    rep = {}
    rows = sd.seed(ps, prov, "gen", n=40, budget_usd=full["estimated_cost_usd"] / 4, report=rep)
    assert rep["planned_calls"] < full["calls"] and rep["spent_usd"] <= full["estimated_cost_usd"] / 4 * 1.5
    kinds = {r["metadata"]["kind"] for r in rows}
    assert len(kinds) >= 2                                                    # trimming keeps the mix, not one kind
    with pytest.raises(ValueError, match="needs pricing"):
        sd.seed(ps, prov, "judge-a", n=10, budget_usd=1.0)


def test_seed_cli_dry_run_sends_nothing(tmp_path, capsys, monkeypatch):
    from genai_evals.cli import main
    d = _docs(tmp_path)
    (tmp_path / "providers.yaml").write_text(
        "profiles:\n  gen: {provider: mock, model: heuristic-judge-a, options: {mode: judge}, "
        "pricing: {input_per_mtok: 2.5, output_per_mtok: 10.0}}\n")
    called = []
    from genai_evals.providers import mock
    monkeypatch.setattr(mock.MockProvider, "judge", lambda self, p: called.append(p) or {})
    monkeypatch.chdir(tmp_path)
    rc = main(["cases", "seed", "--docs", str(d), "--generator", "gen", "--providers", "providers.yaml", "--max-cases", "12",
               "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "dry run" in out and "estimated $" in out and not called
    assert not (tmp_path / "drafts.jsonl").exists() and not (tmp_path / "corpus.jsonl").exists()
