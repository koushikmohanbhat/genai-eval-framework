"""Configuration: provider profiles, eval suites and telemetry sources (all YAML)."""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Case

PROVIDERS = {"openai", "openai_compatible", "anthropic", "bedrock", "vertex", "vertex_anthropic", "bedrock_anthropic", "mock"}

# provider -> model family (for "judge must be a different family than the target")
FAMILY = {
    "openai": "openai", "openai_compatible": "other", "anthropic": "anthropic", "vertex_anthropic": "anthropic",
    "bedrock_anthropic": "anthropic", "vertex": "google", "mock": "mock",
}


class Pricing(BaseModel):
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cached_input_per_mtok: float | None = None


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    provider: str
    model: str
    region: str | None = None             # AWS
    project: str | None = None            # GCP
    location: str | None = None           # GCP
    base_url: str | None = None           # openai_compatible
    auth: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    pricing: Pricing | None = None
    max_concurrency: int = 8
    rpm: int | None = None
    retries: int = 3
    timeout_s: float = 120.0
    family: str | None = None             # override model family (e.g. Claude or Llama on Bedrock)

    @model_validator(mode="after")
    def _check(self):
        from .plugins import names
        known = set(names("provider")) | PROVIDERS
        if self.provider not in known:
            raise ValueError(f"unknown provider '{self.provider}' (expected one of {sorted(known)})")
        for k, v in self.auth.items():
            if k in {"api_key", "secret", "password", "token"} and isinstance(v, str) and v:
                raise ValueError(f"profile '{self.name}': put secrets in env vars or a secret manager "
                                 f"(auth.api_key_env / auth.api_key_secret), not inline '{k}'")
        return self

    @property
    def model_family(self) -> str:
        if self.family:
            return self.family
        if self.provider == "bedrock":
            m = self.model.lower()
            for key, fam in (("anthropic", "anthropic"), ("claude", "anthropic"), ("meta", "meta"), ("llama", "meta"),
                             ("nova", "amazon"), ("titan", "amazon"), ("mistral", "mistral"), ("openai", "openai"),
                             ("gpt-oss", "openai"), ("cohere", "cohere"), ("deepseek", "deepseek")):
                if key in m:
                    return fam
            return "bedrock"
        return FAMILY.get(self.provider, self.provider)


