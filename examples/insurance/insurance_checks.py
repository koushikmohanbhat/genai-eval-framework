"""Custom (python) graders for the insurance suites."""
from __future__ import annotations


def verified_policy_first(sample, case, params):
    """If the agent created a claim, it must have looked up the policy first."""
    names = [t.name for t in sample.trajectory if t.kind == "tool"]
    if "create_claim" not in names:
        return {"skip": True, "reason": "no claim created"}
    ok = "lookup_policy" in names and names.index("lookup_policy") < names.index("create_claim")
    return {"value": 1.0 if ok else 0.0, "reasoning": "policy verified before claim" if ok else "claim created without verifying the policy"}
