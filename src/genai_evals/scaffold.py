"""`genai-evals init`: copy a starter evals project into the partner's or customer's own repo.

The framework repo ships no environment-specific settings. Everything a deployment needs (regions, projects,
model ids, ARNs, log groups, keys) is supplied by the user through environment variables or their own files.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from .prompts import PromptStore

# template file name -> name in the generated project (dotfiles are stored without the dot so packaging keeps them)
RENAMES = {"env.example": ".env.example", "gitignore": ".gitignore"}


def _walk(node, rel: Path):
    for child in node.iterdir():
        if child.name == "__pycache__":
            continue
        if child.is_dir():
            yield from _walk(child, rel / child.name)
        else:
            yield rel / child.name, child


def scaffold(target: Path, force: bool = False) -> tuple[list[str], list[str]]:
    root = resources.files("genai_evals") / "templates" / "starter"
    target.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for rel, src in sorted(_walk(root, Path("")), key=lambda x: str(x[0])):
        out_rel = rel.with_name(RENAMES.get(rel.name, rel.name))
        dst = target / out_rel
        if dst.exists() and not force:
            skipped.append(str(out_rel))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        written.append(str(out_rel))
    store = PromptStore(target / "prompts")
    lock_existed = store.lock_path.exists()
    store.lock()
    for pid in store.ids():
        if not store.read_lock()["prompts"][pid]["stages"].get("prod"):
            store.promote(pid, store.versions(pid)[-1], "prod")
    if not lock_existed:
        written.append("prompts.lock")
    return written, skipped
