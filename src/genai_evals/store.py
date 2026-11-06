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
