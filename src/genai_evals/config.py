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
    # a prompt managed outside this repo (Langfuse, Bedrock Prompt Management, Vertex): recorded on every sample and
    # in results so gates, comparisons and online scores are tied to it, without prompts/ or prompts.lock.
    # "langfuse:claims_agent@7", "bedrock:arn:aws:bedrock:...:prompt/ABC:3", or a map {id: version}
    prompt_version: str | dict[str, str] | None = None
    system: str | None = None                       # inline system prompt (when not using prompts/)
    template: str | None = None                     # inline user template
    framework: str | None = None                    # agent: tool_loop | openai_agents_sdk | agentcore_runtime | bedrock_agent | agent_engine
    tools_module: str | None = None                 # python module with tool implementations (tool_loop)
    callable: str | None = None                     # python: "module:function"
    retriever: dict[str, Any] | None = None         # rag: {type: python|bedrock_kb|vertex_search, ...}
    max_steps: int = 12
    output: Literal["text", "json"] = "text"
    options: dict[str, Any] = Field(default_factory=dict)
    reset: dict[str, Any] | None = None             # per-trial reset hook: {python: "mod:fn"} or {url: ..., body: ...}


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
    severity: Literal["block", "warn", "info"] = "block"   # info: diagnostic, reported but never fails a run
    targets: list[str] | None = None
    min_n: int | None = None                    # overrides suite.min_n for this gate
    allow_missing_baseline: bool = False        # delta()/significant() pass (as 0 / False) when there is no baseline


class RepresentativeSample(BaseModel):
    """How the representative set was drawn, so "representative" is a claim with evidence behind it."""
    model_config = ConfigDict(extra="allow")
    source: str                                  # production | pilot | historical logs | ...
    population: str                              # what was sampled from, e.g. "all web-chat claim sessions"
    window: str                                  # when, e.g. "2026-10-01..2026-10-31"
    method: str                                  # how, e.g. "simple random sample of sessions", "stratified by intent"
    weights: str | None = None                   # how case weights were set, if any (e.g. "intent share in October")
    drawn_by: str | None = None
    drawn_at: str | None = None
    notes: str | None = None


class CaseFilter(BaseModel):
    """Which cases a run uses (set by a profile). Critical cases are always kept."""
    model_config = ConfigDict(extra="forbid")
    splits: list[Literal["representative", "stress"]] | None = None
    tags: list[str] | None = None              # keep cases with any of these tags
    exclude_tags: list[str] = Field(default_factory=list)
    max_cases: int | None = None               # deterministic sample (by suite seed) of the non-critical cases


def parse_prompt_version(v: str | dict[str, str] | None) -> dict[str, str]:
    """{prompt id: version} from a target's prompt_version: "registry:id@version" splits at the last @."""
    if not v:
        return {}
    if isinstance(v, dict):
        return {str(k): str(x) for k, x in v.items()}
    s = str(v).strip()
    if "@" in s:
        pid, ver = s.rsplit("@", 1)
        return {pid: ver}
    if s.count(":") >= 1 and s.rsplit(":", 1)[1].isdigit():      # Bedrock prompt ARN with a :version suffix
        pid, ver = s.rsplit(":", 1)
        return {pid: ver}
    return {s: "unversioned"}


