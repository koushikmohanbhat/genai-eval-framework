"""The smallest connection: a plain function agent, its tools decorated so the evals see every call.

Run it in a suite as a Python target (no server):
    targets: [{name: support, kind: python, callable: "plain_agent:support_agent"}]
or serve it over HTTP with the genai-evals contract and use an HTTP target:
    python plain_agent.py                     # http://127.0.0.1:8080
    targets: [{name: support, kind: http, options: {url: "http://127.0.0.1:8080", contract: v1}}]
"""
import re

import genai_evals

ORDERS = {"ORD-1": {"status": "delivered", "amount": 900}, "ORD-2": {"status": "processing", "amount": 400}}


@genai_evals.tool
def lookup_order(order_id: str) -> dict:
    return ORDERS.get(order_id, {"status": "not_found"})


@genai_evals.tool
def issue_refund(order_id: str, amount: int) -> dict:
    genai_evals.report_state(refunds=1, refunded_amount=amount)     # the outcome final_state compares
    return {"ok": True}


@genai_evals.agent
def support_agent(message: str, session: dict, user: str | None = None) -> str:
    m = re.search(r"ORD-\d+", message)
    if m:
        session["order"] = m.group(0)                                # remembered for later turns
    if "refund" in message.lower():
        session["wants_refund"] = True
    oid = session.get("order")
    if not oid:
        return "Which order is this about?"
    order = lookup_order(oid)
    if session.pop("wants_refund", False) and order["status"] == "delivered":
        issue_refund(oid, order["amount"])
        return f"Refunded {order['amount']} for {oid}."
    return f"{oid} is {order['status']}."


if __name__ == "__main__":
    genai_evals.serve(support_agent, port=8080)
