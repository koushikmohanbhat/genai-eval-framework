"""Environment interpolation and .env loading."""
import pytest

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


def test_repo_ships_no_environment_specific_values():
    """Account ids, real-looking project ids and fixed regions must not appear in shipped configs."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = [*root.glob("examples/**/*.yaml"), *root.glob("packs/**/*.yaml"), *root.glob("terraform/**/*.tf")]
    bad = re.compile(r"\b\d{12}\b|default\s*=\s*\"(ap|us|eu|asia|europe)-[a-z]+-?\d|my-gcp-project")
    hits = [f"{f.relative_to(root)}: {m.group(0)}" for f in files for m in bad.finditer(f.read_text())]
    assert not hits, hits