class ProfileSpec(BaseModel):
    """A named way to run the same suite, e.g. a fast `pr-check` and a thorough `release-gate`. Fields left out keep
    the suite's value. Gates that read a grader the profile drops, or a split it leaves out, are skipped and listed
    in the result (`profile.skipped_gates`), never silently passed."""
    model_config = ConfigDict(extra="forbid")
    description: str = ""
    repeats: int | None = None
    judges: list[str] | None = None            # replaces the suite's panel and any per-grader panel
    graders: list[str] | None = None           # run only these graders
    skip_graders: list[str] = Field(default_factory=list)
    splits: list[Literal["representative", "stress"]] | None = None
    tags: list[str] | None = None
    exclude_tags: list[str] = Field(default_factory=list)
    max_cases: int | None = None
    gates: list[str] | None = None             # keep only these gates (by name)
    add_gates: list[GateSpec] = Field(default_factory=list)   # gates only this profile runs (e.g. a cost budget)
    gate_severity: Literal["warn", "info"] | None = None      # downgrade every gate: results inform, never block
    #: a proof-of-concept run: reports say judge-graded numbers are uncalibrated and directional, not sign-off
    directional: bool = False
    overrides: dict[str, Any] = Field(default_factory=dict)   # any other suite field: min_n, concurrency, ...


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
    include_draft_cases: bool = False                # draft cases (review_status: draft) are left out of runs and gates
    stress_tags: list[str] = Field(default_factory=lambda: ["edge", "adversarial"])  # cases with these tags form the stress split
    min_representative_cases: int = 30              # doctor: size of the representative (production-like) set
    min_representative_production_share: float = 0.8  # doctor: share of representative cases with source: production
    representative_sample: RepresentativeSample | None = None   # provenance of the representative set
    stress_min_cases: dict[str, int] = Field(default_factory=lambda: {"high": 10, "standard": 5})  # per coverage tag, by risk tier
    stress_min_by_tag: dict[str, int] = Field(default_factory=dict)  # per-tag overrides
    tokenizer: dict[str, Any] = Field(default_factory=dict)
    # identifier detectors for tokenisation and pii_leak (genai_evals.pii): {sets: [global, in, pci, us_hipaa, uk,
    # banking, ...], custom: [{name, pattern, validator, context}]}; overlays add to it
    pii: dict[str, Any] = Field(default_factory=dict)
    validation: str | None = None                    # adapter validation records (default: validation.yaml next to the suite)
    overlays: list[str] = Field(default_factory=list)  # pack overlays (folders with overlay.yaml): extra cases, graders, gates
    pack: str | None = None                          # the eval pack this suite came from (informational)
    targets_from: str | None = None                  # YAML file with `targets:` (lets a pack switch systems by env var)
    dataset_schema: str | None = None                # JSON Schema every case must satisfy (checked when cases load)
    gate_correction: Literal["none", "holm"] = "none"   # multiple-comparison correction for significant() across gates
    security_review: bool = False                    # require the security tags (indirect_injection, prompt_leak, ...)
    safety_claim: bool = False                       # results will back a safety claim: coverage requires ~100 multi-turn
                                                     # or ~1,000 single-turn cases (Anthropic's guidance)
    extra_datasets: list[str] = Field(default_factory=list)   # more case files (overlays add theirs here)
    report: dict[str, Any] = Field(default_factory=dict)      # report template: title, intro, headline, failure_categories
    profiles: dict[str, ProfileSpec] = Field(default_factory=dict)   # named run profiles (pr-check, release-gate)
    case_filter: CaseFilter | None = None            # set by the active profile
    active_profile: dict[str, Any] | None = None     # the profile this suite was loaded with, recorded in results
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


def load_suite(path: str | Path, profile: str | None = None) -> SuiteSpec:
    """Load a suite. `profile` (or GENAI_EVALS_PROFILE) applies one of the suite's `profiles:`."""
    suite = _load_suite(path)
    name = profile if profile is not None else os.environ.get("GENAI_EVALS_PROFILE") or None
    return apply_profile(suite, name) if name else suite


def apply_profile(suite: SuiteSpec, name: str) -> SuiteSpec:
    from .gates import gated_graders, split_args
    if name not in suite.profiles:
        raise ValueError(f"profile {name!r} not in the suite (has: {', '.join(sorted(suite.profiles)) or 'none'})")
    p = suite.profiles[name]
    upd: dict[str, Any] = dict(p.overrides)
    graders = list(suite.graders)
    if p.graders is not None:
        unknown = set(p.graders) - {g.name for g in graders}
        if unknown:
            raise ValueError(f"profile {name!r}: unknown graders {sorted(unknown)}")
        graders = [g for g in graders if g.name in p.graders]
    graders = [g for g in graders if g.name not in set(p.skip_graders)]
    if p.judges is not None:
        upd["judges"] = list(p.judges)
        graders = [g.model_copy(update={"judges": None}) if g.judges else g for g in graders]
    kept = {g.name for g in graders}
    gates, skipped = [], []
    for g in suite.gates:
        reads = gated_graders([g], ("block", "warn", "info")) & {x.name for x in suite.graders}
        why = None
        if p.gates is not None and g.name not in p.gates:
            why = "not in this profile"
        elif reads - kept:
            why = f"reads {', '.join(sorted(reads - kept))}, not run in this profile"
        elif p.splits is not None and split_args(g.rule) - set(p.splits):
            why = f"reads the {', '.join(sorted(split_args(g.rule) - set(p.splits)))} split, left out of this profile"
        if why:
            skipped.append({"gate": g.name, "severity": g.severity, "reason": why})
        else:
            gates.append(g)
    gates += list(p.add_gates)
    if p.gate_severity:
        gates = [g.model_copy(update={"severity": p.gate_severity}) for g in gates]
    upd.update(graders=graders, gates=gates)
    if p.repeats is not None:
        upd["repeats"] = p.repeats
    if any(v not in (None, []) for v in (p.splits, p.tags, p.exclude_tags, p.max_cases)):
        upd["case_filter"] = CaseFilter(splits=p.splits, tags=p.tags, exclude_tags=p.exclude_tags, max_cases=p.max_cases)
    upd["active_profile"] = {"name": name, "description": p.description, "repeats": upd.get("repeats", suite.repeats),
                             "judges": upd.get("judges", suite.judges), "graders": sorted(kept),
                             "case_filter": upd["case_filter"].model_dump() if "case_filter" in upd else None,
                             "skipped_gates": skipped, "directional": p.directional,
                             "gate_severity": p.gate_severity}
    out = suite.model_copy(update=upd)
    SuiteSpec.model_validate(out.model_dump())
    return out