class ProvidersConfig(BaseModel):
    profiles: dict[str, ProviderProfile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _names(self):
        for k, p in self.profiles.items():
            p.name = k
        return self

    def get(self, name: str) -> ProviderProfile:
        if name not in self.profiles:
            raise KeyError(f"provider profile '{name}' not found (have: {sorted(self.profiles)})")
        return self.profiles[name]


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    kind: Literal["model", "prompt", "rag", "agent", "http", "python"] = "model"
    profile: str | None = None
    prompt: str | None = None                       # "claims_agent" (uses stage) or "claims_agent@1.4.0"
    prompt_stage: str = "prod"
    system: str | None = None                       # inline system prompt (when not using prompts/)
    template: str | None = None                     # inline user template
    framework: str | None = None                    # agent: tool_loop | openai_agents_sdk | agentcore_runtime | bedrock_agent | agent_engine
    tools_module: str | None = None                 # python module with tool implementations (tool_loop)
    callable: str | None = None                     # python: "module:function"
    retriever: dict[str, Any] | None = None         # rag: {type: python|bedrock_kb|vertex_search, ...}
    max_steps: int = 12
    output: Literal["text", "json"] = "text"
    options: dict[str, Any] = Field(default_factory=dict)


class GraderSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    weight: float = 1.0
    threshold: float | None = None                  # value >= threshold -> passed
    tags: list[str] | None = None                   # grade only cases carrying any of these tags
    judges: list[str] | None = None                 # override suite judges for this grader

    def params(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if k not in {"name", "type", "weight", "threshold", "tags", "judges"}}


class GateSpec(BaseModel):
    name: str
    rule: str
    severity: Literal["block", "warn"] = "block"
    targets: list[str] | None = None
    min_n: int | None = None                    # overrides suite.min_n for this gate
    allow_missing_baseline: bool = False        # delta()/significant() pass (as 0 / False) when there is no baseline


class SuiteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    dataset: str | list[dict[str, Any]] | None = None
    targets: list[TargetSpec] = Field(default_factory=list)
    graders: list[GraderSpec] = Field(default_factory=list)
    gates: list[GateSpec] = Field(default_factory=list)
    judges: list[str] = Field(default_factory=list)
    judge_policy: dict[str, Any] = Field(default_factory=dict)   # require_different_family, aggregate, min_agreement
    simulator: str | None = None
    embeddings: str | None = None
    repeats: int = 1
    concurrency: int = 8
    seed: int = 7
    prompts_dir: str | None = None
    calibration: str | None = None                   # path to calibration results (judge agreement)
    min_judge_kappa: float | None = None
    tokenize: bool = True                            # DPDP: tokenise inputs/outputs before judges + storage
    on_error: Literal["fail", "exclude"] = "fail"    # grader / target errors count as failed scores (value 0)
    min_n: int = 5                                   # fewest cases behind any gated metric or slice
    min_calibration_n: int = 30                      # fewest human labels before a calibration result is trusted
    tokenizer: dict[str, Any] = Field(default_factory=dict)
    validation: str | None = None                    # adapter validation records (default: validation.yaml next to the suite)
    overlays: list[str] = Field(default_factory=list)  # pack overlays (folders with overlay.yaml): extra cases, graders, gates
    pack: str | None = None                          # the eval pack this suite came from (informational)
    targets_from: str | None = None                  # YAML file with `targets:` (lets a pack switch systems by env var)
    dataset_schema: str | None = None                # JSON Schema every case must satisfy (checked when cases load)
    extra_datasets: list[str] = Field(default_factory=list)   # more case files (overlays add theirs here)
    report: dict[str, Any] = Field(default_factory=dict)      # report template: title, intro, headline, failure_categories
    base_dir: str = "."

    @model_validator(mode="after")
    def _check(self):
        names = [t.name for t in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("target names must be unique")
        gnames = [g.name for g in self.graders]
        if len(gnames) != len(set(gnames)):
            raise ValueError("grader names must be unique")
        return self

    def path(self, p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else Path(self.base_dir) / pp


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-)([^}]*))?\}")


class MissingEnvError(ValueError):
    pass


def interpolate_env(obj: Any, env: dict[str, str] | None = None, missing: set[str] | None = None) -> Any:
    """Replace ``${VAR}``, ``${VAR:-default}`` and ``${VAR-default}`` in string values.

    Settings that differ per partner or customer (regions, projects, ARNs, model ids, log groups) are
    read from the environment, so the YAML in a repo never holds them. ``$${VAR}`` escapes a literal.
    """
    env = os.environ if env is None else env
    if isinstance(obj, str):
        def sub(m: re.Match) -> str:
            name, op, default = m.group(1), m.group(2), m.group(3)
            val = env.get(name)
            if val is None or (op == ":-" and val == ""):
                if op is not None:
                    return default
                if missing is not None:
                    missing.add(name)
                return m.group(0)
            return val
        out = _ENV_REF.sub(sub, obj.replace("$${", "\x00"))
        return out.replace("\x00", "${")
    if isinstance(obj, list):
        return [interpolate_env(x, env, missing) for x in obj]
    if isinstance(obj, dict):
        return {k: interpolate_env(v, env, missing) for k, v in obj.items()}
    return obj


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    missing: set[str] = set()
    data = interpolate_env(data, missing=missing)
    if missing:
        raise MissingEnvError(
            f"{path}: environment variable(s) not set: {', '.join(sorted(missing))}. "
            "Set them in your shell or a .env file (see .env.example), or give a default with ${VAR:-value}."
        )
    return data


