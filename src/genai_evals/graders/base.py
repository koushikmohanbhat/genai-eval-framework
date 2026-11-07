"""Grader base class, registry and shared helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import GraderSpec, SuiteSpec
from ..models import Case, Sample, Score

REGISTRY: dict[str, type[Grader]] = {}


def register(name: str) -> Callable[[type[Grader]], type[Grader]]:
    def deco(cls: type[Grader]) -> type[Grader]:
        REGISTRY[name] = cls
        cls.type_name = name
        return cls
    return deco


@dataclass
class GradeContext:
    suite: SuiteSpec
    pool: Any = None                                            # ProviderPool
    providers: Any = None                                       # ProvidersConfig
    target_families: dict[str, str | None] = field(default_factory=dict)
    samples: dict[tuple[str, str, int], Sample] = field(default_factory=dict)   # (target, case_id, repeat) -> sample
    tokenize: Callable[[str], str] = lambda s: s
    calibration: dict[str, Any] = field(default_factory=dict)


class Grader:
    type_name = "base"
    uses_model = False

    def __init__(self, spec: GraderSpec, ctx: GradeContext):
        self.spec = spec
        self.ctx = ctx
        self.p = spec.params()

    @property
    def name(self) -> str:
        return self.spec.name

    def applies(self, case: Case) -> bool:
        return not self.spec.tags or any(t in case.tags for t in self.spec.tags)

    async def grade(self, sample: Sample, case: Case) -> Score:  # pragma: no cover
        raise NotImplementedError

    # ---- helpers -----------------------------------------------------------
    def score(self, sample: Sample, value: float | None, passed: bool | None = None, **kw) -> Score:
        if value is not None:
            value = max(0.0, min(1.0, float(value)))
            if passed is None:
                thr = self.spec.threshold if self.spec.threshold is not None else self.default_threshold()
                passed = value >= thr
        return Score(grader=self.name, case_id=sample.case_id, target=sample.target, repeat=sample.repeat,
                     value=value, passed=passed, **kw)

    def skip(self, sample: Sample, reason: str) -> Score:
        return Score(grader=self.name, case_id=sample.case_id, target=sample.target, repeat=sample.repeat, skipped=True, skip_reason=reason)

    def default_threshold(self) -> float:
        return 1.0

    def output_value(self, sample: Sample) -> Any:
        path = self.p.get("output_path")
        if path:
            return dig(sample.output_json, path)
        return sample.output

    def reference_value(self, case: Case, default_key: str = "answer") -> Any:
        if "value" in self.p:
            return self.p["value"]
        key = self.p.get("reference_key", default_key)
        return dig(case.reference, key)


def dig(obj: Any, path: str | None) -> Any:
    if path is None or obj is None:
        return obj
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def build_grader(spec: GraderSpec, ctx: GradeContext) -> Grader:
    if spec.type not in REGISTRY:
        from ..plugins import info, load
        if info("grader", spec.type) is None:
            raise ValueError(f"unknown grader type '{spec.type}' (available: {sorted(REGISTRY)})")
        REGISTRY[spec.type] = load("grader", spec.type)       # plugin grader
    return REGISTRY[spec.type](spec, ctx)
