"""Environment interpolation, .env loading and the `genai-evals init` starter project."""
import pytest

from genai_evals.cli import main
from genai_evals.config import MissingEnvError, interpolate_env, load_env_file, load_providers


def test_interpolate_env_defaults_and_escapes():
    env = {"REGION": "eu-west-1", "EMPTY": ""}
    data = {"a": "${REGION}", "b": "${MISSING:-x}", "c": "${EMPTY:-fallback}", "d": "${EMPTY-kept}",
            "e": ["arn:${REGION}:1", {"f": "$${LITERAL}"}], "g": 3}
    out = interpolate_env(data, env)
    assert out == {"a": "eu-west-1", "b": "x", "c": "fallback", "d": "", "e": ["arn:eu-west-1:1", {"f": "${LITERAL}"}], "g": 3}


def test_missing_env_names_every_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("GE_TEST_MODEL", raising=False)
    monkeypatch.delenv("GE_TEST_REGION", raising=False)
    p = tmp_path / "providers.yaml"
    p.write_text('profiles:\n  b: {provider: bedrock, model: "${GE_TEST_MODEL}", region: "${GE_TEST_REGION}"}\n')
    with pytest.raises(MissingEnvError, match="GE_TEST_MODEL, GE_TEST_REGION"):
        load_providers(p)
    monkeypatch.setenv("GE_TEST_MODEL", "m")
    monkeypatch.setenv("GE_TEST_REGION", "r")
    prof = load_providers(p).profiles["b"]
    assert (prof.model, prof.region) == ("m", "r")


def test_env_file_does_not_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GE_KEEP", "shell")
    monkeypatch.delenv("GE_NEW", raising=False)
    f = tmp_path / ".env"
    f.write_text("# comment\nexport GE_NEW='from file'\nGE_KEEP=file  # trailing\n")
    assert load_env_file(f) == ["GE_NEW"]
    import os
    assert os.environ["GE_NEW"] == "from file" and os.environ["GE_KEEP"] == "shell"
    monkeypatch.delenv("GE_NEW")


def test_init_scaffold_runs_offline_and_validates_live(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for k in ("GENAI_EVALS_PROVIDERS", "EVAL_TARGET_PROFILE", "EVAL_JUDGE_A", "EVAL_JUDGE_B"):
        monkeypatch.delenv(k, raising=False)
    assert main(["init", "evals"]) == 0
    root = tmp_path / "evals"
    for f in ("suite.yaml", "providers.yaml", "providers.live.yaml", "sources.yaml", "online_checks.yaml",
              ".env.example", ".gitignore", "prompts.lock", "data/cases.jsonl", "prompts/assistant/1.0.0/prompt.yaml"):
        assert (root / f).exists(), f
    monkeypatch.chdir(root)
    assert main(["prompts", "check"]) == 0
    assert main(["run", "suite.yaml", "--no-html", "--out", "out"]) == 0          # offline mocks pass every gate
    # live providers: unset variables are reported, then validation passes once they are set
    assert main(["validate", "suite.yaml", "--providers", "providers.live.yaml"]) == 2
    assert "environment variable(s) not set" in capsys.readouterr().err
    env = {"OPENAI_MODEL": "m", "JUDGE_OPENAI_MODEL": "m", "ANTHROPIC_MODEL": "m", "AWS_REGION": "r",
           "BEDROCK_MODEL_ID": "m", "GCP_PROJECT": "p", "GCP_LOCATION": "l", "VERTEX_MODEL": "m",
           "JUDGE_VERTEX_MODEL": "m", "EVAL_TARGET_PROFILE": "bedrock", "EVAL_JUDGE_A": "judge-openai",
           "EVAL_JUDGE_B": "judge-gemini"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert main(["validate", "suite.yaml", "--providers", "providers.live.yaml"]) == 0
    assert main(["check-config", "providers.live.yaml"]) == 0
    # a second init keeps existing files
    monkeypatch.chdir(tmp_path)
    assert main(["init", "evals"]) == 0
    assert "kept" in capsys.readouterr().out


def test_repo_ships_no_environment_specific_values():
    """Account ids, real-looking project ids and fixed regions must not appear in shipped configs."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("examples/**/*.yaml"), *root.glob("src/genai_evals/templates/**/*.yaml"), *root.glob("terraform/**/*.tf")]
    bad = re.compile(r"\b\d{12}\b|default\s*=\s*\"(ap|us|eu|asia|europe)-[a-z]+-?\d|my-gcp-project")
    hits = [f"{f.relative_to(root)}: {m.group(0)}" for f in files for m in bad.finditer(f.read_text())]
    assert not hits, hits