def _load_suite(path: str | Path) -> SuiteSpec:
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
    pii = {"sets": list((suite.pii or {}).get("sets") or []), "custom": list((suite.pii or {}).get("custom") or [])}
    for o in suite.overlays:
        d = suite.path(o)
        spec = _load_yaml(d / "overlay.yaml")
        op = spec.get("pii") or {}
        if op:
            from .pii import DEFAULT_SETS
            base_sets = pii["sets"] or list(DEFAULT_SETS)      # an overlay adds to the defaults, never narrows them
            pii["sets"] = base_sets + [s for s in op.get("sets", []) if s not in base_sets]
            names = {c["name"] for c in pii["custom"]}
            pii["custom"] += [c for c in op.get("custom", []) if c["name"] not in names]
        extra += [str(Path(o) / c) for c in spec.get("cases", [])]
        graders += [GraderSpec(**g) for g in spec.get("graders", [])]
        gates += [GateSpec(**g) for g in spec.get("gates", [])]
        if spec.get("failure_categories"):
            fc = {k: list(v) for k, v in (report.get("failure_categories") or {}).items()}
            for k, v in spec["failure_categories"].items():
                fc[k] = fc.get(k, []) + [g for g in v if g not in fc.get(k, [])]
            report["failure_categories"] = fc
    upd = {"graders": graders, "gates": gates, "extra_datasets": extra, "report": report}
    if pii["sets"] or pii["custom"]:
        upd["pii"] = {k: v for k, v in pii.items() if v}
    out = suite.model_copy(update=upd)
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
    if not suite.include_draft_cases:
        # generated / harvested cases count only once an expert has reviewed them (review_status: reviewed)
        cases = [c for c in cases if c.review_status != "draft"]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"case ids must be unique (duplicated: {', '.join(dup[:5])})")
    if suite.case_filter:
        cases = filter_cases(cases, suite.case_filter, suite.stress_tags, suite.seed)
    return cases


def filter_cases(cases: list[Case], f: CaseFilter, stress_tags: list[str], seed: int) -> list[Case]:
    import hashlib

    from .stats import split_of
    keep = []
    for c in cases:
        tags = set(c.tags or [])
        if c.critical:
            keep.append(c)
            continue
        if f.splits is not None and split_of(c, stress_tags) not in f.splits:
            continue
        if f.tags is not None and not tags & set(f.tags):
            continue
        if tags & set(f.exclude_tags):
            continue
        keep.append(c)
    if f.max_cases is not None:
        crit = [c for c in keep if c.critical]
        rest = sorted((c for c in keep if not c.critical),
                      key=lambda c: hashlib.sha256(f"{seed}:{c.id}".encode()).hexdigest())
        chosen = {c.id for c in crit + rest[:max(0, f.max_cases - len(crit))]}
        keep = [c for c in keep if c.id in chosen]
    return keep


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
    sampling: float = 1.0                            # share of captured sessions this source grades (hash of the id)
    capture_rate: float | None = None                # share of production traffic the telemetry itself captures
                                                     # (trace sampling, content capture); None = unknown
    filter: dict[str, Any] = Field(default_factory=dict)
    join: list[dict[str, Any]] = Field(default_factory=list)
    target: str | None = None                        # label used in reports (defaults to source name)


class JoinPolicy(BaseModel):
    """What an online run does when telemetry joins look wrong (docs/sources.md, Troubleshooting telemetry joins)."""
    model_config = ConfigDict(extra="forbid")
    #: invalid: the affected targets' gates are invalid and the run is marked unreliable (exit 1); warn: report only
    on_problem: Literal["invalid", "warn"] = "invalid"
    min_id_share: float = 0.9          # spans matched to their payload record by trace or session id
    max_time_share: float = 0.1        # spans matched by timestamp only
    min_content_share: float = 0.9     # samples with a completion to grade


class SourcesConfig(BaseModel):
    sources: list[SourceSpec]
    base_dir: str = "."
    join_policy: JoinPolicy = Field(default_factory=JoinPolicy)


def load_sources(path: str | Path) -> SourcesConfig:
    data = _load_yaml(path)
    return SourcesConfig(sources=data.get("sources", []), base_dir=str(Path(path).parent),
                         join_policy=data.get("join_policy") or {})


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
