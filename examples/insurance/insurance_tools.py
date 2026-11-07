"""Mock policy and claims APIs. Used by the offline agent and, unchanged, by the live tool_loop agent
(target `fnol-agent-live`): each function is a tool, called as fn(args, state). `state` becomes Sample.final_state."""
from __future__ import annotations

POLICIES = {
    "MOT-2026-004512": {"status": "active", "holder": "Rahul Verma", "covers": ["collision", "theft", "fire", "flood"], "addons": ["engine_protect"]},
    "MOT-2026-007731": {"status": "active", "holder": "Priya Nair", "covers": ["collision", "theft", "fire"], "addons": []},
    "MOT-2025-001198": {"status": "lapsed", "holder": "Arjun Mehta", "covers": ["collision", "theft", "fire", "flood"], "addons": []},
    "MOT-2026-002264": {"status": "active", "holder": "Sneha Kulkarni", "covers": ["collision", "fire"], "addons": []},
    "MOT-2024-009915": {"status": "cancelled", "holder": "Vikram Singh", "covers": ["collision"], "addons": []},
    "MOT-2026-005540": {"status": "active", "holder": "Ananya Iyer", "covers": ["collision", "theft", "fire", "flood"], "addons": ["zero_dep"]},
}


def make_state(case) -> dict:
    return {"claims_created": 0, "escalated": False, "claims": []}


def lookup_policy(args: dict, state: dict) -> dict:
    p = POLICIES.get(str(args.get("policy_number", "")).upper())
    if not p:
        return {"status": "not_found"}
    return {"status": p["status"], "holder": p["holder"], "covers": p["covers"]}


def check_coverage(args: dict, state: dict) -> dict:
    p = POLICIES.get(str(args.get("policy_number", "")).upper())
    if not p:
        return {"covered": False, "reason": "policy not found"}
    inc = args.get("incident_type", "collision")
    if inc == "flood" and "engine_protect" not in p["addons"]:
        return {"covered": inc in p["covers"], "note": "engine damage from water ingress needs the Engine Protect add-on"}
    return {"covered": inc in p["covers"], "reason": None if inc in p["covers"] else f"{inc} not in policy covers"}


def create_claim(args: dict, state: dict) -> dict:
    state["claims_created"] += 1
    cid = f"CLM-{len(state['claims']) + 1:04d}"
    state["claims"].append({"claim_id": cid, "policy_number": args.get("policy_number"), "incident_type": args.get("incident_type")})
    return {"claim_id": cid, "status": "registered"}


def escalate_to_human(args: dict, state: dict) -> dict:
    state["escalated"] = True
    return {"ticket": "HUM-0001", "eta_minutes": 10}
