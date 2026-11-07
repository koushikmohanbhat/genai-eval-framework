"""Results store (SQLAlchemy): SQLite locally, Postgres / Aurora / Cloud SQL / AlloyDB for teams."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, insert, select

meta = MetaData()
runs = Table("runs", meta,
             Column("run_id", String(64), primary_key=True), Column("suite", String(200), index=True),
             Column("created_at", DateTime(timezone=True)), Column("git_sha", String(64)), Column("passed", Integer),
             Column("summary", JSON), Column("gates", JSON), Column("meta", JSON))
samples_t = Table("samples", meta,
                  Column("id", Integer, primary_key=True, autoincrement=True), Column("run_id", String(64), index=True),
                  Column("target", String(200), index=True), Column("case_id", String(200), index=True), Column("repeat", Integer),
                  Column("prompt_versions", JSON), Column("latency_ms", Float), Column("cost_usd", Float), Column("data", Text))
scores_t = Table("scores", meta,
                 Column("id", Integer, primary_key=True, autoincrement=True), Column("run_id", String(64), index=True),
                 Column("target", String(200), index=True), Column("case_id", String(200)), Column("repeat", Integer),
                 Column("grader", String(200), index=True), Column("value", Float), Column("passed", Integer), Column("data", Text))


class ResultStore:
    """SQL store (SQLAlchemy URL). Implements the `plugins.Store` contract."""

    def __init__(self, url: str = "sqlite:///.genai-evals/results.db"):
        if url.startswith("sqlite:///"):
            from pathlib import Path
            p = Path(url.removeprefix("sqlite:///"))
            p.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, future=True)
        meta.create_all(self.engine)

    def save(self, result: dict[str, Any]) -> None:
        with self.engine.begin() as c:
            c.execute(insert(runs).values(run_id=result["run_id"], suite=result["suite"], created_at=datetime.fromisoformat(result["created_at"]),
                                          git_sha=result.get("git_sha"), passed=int(result["passed"]), summary=result["summary"],
                                          gates=result["gates"], meta={k: result.get(k) for k in ("prompt_versions", "targets", "baseline")}))
            if result["samples"]:
                c.execute(insert(samples_t), [{"run_id": result["run_id"], "target": s["target"], "case_id": s["case_id"], "repeat": s["repeat"],
                                               "prompt_versions": s.get("prompt_versions"), "latency_ms": s.get("latency_ms"),
                                               "cost_usd": s.get("cost_usd"), "data": json.dumps(s, default=str)} for s in result["samples"]])
            if result["scores"]:
                c.execute(insert(scores_t), [{"run_id": result["run_id"], "target": s["target"], "case_id": s["case_id"], "repeat": s["repeat"],
                                              "grader": s["grader"], "value": s.get("value"),
                                              "passed": None if s.get("passed") is None else int(s["passed"]),
                                              "data": json.dumps(s, default=str)} for s in result["scores"]])

    def history(self, suite: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(runs).where(runs.c.suite == suite).order_by(runs.c.created_at.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()


class FileStore:
    """A folder of result JSON files plus an index: no database, readable by any tool. URL: file://<folder>."""

    def __init__(self, url: str):
        from pathlib import Path
        self.root = Path(url.removeprefix("file://"))
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, result: dict[str, Any]) -> None:
        (self.root / f"{result['run_id']}.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        row = {k: result.get(k) for k in ("run_id", "suite", "created_at", "git_sha", "passed", "schema_version")}
        with (self.root / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def history(self, suite: str, limit: int = 20) -> list[dict[str, Any]]:
        idx = self.root / "index.jsonl"
        rows = [json.loads(line) for line in idx.read_text(encoding="utf-8").splitlines() if line.strip()] if idx.exists() else []
        return sorted((r for r in rows if r["suite"] == suite), key=lambda r: r["created_at"], reverse=True)[:limit]


def store_for(url: str):
    """Pick a store by URL scheme: sqlite / postgresql+... (SQL), file:// (folder), or a `genai_evals.stores` plugin."""
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    if scheme == "file":
        return FileStore(url)
    if scheme in ("sqlite", "postgresql", "postgres", "mysql"):
        return ResultStore(url)
    from .plugins import load
    return load("store", scheme)(url)
