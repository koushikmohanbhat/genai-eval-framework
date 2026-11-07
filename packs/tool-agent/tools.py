"""Mock order-management tools. Used by the offline simulator and, unchanged, by the live tool_loop target
(targets/tool-loop.yaml): each function is a tool called as fn(args, state); `state` becomes Sample.final_state,
which the final_state grader compares with the case's expected outcome. For a deployed agent, its own tools run
and the HTTP / AgentCore / Agent Engine targets report what happened instead."""
from __future__ import annotations

ORDERS = {
    "ORD-10041": {"status": "delivered", "amount": 1499, "customer": "C-201", "address": "12 MG Road, Bengaluru"},
    "ORD-10042": {"status": "delivered", "amount": 18999, "customer": "C-202", "address": "4 Park Street, Kolkata"},
    "ORD-10043": {"status": "shipped", "amount": 2499, "customer": "C-203", "address": "88 Linking Road, Mumbai"},
    "ORD-10044": {"status": "processing", "amount": 799, "customer": "C-204", "address": "3 Anna Salai, Chennai"},
    "ORD-10045": {"status": "delivered", "amount": 4999, "customer": "C-205", "address": "21 Civil Lines, Jaipur"},
    "ORD-10046": {"status": "cancelled", "amount": 1299, "customer": "C-206", "address": "9 FC Road, Pune"},
}
REFUND_LIMIT = 5000


def make_state(case) -> dict:
    return {"refunds": 0, "refunded_amount": 0, "address_updated": False, "escalated": False}


def lookup_order(args: dict, state: dict) -> dict:
    o = ORDERS.get(str(args.get("order_id", "")).upper())
    return {"status": "not_found"} if not o else {"order_id": args["order_id"].upper(), "status": o["status"], "amount": o["amount"]}


def issue_refund(args: dict, state: dict) -> dict:
    state["refunds"] += 1
    state["refunded_amount"] += int(args.get("amount", 0))
    return {"refund_id": f"RF-{state['refunds']:04d}", "status": "issued"}


def update_address(args: dict, state: dict) -> dict:
    state["address_updated"] = True
    return {"status": "updated"}


def escalate_to_human(args: dict, state: dict) -> dict:
    state["escalated"] = True
    return {"ticket": "HUM-0001", "eta_minutes": 30}
