"""How many cases does a conclusion need? Normal-approximation sample sizes for pass rates.

- `n_for_interval`: cases for a 95% interval of +/- half_width around a pass rate (one system, one number).
- `n_unpaired`: cases per arm to detect a pass-rate change of `delta` between two independent samples.
- `n_paired`: cases to detect a change of `delta` on the **same** cases (McNemar), given the share of cases whose
  verdict differs between the two systems (`discordant`). Paired designs need far fewer cases when systems mostly agree.
- `mde_unpaired` / `mde_paired`: the smallest change a given number of cases can detect.

These are planning numbers (normal approximation, binary pass / fail, independent cases), not guarantees. Repeats
don't add cases: they are averaged per case first.
"""
from __future__ import annotations

import math
from statistics import NormalDist

Z = NormalDist()


def _z(p: float) -> float:
    return Z.inv_cdf(p)


def n_for_interval(rate: float, half_width: float, alpha: float = 0.05) -> int:
    rate = min(max(rate, 1e-6), 1 - 1e-6)
    return math.ceil(_z(1 - alpha / 2) ** 2 * rate * (1 - rate) / half_width ** 2)


def n_unpaired(rate: float, delta: float, alpha: float = 0.05, power: float = 0.8) -> int:
    p1, p2 = rate, min(max(rate + delta, 1e-6), 1 - 1e-6)
    pbar = (p1 + p2) / 2
    za, zb = _z(1 - alpha / 2), _z(power)
    num = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / delta ** 2)


def n_paired(delta: float, discordant: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """McNemar (Connor 1987). `discordant`: share of cases where the two systems' verdicts differ (>= |delta|)."""
    d = max(discordant, abs(delta))
    za, zb = _z(1 - alpha / 2), _z(power)
    return math.ceil((za * math.sqrt(d) + zb * math.sqrt(d - delta ** 2)) ** 2 / delta ** 2)


def mde_unpaired(n: int, rate: float, alpha: float = 0.05, power: float = 0.8) -> float:
    rate = min(max(rate, 1e-6), 1 - 1e-6)
    return (_z(1 - alpha / 2) + _z(power)) * math.sqrt(2 * rate * (1 - rate) / n)


def mde_paired(n: int, discordant: float, alpha: float = 0.05, power: float = 0.8) -> float:
    return (_z(1 - alpha / 2) + _z(power)) * math.sqrt(discordant / n)


def plan(rate: float, delta: float | None = None, half_width: float | None = None, n: int | None = None,
         discordant: float | None = None, alpha: float = 0.05, power: float = 0.8) -> dict:
    out: dict = {"rate": rate, "alpha": alpha, "power": power}
    if half_width:
        out["interval"] = {"half_width": half_width, "cases": n_for_interval(rate, half_width, alpha)}
    if delta:
        out["detect"] = {"delta": delta, "unpaired_cases_per_arm": n_unpaired(rate, delta, alpha, power)}
        if discordant:
            out["detect"] |= {"discordant": discordant, "paired_cases": n_paired(delta, discordant, alpha, power)}
    if n:
        out["with_n"] = {"n": n, "interval_half_width": _z(1 - alpha / 2) * math.sqrt(rate * (1 - rate) / n),
                         "mde_unpaired": mde_unpaired(n, rate, alpha, power)}
        if discordant:
            out["with_n"]["mde_paired"] = mde_paired(n, discordant, alpha, power)
    return out


#: rough guidance from Anthropic's "Building strong safety evaluations": items needed before a safety claim
SAFETY_GUIDANCE = {"multi_turn": 100, "single_turn": 1000}