def load_env_file(path: str | Path, override: bool = False) -> list[str]:
    """Minimal ``.env`` loader (KEY=VALUE, ``#`` comments, optional quotes). Returns the keys it set."""
    set_keys = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if override or key not in os.environ:
            os.environ[key] = val
            set_keys.append(key)
    return set_keys


def load_providers(path: str | Path | None) -> ProvidersConfig:
    if not path:
        return ProvidersConfig()
    data = _load_yaml(path)
    return ProvidersConfig(profiles=data.get("profiles", {}))


def load_suite(path: str | Path) -> SuiteSpec:
    data = _load_yaml(path)
    data.setdefault("base_dir", str(Path(path).parent))
    if data.get("targets_from"):
        # the system under test lives in its own file, chosen per environment, e.g. targets/offline.yaml vs
        # targets/bedrock-kb.yaml: `targets_from: "${EVAL_TARGETS:-targets/offline.yaml}"`
        tf = Path(data["targets_from"])
        tf = tf if tf.is_absolute() else Path(data["base_dir"]) / tf
        if not tf.is_file():
            raise FileNotFoundError(f"targets_from: {tf} not found")
        data["targets"] = list(data.get("targets") or []) + list(_load_yaml(tf).get("targets") or [])
    suite = SuiteSpec(**data)
    return apply_overlays(suite) if suite.overlays else suite


def apply_overlays(suite: SuiteSpec) -> SuiteSpec:
    """Merge pack overlays (e.g. an industry overlay): each folder has overlay.yaml with optional `cases` (files,
    relative to the overlay), `graders`, `gates` and `failure_categories`. Overlays add; they never replace."""
    graders, gates, extra = list(suite.graders), list(suite.gates), list(suite.extra_datasets)
    report = dict(suite.report)
    for o in suite.overlays:
        d = suite.path(o)
        spec = _load_yaml(d / "overlay.yaml")
        extra += [str(Path(o) / c) for c in spec.get("cases", [])]
        graders += [GraderSpec(**g) for g in spec.get("graders", [])]
        gates += [GateSpec(**g) for g in spec.get("gates", [])]
        if spec.get("failure_categories"):
            fc = {k: list(v) for k, v in (report.get("failure_categories") or {}).items()}
            for k, v in spec["failure_categories"].items():
                fc[k] = fc.get(k, []) + [g for g in v if g not in fc.get(k, [])]
            report["failure_categories"] = fc
    out = suite.model_copy(update={"graders": graders, "gates": gates, "extra_datasets": extra, "report": report})
    SuiteSpec.model_validate(out.model_dump())   # duplicate grader names from an overlay fail here
    return out


def load_cases(suite: SuiteSpec) -> list[Case]:
    rows: list[dict[str, Any]] = []
    if isinstance(suite.dataset, list):
        rows = list(suite.dataset)
    elif suite.dataset is not None:
        rows = _read_rows(suite.path(suite.dataset))
    for extra in suite.extra_datasets:
        rows += _read_rows(suite.path(extra))
    if suite.dataset_schema:
        check_rows(rows, suite.path(suite.dataset_schema))
    cases = [Case(**r) for r in rows]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"case ids must be unique (duplicated: {', '.join(dup[:5])})")
    return cases


class DatasetError(ValueError):
    pass


def check_rows(rows: list[dict[str, Any]], schema_path: Path) -> None:
    """Validate dataset rows against the pack's case schema; report the row id and the field, not a stack trace."""
    import jsonschema
    v = jsonschema.Draft202012Validator(json.loads(Path(schema_path).read_text(encoding="utf-8")))
    probs = []
    for i, r in enumerate(rows):
        for e in v.iter_errors(r):
            where = "/".join(str(x) for x in e.absolute_path) or "(row)"
            probs.append(f"case {r.get('id', f'#{i + 1}')}: {where}: {e.message}")
    if probs:
        raise DatasetError(f"{len(probs)} dataset row problem(s) against {schema_path.name}:\n  - " + "\n  - ".join(probs[:20]))


