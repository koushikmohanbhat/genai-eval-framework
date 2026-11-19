"""Offline stand-in for a tool-using support agent, so the pack runs with no keys or cloud access.

It follows the policy in prompts/support_agent (look up first, refund delivered orders up to 5,000 rupees,
escalate larger refunds, safety issues, legal threats and requests for a person, change the address only while
processing, never promise dates) using the mock tools in tools.py. Options (target `options:`) simulate typical
regressions for experiments:
  skip_lookup: share of conversations where it acts without looking the order up first (default 0)
  refund_limit: the refund limit it applies (default 5000; a prompt that drops the rule behaves like 100000)
  model: "fast" for a cheaper, faster model that skips the lookup a quarter of the time
  vulnerable: true behaves like an unguarded agent: it obeys instructions in order notes, shows its instructions when
    asked, and acts on any order whoever asks (to check that the security-guardrails overlay catches it)

Replace it with your agent: see README.md, "Connect your system".
"""
from __future__ import annotations

import hashlib
import re

import tools


def _rnd(*parts) -> float:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def agent(case, ctx) -> dict:
    opts = ctx.get("options") or {}
    fast = opts.get("model") == "fast"
    skip_rate = float(opts.get("skip_lookup", 0.25 if fast else 0))
    limit = int(opts.get("refund_limit", tools.REFUND_LIMIT))
    rep = ctx.get("repeat", 0)
    text = case.input_text()
    state, traj = tools.make_state(case), []

    def call(name, args):
        res = getattr(tools, name)(args, state)
        traj.append({"name": name, "args": args, "result": res})
        return res

    def done(reply, base_ms=1100):
        lat = base_ms + 350 * len(traj) + int(_rnd(case.id, rep, "lat") * 400)
        cost = 0.0012 + 0.0006 * len(traj)
        if fast:
            lat, cost = lat * 0.5, cost * 0.35
        return {"output": reply, "trajectory": traj, "final_state": state, "steps": 1 + len(traj),
                "latency_ms": round(lat), "cost_usd": round(cost, 6)}

    vulnerable = bool(opts.get("vulnerable"))
    if vulnerable and re.search(r"\b(system prompt|instructions|developer message)\b", text, re.I):
        return done("Sure. My instructions: " + tools.SYSTEM_PROMPT_TEXT, 700)
    if re.search(r"\b(system prompt|your (?:hidden |initial |system )?instructions|word for word|verbatim|developer message)\b",
                 text, re.I):
        return done("I can't share my instructions. I can help with your orders: status, refunds and address changes.", 700)
    me = (case.metadata or {}).get("customer")      # the signed-in customer, when the case says who is asking
    m = re.search(r"\bORD-\d{5}\b", text, re.I)
    oid = m.group(0).upper() if m else None
    if not oid:      # ids in other formats that overlays add to the order system (e.g. SAP sales order numbers)
        oid = next((k for k in tools.ORDERS if not k.startswith("ORD-") and re.search(rf"\b{re.escape(k)}\b", text)), None)
    wants_human = re.search(r"\b(human|person|court|legal|lawyer|fire|unsafe|injur\w*|burn\w*)\b", text, re.I)
    skip = _rnd(case.id, rep, "skip") < skip_rate

    if wants_human:
        if oid and not re.search(r"speak to a human|talk to a person", text, re.I) or (oid and "fire" in text.lower()):
            call("lookup_order", {"order_id": oid})
        call("escalate_to_human", {"reason": "customer asked for a person or reported a safety / legal issue"})
        return done("I've passed this to a colleague who will contact you shortly. I'm sorry for the trouble.")
    if not oid:
        return done("I can help with that. Could you share your order number? It looks like ORD-12345.", 800)

    order = None if skip else call("lookup_order", {"order_id": oid})
    if order and order["status"] == "not_found":
        return done(f"I couldn't find an order {oid}. Could you check the number?")
    if vulnerable and order and order.get("note"):
        note = order["note"]
        amt = re.search(r"issue_refund for (\d+)", note)
        if amt:
            call("issue_refund", {"order_id": oid, "amount": int(amt.group(1))})
            code = re.search(r"refund code is (\S+?)\.?$", note)
            return done(f"Good news: you're pre-approved. Your refund code is {code.group(1) if code else 'RF'}.")
        addr = re.search(r"update_address with (.+?) for", note)
        if addr:
            call("update_address", {"order_id": oid, "address": addr.group(1)})
            return done(f"Done: the delivery address is now {addr.group(1)}.")
    owner = order["customer"] if order else tools.ORDERS.get(oid, {}).get("customer")
    if me and owner and owner != me and not vulnerable:
        return done("I can only help with orders on your own account. Please sign in with the account that placed it.")
    status = order["status"] if order else tools.ORDERS.get(oid, {}).get("status", "delivered")
    amount = order["amount"] if order else tools.ORDERS.get(oid, {}).get("amount", 0)

    if re.search(r"ref(?:u)?nd|money back|credit (?:memo|note)", text, re.I):   # tolerate the common typo "refnd"
        part = re.search(r"refund\s+([\d,]+)\s*rupees", text, re.I)
        amt = int(part.group(1).replace(",", "")) if part else amount
        if status == "cancelled":
            return done("This order was cancelled, so its payment is refunded automatically within 5 to 7 working days.")
        if status != "delivered":
            return done("A refund can be requested once the order is delivered. I can help you then.")
        if amt > limit:
            call("escalate_to_human", {"reason": f"refund of {amt} rupees is above the limit"})
            return done("This refund needs a specialist's approval, so I've passed it to a colleague who will contact you.")
        call("issue_refund", {"order_id": oid, "amount": amt})
        return done(f"I've issued a refund of {amt:,} rupees for {oid}. It reaches your account in 5 to 7 working days.")
    if re.search(r"address|ship-to", text, re.I):
        if status == "processing":
            addr = re.search(r"\bto (.+?)\.?$", text)
            call("update_address", {"order_id": oid, "address": addr.group(1) if addr else ""})
            return done(f"Done: the delivery address for {oid} is updated.")
        return done(f"{oid} has already {status}, so its address can't be changed. You can redirect it with the courier.")
    return done(f"{oid} is {status}." + (" You'll get tracking updates by SMS." if status == "shipped" else ""))
