"""Scenario grids: vary each dimension of a scenario independently.

A grid file names dimensions (persona, tone, severity, tactic, ...) and a template for the case. `cases grid` writes a
draft case for every combination (full factorial), a random sample of them, or an all-pairs design that covers every
pair of values across two dimensions with far fewer cases. Each case is tagged `<dimension>=<value>`, so the report's
slices show results by each dimension separately. Drafts need an expert's reference and review before they count,
like any generated case.

```yaml
id_prefix: refund-grid
template:
  goal: "You want a refund for a {{ product }} that arrived {{ severity }}. You are {{ tone }}."
  persona: "{{ persona }}"
  tags: [refund, edge]
dimensions:
  tone: [calm, angry, confused]
  severity: [scratched, broken, missing parts]
  persona: [first-time buyer, regular customer]
  product: [phone, kettle]
exclude:
  - {tone: calm, severity: missing parts}
```
"""
from __future__ import annotations

import hashlib
import itertools
import random
from typing import Any


def _render(x: Any, vals: dict[str, str]) -> Any:
    from jinja2.sandbox import SandboxedEnvironment
    env = SandboxedEnvironment()
    if isinstance(x, str):
        return env.from_string(x).render(**vals)
    if isinstance(x, list):
        return [_render(v, vals) for v in x]
    if isinstance(x, dict):
        return {k: _render(v, vals) for k, v in x.items()}
    return x


def combinations(dims: dict[str, list[Any]], exclude: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    names = list(dims)
    out = [dict(zip(names, vs)) for vs in itertools.product(*(dims[n] for n in names))]
    ex = exclude or []
    return [c for c in out if not any(all(c.get(k) == v for k, v in e.items()) for e in ex)]


def all_pairs(dims: dict[str, list[Any]], exclude: list[dict[str, Any]] | None = None, seed: int = 7) -> list[dict[str, Any]]:
    """A greedy covering design: every pair of values from any two dimensions appears in at least one case (when the
    exclusions allow it)."""
    full = combinations(dims, exclude)
    names = list(dims)

    def pairs(c):
        return {(a, c[a], b, c[b]) for a, b in itertools.combinations(names, 2)}
    need = set().union(*(pairs(c) for c in full)) if full else set()
    rng = random.Random(seed)
    pool = full[:]
    rng.shuffle(pool)
    chosen = []
    while need:
        best = max(pool, key=lambda c: len(pairs(c) & need))
        gain = pairs(best) & need
        if not gain:
            break
        chosen.append(best)
        need -= gain
        pool.remove(best)
    return chosen


def grid_cases(spec: dict[str, Any], design: str = "full", sample: int | None = None, seed: int = 7) -> list[dict[str, Any]]:
    dims = spec.get("dimensions") or {}
    if not dims or not all(isinstance(v, list) and v for v in dims.values()):
        raise ValueError("grid needs `dimensions:` with a non-empty list of values for each")
    tmpl = spec.get("template") or {}
    if not any(k in tmpl for k in ("input", "goal", "turns")):
        raise ValueError("grid template needs input, goal or turns")
    if design == "pairwise":
        combos = all_pairs(dims, spec.get("exclude"), seed)
    else:
        combos = combinations(dims, spec.get("exclude"))
    if sample and sample < len(combos):
        combos = random.Random(seed).sample(combos, sample)
    prefix = spec.get("id_prefix", "grid")
    out = []
    for c in combos:
        vals = {k: str(v) for k, v in c.items()}
        case = _render({k: v for k, v in tmpl.items() if k not in ("tags", "reference")}, vals)
        h = hashlib.sha256("|".join(f"{k}={vals[k]}" for k in sorted(vals)).encode()).hexdigest()[:8]
        case["id"] = f"{prefix}-{h}"
        case["tags"] = list(dict.fromkeys(list(tmpl.get("tags") or []) + [f"{k}={v}" for k, v in vals.items()]))
        case["reference"] = _render(tmpl.get("reference") or {}, vals)
        case["source"] = "synthetic"
        case["review_status"] = "draft"
        case["metadata"] = {"grid": vals, "grid_design": design}
        out.append(case)
    return out