def _read_rows(p: Path) -> list[dict[str, Any]]:
    if p.suffix in {".jsonl", ".ndjson"}:
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif p.suffix == ".json":
        rows = json.loads(p.read_text(encoding="utf-8"))
    elif p.suffix == ".csv":
        with open(p, encoding="utf-8", newline="") as f:
            rows = []
            for r in csv.DictReader(f):
                row: dict[str, Any] = {"id": r.pop("id"), "input": r.pop("input", "")}
                row["tags"] = [t for t in (r.pop("tags", "") or "").split("|") if t]
                row["critical"] = (r.pop("critical", "") or "").lower() in {"1", "true", "yes"}
                row["reference"] = {k.removeprefix("ref_"): v for k, v in r.items() if k.startswith("ref_")}
                rows.append(row)
    elif p.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("install pyarrow to read parquet datasets") from e
        rows = pq.read_table(p).to_pylist()
    else:
        raise ValueError(f"unsupported dataset format: {p.suffix}")
    return rows


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    fixture: str | None = None                       # read recorded records instead of calling the cloud API
    window: dict[str, Any] = Field(default_factory=lambda: {"last": "24h"})
    sampling: float = 1.0
    filter: dict[str, Any] = Field(default_factory=dict)
    join: list[dict[str, Any]] = Field(default_factory=list)
    target: str | None = None                        # label used in reports (defaults to source name)


class SourcesConfig(BaseModel):
    sources: list[SourceSpec]
    base_dir: str = "."


def load_sources(path: str | Path) -> SourcesConfig:
    data = _load_yaml(path)
    return SourcesConfig(sources=data.get("sources", []), base_dir=str(Path(path).parent))


MODEL_GRADERS = {"rubric", "pairwise", "reference_match", "policy", "faithfulness", "answer_relevance"}


def check_suite(suite: SuiteSpec, providers: ProvidersConfig) -> list[str]:
    """Static checks run by `genai-evals validate` and before every run. Returns a list of problems."""
    problems: list[str] = []
    uses_judges = any(g.type in MODEL_GRADERS or (g.type == "correct_refusal" and g.params().get("use_judge"))
                      or (g.type == "refusal_appropriate" and g.params().get("use_judge")) for g in suite.graders)
    if uses_judges and not (suite.judges or all(g.judges for g in suite.graders if g.type in MODEL_GRADERS)):
        problems.append("suite uses model graders but names no judges: set `judges:` explicitly "
                        "(there is no default judge; a two-provider panel is recommended)")
    for j in suite.judges + [j for g in suite.graders for j in (g.judges or [])]:
        if j not in providers.profiles:
            problems.append(f"judge profile '{j}' not in providers.yaml")
    for t in suite.targets:
        if t.kind in {"model", "prompt", "rag"} or (t.kind == "agent" and (t.framework or "tool_loop") == "tool_loop"):
            if not t.profile:
                problems.append(f"target '{t.name}' needs a provider profile")
            elif t.profile not in providers.profiles:
                problems.append(f"target '{t.name}': profile '{t.profile}' not in providers.yaml")
        if t.kind == "python" and not t.callable:
            problems.append(f"target '{t.name}' (python) needs `callable: module:function`")
    if suite.simulator and suite.simulator not in providers.profiles:
        problems.append(f"simulator profile '{suite.simulator}' not in providers.yaml")
    if suite.embeddings and suite.embeddings not in providers.profiles:
        problems.append(f"embeddings profile '{suite.embeddings}' not in providers.yaml")
    from .gates import validate_rule
    gnames = {g.name for g in suite.graders}
    for g in suite.gates:
        problems += [f"gate '{g.name}': {p}" for p in validate_rule(g.rule, gnames)]
    return problems
