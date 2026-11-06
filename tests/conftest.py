import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples" / "insurance"
sys.path.insert(0, str(EX))
os.environ.setdefault("GENAI_EVALS_TOKEN_KEY", "test-key")


@pytest.fixture
def ex_dir():
    return EX


def write_yaml(path: Path, data) -> Path:
    import yaml
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


MOCK_PROVIDERS = {"profiles": {
    "judge-a": {"provider": "mock", "model": "j-a", "family": "openai", "options": {"mode": "judge"}},
    "judge-b": {"provider": "mock", "model": "j-b", "family": "google", "options": {"mode": "judge", "strictness": 1.25}},
    "echo": {"provider": "mock", "model": "echo", "pricing": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
    "emb": {"provider": "mock", "model": "emb"},
}}
