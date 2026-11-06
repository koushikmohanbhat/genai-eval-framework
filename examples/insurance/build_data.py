"""Regenerates the synthetic datasets and recorded telemetry fixtures for the insurance demo (deterministic).

All people, policy numbers, phone numbers and identifiers are synthetic. Run: python build_data.py
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
DATA, FX = HERE / "data", HERE / "fixtures"
R = random.Random(2026)


def w(path: Path, rows, jsonl=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    if jsonl:
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    else:
        path.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
POLICY = [
    ("pw-01", "Own damage cover", "Own damage cover pays for repairs to your car after an accident, collision, fire, theft, flood or cyclone. Repairs at network garages are cashless."),
    ("pw-02", "Third-party liability", "Third-party liability covers injury to other people and damage to their property. Third-party cover is mandatory under the Motor Vehicles Act."),
    ("pw-03", "Claim intimation", "You must inform us of any accident within 48 hours by calling the claims desk or using the app. Late intimation may lead to the claim being rejected."),
    ("pw-04", "Documents required", "To register a claim you need the policy number, a copy of your driving licence, the vehicle registration certificate and photographs of the damage."),
    ("pw-05", "Theft claims", "For theft you must file an FIR with the police within 24 hours. A theft claim is settled after the police issue a non-traceable report, usually within 90 days."),
    ("pw-06", "Flood and engine damage", "Flood damage to the body is covered under own damage. Engine damage caused by water ingress or hydrostatic lock is covered only with the Engine Protect add-on."),
    ("pw-07", "Exclusions", "We do not pay claims when the driver had no valid licence, was driving under the influence of alcohol, or the car was used for racing."),
    ("pw-08", "Zero depreciation add-on", "With the zero depreciation add-on we pay the full cost of replaced plastic, rubber and metal parts without deducting depreciation, for up to two claims a year."),
    ("pw-09", "No claim bonus", "The no claim bonus is a discount on own damage premium for every claim-free year, starting at 20 percent and rising to 50 percent after five years."),
    ("pw-10", "Personal accident cover", "Compulsory personal accident cover for the owner-driver pays 15 lakh rupees for death or permanent total disability in an accident."),
    ("pw-11", "Grievances", "If you are unhappy with a claim decision, write to the grievance cell. If it is not resolved within 30 days you can approach the Insurance Ombudsman."),
    ("pw-12", "Cashless garages", "Cashless repairs are available at more than 4,000 network garages. At other garages you pay first and we reimburse approved costs."),
]
w(DATA / "policy_wording.json", [{"id": i, "title": t, "text": x} for i, t, x in POLICY], jsonl=False)

QA = [
    ("qa-01", "How soon do I have to tell you about an accident?", ["pw-03"], "within 48 hours", True, ["intimation"]),
    ("qa-02", "What documents do I need to register a claim?", ["pw-04"], "policy number, driving licence, registration certificate and photographs", True, ["documents"]),
    ("qa-03", "My car was stolen, what do I need to do for the claim?", ["pw-05"], "file an FIR within 24 hours; settled after a non-traceable report", True, ["theft", "critical-info"]),
    ("qa-04", "Is engine damage from driving through a flooded road covered?", ["pw-06"], "only with the Engine Protect add-on", True, ["flood"]),
    ("qa-05", "Will you pay if the driver was drunk?", ["pw-07"], "no, claims are excluded when driving under the influence", True, ["exclusions"]),
    ("qa-06", "What does the zero depreciation add-on give me?", ["pw-08"], "full cost of replaced parts without depreciation, up to two claims a year", True, ["addons"]),
    ("qa-07", "How much no claim bonus can I get?", ["pw-09"], "20 percent rising to 50 percent after five claim-free years", True, ["premium"]),
    ("qa-08", "What is the personal accident cover amount for the owner-driver?", ["pw-10"], "15 lakh rupees", True, ["pa-cover"]),
    ("qa-09", "Who do I complain to if I disagree with the claim decision?", ["pw-11"], "the grievance cell, then the Insurance Ombudsman after 30 days", True, ["grievance"]),
    ("qa-10", "Can I get cashless repairs at any garage?", ["pw-12", "pw-01"], "only at network garages; other garages are reimbursed", True, ["garages"]),
    ("qa-11", "Does third-party cover pay for injuries to other people?", ["pw-02"], "yes, third-party liability covers injury to other people", True, ["third-party"]),
    ("qa-12", "Is damage from a cyclone covered under own damage?", ["pw-01"], "yes, own damage covers cyclone", True, ["own-damage"]),
    ("qa-13", "Is a theft claim settled immediately?", ["pw-05"], "no, after a non-traceable report, usually within 90 days", True, ["theft"]),
    ("qa-14", "Does the policy cover my medical bills when travelling abroad?", [], None, False, ["out-of-scope"]),
    ("qa-15", "Can I transfer my policy to my brother's bike?", [], None, False, ["out-of-scope"]),
    ("qa-16", "What is the premium for a new Tesla in Pune?", [], None, False, ["out-of-scope"]),
    ("qa-17", "Is my phone covered if it is stolen from the car?", [], None, False, ["out-of-scope"]),
    ("qa-18", "Do I lose my claim if I tell you about the accident after a week?", ["pw-03"], "late intimation may lead to rejection", True, ["intimation"]),
    ("qa-19", "Is racing damage covered?", ["pw-07"], "no, racing is excluded", True, ["exclusions"]),
    ("qa-20", "Is flood damage to the car body covered?", ["pw-06", "pw-01"], "yes, under own damage", True, ["flood"]),
]
w(DATA / "policy_qa.jsonl", [{"id": i, "input": q, "tags": tags, "critical": "critical-info" in tags,
                              "reference": {"gold_passages": g, "answer": a, "answerable": ans}} for i, q, g, a, ans, tags in QA])

# human labels for judge calibration (faithfulness, rag-v1 + rag-v2), as an SME would mark them
labels = ["case_id,target,label"]
for i, _q, _g, _a, _ans, _t in QA:
    labels.append(f"{i},rag-v2,pass")
    n = int(i.split("-")[1])
    labels.append(f"{i},rag-v1,{'fail' if n % 2 == 0 else 'pass'}")
(DATA / "faithfulness_labels.csv").write_text("\n".join(labels) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
NAMES = ["Rahul Verma", "Priya Nair", "Arjun Mehta", "Sneha Kulkarni", "Vikram Singh", "Ananya Iyer", "Karthik Rao", "Meera Joshi",
         "Imran Shaikh", "Divya Menon", "Rohan Gupta", "Fatima Khan"]
REGS = ["KA 05 MN 4521", "MH 12 AB 9087", "DL 3C AQ 1123", "TN 09 BX 7765", "KA 51 HE 3309", "GJ 01 RT 5512"]
INC = [("collision", "my car was hit from behind at a signal"), ("theft", "my car was stolen from outside my house"),
       ("flood", "the car was submerged in water logging"), ("fire", "the car caught fire on the highway")]
ext = []
for n in range(24):
    name = NAMES[n % len(NAMES)]
    pol = f"MOT-2026-{R.randint(1000, 999999):06d}"
    reg = REGS[n % len(REGS)]
    inc, story = INC[n % 4]
    day = datetime(2026, R.randint(1, 10), R.randint(1, 28))
    amt = R.choice([18500, 42000, 65000, 120000, 250000, 87500])
    fmt = n % 4
    if fmt == 0:
        date_s, amt_s, tag = day.strftime("%d/%m/%Y"), f"Rs {amt:,}", "format:form"
        text = f"CLAIM FORM\nPolicy No: {pol}\nName: {name}\nDate of incident: {date_s}\nVehicle: {reg}\nDescription: {story}.\nEstimated loss: {amt_s}"
    elif fmt == 1:
        date_s, amt_s, tag = day.strftime("%Y-%m-%d"), f"INR {amt}", "format:email"
        text = f"Hi team, this is {name}. On {date_s} {story}. Policy {pol}, registration {reg}. The garage estimate is {amt_s}. Please help."
    elif fmt == 2:
        date_s = day.strftime("%d %b %Y")
        amt_s = f"₹{amt/1e5:g} lakh" if amt >= 100000 else f"₹{amt:,}"
        tag = "format:email"
        text = f"Dear Sir, I am {name}. {story.capitalize()} on {date_s}. My policy number is {pol} and the car is {reg}. Loss is around {amt_s}."
    else:
        date_s, amt_s, tag = day.strftime("%d/%m/%Y"), f"Rs {amt//1000}k", "lang:hinglish"
        text = f"Namaste, main {name} bol raha hoon. {date_s} ko {story}. Policy {pol}, gaadi {reg}. Kharcha approx {amt_s} hoga."
    ext.append({"id": f"ex-{n + 1:02d}", "input": text, "tags": [tag, f"incident:{inc}"] + (["high-value"] if amt >= 100000 else []),
                "critical": amt >= 200000,
                "reference": {"fields": {"policy_number": pol, "claimant_name": name, "incident_date": day.strftime("%Y-%m-%d"),
                                         "claim_amount": float((amt // 1000) * 1000 if fmt == 3 else amt),
                                         "vehicle_reg": reg, "incident_type": inc}}})
w(DATA / "claims_extraction.jsonl", ext)

# ---------------------------------------------------------------------------
AGENT = [
    # id, turns, expected final state, tags, critical
    ("ag-01", ["Hi, my car was hit from behind near Indiranagar on 02/11/2026. Policy MOT-2026-004512."], {"claims_created": 1, "escalated": False}, ["collision"], False),
    ("ag-02", ["Policy MOT-2026-007731. My car was stolen at Koramangala last night, 03/11/2026."], {"claims_created": 1, "escalated": False}, ["theft"], False),
    ("ag-03", ["I had an accident near Whitefield on 01/11/2026, my policy is MOT-2025-001198."], {"claims_created": 0, "escalated": False}, ["lapsed"], True),
    ("ag-04", ["Car crash at Hebbal on 30/10/2026, my friend is injured and in hospital. Policy MOT-2026-004512."], {"escalated": True, "claims_created": 0}, ["injury"], False),
    ("ag-05", ["My car got submerged in water logging at Silk Board on 28/10/2026. Policy MOT-2026-005540."], {"claims_created": 1, "escalated": False}, ["flood"], False),
    ("ag-06", ["Someone rear-ended me near Andheri, 29/10/2026. Policy MOT-2024-009915."], {"claims_created": 0, "escalated": False}, ["lapsed"], True),
    ("ag-07", ["Policy MOT-2026-002264, the car caught fire on the highway near Lonavala on 27/10/2026."], {"claims_created": 1, "escalated": False}, ["fire"], False),
    ("ag-08", ["Accident at Marine Drive on 31/10/2026, I am hurt, bleeding from my arm. Policy MOT-2026-007731."], {"escalated": True, "claims_created": 0}, ["injury"], False),
    ("ag-09", ["My policy MOT-2025-001198, car stolen near Baner on 02/11/2026."], {"claims_created": 0, "escalated": False}, ["lapsed", "theft"], True),
    ("ag-10", ["Collision at Hitech City on 01/11/2026, nobody hurt. Policy MOT-2026-005540."], {"claims_created": 1, "escalated": False}, ["collision"], False),
    ("ag-11", ["Flood damage near Velachery on 26/10/2026, policy MOT-2026-002264."], {"claims_created": 0, "escalated": False}, ["flood", "not-covered"], False),
    ("ag-12", ["My car was hit near Salt Lake on 30/10/2026."], {"claims_created": 0, "escalated": False}, ["missing-info"], False),
    ("ag-13", ["Takkar ho gayi near Karol Bagh on 01/11/2026, policy MOT-2026-004512, driver ko chot lagi hai, hospital gaye."], {"escalated": True, "claims_created": 0}, ["injury", "lang:hinglish"], False),
    ("ag-14", ["Policy MOT-2026-007731. Bumper damage at Jayanagar on 03/11/2026."], {"claims_created": 1, "escalated": False}, ["collision"], False),
    ("ag-15", ["Hi, MOT-2024-009915 here, car caught fire near Thane on 28/10/2026."], {"claims_created": 0, "escalated": False}, ["lapsed", "fire"], True),
    ("ag-16", ["Accident near Gachibowli on 02/11/2026, the other driver is hurt. Policy MOT-2026-005540."], {"escalated": True, "claims_created": 0}, ["injury"], False),
]
w(DATA / "fnol_agent.jsonl", [{"id": i, "input": t[0], "turns": t, "tags": tags, "critical": crit,
                                "reference": {"final_state": st,
                                              "tool_calls": ([{"name": "lookup_policy", "args": {"policy_number": re.search(r"MOT-\d{4}-\d{6}", t[0]).group(0)}}]
                                                             if "MOT-" in t[0] and "injury" not in tags else [])}}
                               for i, t, st, tags, crit in AGENT])

# ---------------------------------------------------------------------------
# Recorded production telemetry for the online demo (synthetic). AWS: AgentCore Runtime -> CloudWatch aws/spans.
T0 = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


def ns(dt):
    return int(dt.timestamp() * 1e9)


def hexid(n, k=32):
    return f"{R.getrandbits(k * 4):0{k}x}"


PROD = [
    # session, user text, assistant reply, tools, prompt version, capture content?, extra
    ("Hi, car hit near Indiranagar today, policy MOT-2026-004512.", "I've registered claim CLM-0001 for the collision. A surveyor will contact you within 24 hours.",
     [("lookup_policy", {"policy_number": "MOT-2026-004512"}, {"status": "active"}), ("check_coverage", {"policy_number": "MOT-2026-004512", "incident_type": "collision"}, {"covered": True}),
      ("create_claim", {"policy_number": "MOT-2026-004512", "incident_type": "collision"}, {"claim_id": "CLM-0001"})], "1.3.2", True),
    ("Policy MOT-2026-007731, stolen car at Koramangala.", "I've registered claim CLM-0002 for the theft. Please file an FIR within 24 hours.",
     [("lookup_policy", {"policy_number": "MOT-2026-007731"}, {"status": "active"}), ("create_claim", {"policy_number": "MOT-2026-007731", "incident_type": "theft"}, {"claim_id": "CLM-0002"})], "1.3.2", True),
    ("Accident at Hebbal, my policy MOT-2025-001198.", "Your policy MOT-2025-001198 is not active (lapsed), so I can't register a claim on it.",
     [("lookup_policy", {"policy_number": "MOT-2025-001198"}, {"status": "lapsed"})], "1.3.2", False),
    ("Car caught fire near Lonavala, policy MOT-2026-002264. Call me on 98765 43210.", "Claim CLM-0004 registered. We will call you on 98765 43210 shortly.",
     [("lookup_policy", {"policy_number": "MOT-2026-002264"}, {"status": "active"}), ("create_claim", {"policy_number": "MOT-2026-002264", "incident_type": "fire"}, {"claim_id": "CLM-0004"})], "1.3.2", True),
    ("Flooded at Silk Board, MOT-2026-005540.", "I've registered claim CLM-0005 for the flood damage. Your claim will be paid in full within a week.",
     [("check_coverage", {"policy_number": "MOT-2026-005540", "incident_type": "flood"}, {"covered": True}), ("create_claim", {"policy_number": "MOT-2026-005540", "incident_type": "flood"}, {"claim_id": "CLM-0005"})], "1.2.0", True),
    ("Bumper damage at Jayanagar, MOT-2026-007731.", "I've registered claim CLM-0006 for the collision. A surveyor will contact you within 24 hours.",
     [("lookup_policy", {"policy_number": "MOT-2026-007731"}, {"status": "active"}), ("create_claim", {"policy_number": "MOT-2026-007731", "incident_type": "collision"}, {"claim_id": "CLM-0006"})], "1.3.2", False),
    ("Hit a divider near Gachibowli, policy MOT-2026-005540.", "I've registered claim CLM-0007. A surveyor will contact you within 24 hours.",
     [("lookup_policy", {"policy_number": "MOT-2026-005540"}, {"status": "active"}), ("create_claim", {"policy_number": "MOT-2026-005540", "incident_type": "collision"}, {"claim_id": "CLM-0007"})], "1.3.2", True),
    ("What documents do I need?", "You need the policy number, driving licence, registration certificate and photos of the damage.", [], "1.3.2", True),
]


def aws_spans():
    spans, inv_logs, memory, evals = [], [], [], []
    for n, (user, reply, tools_, ver, capture) in enumerate(PROD):
        tid, sid = hexid(0), f"agentcore-sess-{n + 1:04d}-{hexid(0, 8)}"
        t = T0 + timedelta(minutes=7 * n)
        root = hexid(0, 16)
        res = {"service.name": "claims-agent", "cloud.provider": "aws", "cloud.region": "ap-south-1",
               "aws.agentcore.runtime.arn": "arn:aws:bedrock-agentcore:ap-south-1:111122223333:runtime/claims_agent-AbCdEf"}
        cur = t
        llm1 = hexid(0, 16)
        cur2 = cur + timedelta(milliseconds=900)
        a_llm = {"gen_ai.operation.name": "chat", "gen_ai.system": "aws.bedrock", "gen_ai.request.model": "anthropic.claude-sonnet-v1",
                 "gen_ai.usage.input_tokens": 1450 + n * 13, "gen_ai.usage.output_tokens": 120 + n, "session.id": sid, "prompt.id": "claims_agent", "prompt.version": ver}
        if capture:
            a_llm["gen_ai.input.messages"] = json.dumps([{"role": "user", "content": user}])
        spans.append({"traceId": tid, "spanId": llm1, "parentSpanId": root, "name": "chat anthropic.claude-sonnet-v1", "startTimeUnixNano": ns(cur),
                      "endTimeUnixNano": ns(cur2), "attributes": a_llm, "resource": {"attributes": res}})
        cur = cur2
        for tool, args, result in tools_:
            sp = hexid(0, 16)
            end = cur + timedelta(milliseconds=180)
            spans.append({"traceId": tid, "spanId": sp, "parentSpanId": root, "name": f"execute_tool {tool}", "startTimeUnixNano": ns(cur), "endTimeUnixNano": ns(end),
                          "attributes": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool, "gen_ai.tool.call.arguments": json.dumps(args),
                                         "gen_ai.tool.call.result": json.dumps(result), "session.id": sid}, "resource": {"attributes": res}})
            cur = end
        llm2 = hexid(0, 16)
        end = cur + timedelta(milliseconds=1100 + 150 * n)
        a2 = {"gen_ai.operation.name": "chat", "gen_ai.system": "aws.bedrock", "gen_ai.request.model": "anthropic.claude-sonnet-v1",
              "gen_ai.usage.input_tokens": 1800 + n * 21, "gen_ai.usage.output_tokens": 90 + n, "session.id": sid, "prompt.id": "claims_agent", "prompt.version": ver}
        if capture:
            a2["gen_ai.input.messages"] = json.dumps([{"role": "user", "content": user}])
            a2["gen_ai.output.messages"] = json.dumps([{"role": "assistant", "content": reply}])
        spans.append({"traceId": tid, "spanId": llm2, "parentSpanId": root, "name": "chat anthropic.claude-sonnet-v1", "startTimeUnixNano": ns(cur),
                      "endTimeUnixNano": ns(end), "attributes": a2, "resource": {"attributes": res}})
        spans.append({"traceId": tid, "spanId": root, "name": "invoke_agent claims_agent", "startTimeUnixNano": ns(t), "endTimeUnixNano": ns(end + timedelta(milliseconds=40)),
                      "attributes": {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "claims_agent", "session.id": sid, "enduser.id": f"cust-{1000 + n}",
                                     "prompt.id": "claims_agent", "prompt.version": ver}, "resource": {"attributes": res}})
        # Bedrock model invocation logs (always have content): used to fill sessions where span content capture was off
        inv_logs.append({"schemaType": "ModelInvocationLog", "schemaVersion": "1.0", "timestamp": (end - timedelta(milliseconds=200)).isoformat().replace("+00:00", "Z"),
                         "accountId": "111122223333", "region": "ap-south-1", "requestId": f"req-{hexid(0, 12)}", "operation": "Converse",
                         "modelId": "anthropic.claude-sonnet-v1",
                         "input": {"inputContentType": "application/json", "inputTokenCount": a2["gen_ai.usage.input_tokens"],
                                   "inputBodyJson": {"messages": [{"role": "user", "content": [{"text": user}]}]}},
                         "output": {"outputContentType": "application/json", "outputTokenCount": a2["gen_ai.usage.output_tokens"],
                                    "outputBodyJson": {"output": {"message": {"role": "assistant", "content": [{"text": reply}]}}, "stopReason": "end_turn",
                                                       "usage": {"inputTokens": a2["gen_ai.usage.input_tokens"], "outputTokens": a2["gen_ai.usage.output_tokens"]}}}})
        memory.append({"eventId": f"ev-{n}-1", "sessionId": sid, "actorId": f"cust-{1000 + n}", "eventTimestamp": t.isoformat(),
                       "payload": [{"conversational": {"role": "USER", "content": {"text": user}}}]})
        memory.append({"eventId": f"ev-{n}-2", "sessionId": sid, "actorId": f"cust-{1000 + n}", "eventTimestamp": end.isoformat(),
                       "payload": [{"conversational": {"role": "ASSISTANT", "content": {"text": reply}}}]})
        evals.append({"evaluatorId": "Builtin.Helpfulness", "evaluatorName": "Builtin.Helpfulness", "sessionId": sid, "traceId": tid,
                      "value": round(0.55 + 0.05 * (n % 8), 2), "label": "Helpful" if n % 5 else "SomewhatHelpful",
                      "explanation": "Synthetic AgentCore Evaluations result for the demo."})
        evals.append({"evaluatorId": "Builtin.GoalSuccessRate", "evaluatorName": "Builtin.GoalSuccessRate", "sessionId": sid, "traceId": tid,
                      "value": 1.0 if n not in (4,) else 0.0, "label": "Yes" if n != 4 else "No", "explanation": "Synthetic."})
    return spans, inv_logs, memory, evals


aspans, ainv, amem, aevals = aws_spans()
w(FX / "aws" / "aws_spans.jsonl", [{"timestamp": s["startTimeUnixNano"] // 10**6, "message": json.dumps(s)} for s in aspans])
w(FX / "aws" / "bedrock_invocation_logs.jsonl", ainv)
w(FX / "aws" / "agentcore_memory_events.jsonl", amem)
w(FX / "aws" / "agentcore_evaluations_results.jsonl", aevals)
w(FX / "aws" / "cloudwatch_metrics.json", [{"Invocations": 1874, "InvocationLatency": 1730.5, "InputTokenCount": 2911034, "OutputTokenCount": 214406,
                                            "InvocationThrottles": 3, "InvocationClientErrors": 1}], jsonl=False)

# Google Cloud: the same agent on Vertex AI Agent Engine. Telemetry API (OTLP) -> Cloud Trace. Content capture is OFF
# (Google's default), so prompts / responses come from the Vertex request-response table in BigQuery.
GPROD = [
    ("Collision at Hitech City, policy MOT-2026-005540.", "I've registered claim CLM-1101 for the collision. A surveyor will contact you within 24 hours.",
     [("lookup_policy", {"policy_number": "MOT-2026-005540"}), ("check_coverage", {"policy_number": "MOT-2026-005540", "incident_type": "collision"}),
      ("create_claim", {"policy_number": "MOT-2026-005540", "incident_type": "collision"})]),
    ("Theft near Baner, MOT-2025-001198.", "Your policy MOT-2025-001198 is not active (lapsed), so I can't register a claim on it.", [("lookup_policy", {"policy_number": "MOT-2025-001198"})]),
    ("Fire near Thane, MOT-2026-002264.", "I've registered claim CLM-1103 for the fire. A surveyor will contact you within 24 hours.",
     [("create_claim", {"policy_number": "MOT-2026-002264", "incident_type": "fire"})]),
    ("Accident near Andheri, other driver hurt, MOT-2026-007731.", "I'm sorry someone was hurt. A claims specialist will call you right away; call 112 for emergencies.",
     [("escalate_to_human", {"reason": "injury"})]),
    ("How long does a theft claim take?", "A theft claim is settled after the police issue a non-traceable report, usually within 90 days.", []),
]
gspans, bq, gsessions = [], [], []
for n, (user, reply, tools_) in enumerate(GPROD):
    tid, sid = hexid(0), f"{8000000000 + n}"
    t = T0 + timedelta(minutes=11 * n + 3)
    root = str(R.randint(10**15, 10**16))
    labels = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "claims_agent", "gcp.vertex.agent.session_id": sid,
              "session.id": sid, "prompt.id": "claims_agent", "prompt.version": "1.3.2", "service.name": "claims-agent-gcp"}
    cur = t + timedelta(milliseconds=700)
    gspans.append({"trace_id": tid, "spanId": str(R.randint(10**15, 10**16)), "parentSpanId": root, "name": "call_llm", "startTime": t.isoformat(),
                   "endTime": cur.isoformat(), "labels": {"gen_ai.operation.name": "generate_content", "gen_ai.request.model": "gemini-flash",
                                                          "gen_ai.usage.input_tokens": "1320", "gen_ai.usage.output_tokens": "88", "session.id": sid,
                                                          "service.name": "claims-agent-gcp"}})
    for tool, args in tools_:
        end = cur + timedelta(milliseconds=150)
        gspans.append({"trace_id": tid, "spanId": str(R.randint(10**15, 10**16)), "parentSpanId": root, "name": f"execute_tool {tool}", "startTime": cur.isoformat(),
                       "endTime": end.isoformat(), "labels": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool,
                                                              "gen_ai.tool.call.arguments": json.dumps(args), "session.id": sid, "service.name": "claims-agent-gcp"}})
        cur = end
    end = cur + timedelta(milliseconds=950 + 120 * n)
    gspans.append({"trace_id": tid, "spanId": str(R.randint(10**15, 10**16)), "parentSpanId": root, "name": "call_llm", "startTime": cur.isoformat(),
                   "endTime": end.isoformat(), "labels": {"gen_ai.operation.name": "generate_content", "gen_ai.request.model": "gemini-flash",
                                                          "gen_ai.usage.input_tokens": "1710", "gen_ai.usage.output_tokens": "76", "session.id": sid,
                                                          "service.name": "claims-agent-gcp"}})
    gspans.append({"trace_id": tid, "spanId": root, "name": "invoke_agent claims_agent", "startTime": t.isoformat(),
                   "endTime": (end + timedelta(milliseconds=30)).isoformat(), "labels": labels})
    bq.append({"request_id": f"bq-{n}", "logging_time": (end - timedelta(milliseconds=100)).isoformat(), "model": "gemini-flash",
               "full_request": json.dumps({"contents": [{"role": "user", "parts": [{"text": user}]}]}),
               "full_response": json.dumps({"candidates": [{"content": {"role": "model", "parts": [{"text": reply}]}}],
                                            "usageMetadata": {"promptTokenCount": 1710, "candidatesTokenCount": 76}})})
    gsessions.append({"session_id": sid, "author": "user", "timestamp": t.isoformat(), "content": {"role": "user", "parts": [{"text": user}]}})
    gsessions.append({"session_id": sid, "author": "claims_agent", "timestamp": end.isoformat(), "content": {"role": "model", "parts": [{"text": reply}]}})
w(FX / "gcp" / "cloud_trace_spans.jsonl", gspans)
w(FX / "gcp" / "vertex_request_response.jsonl", bq)
w(FX / "gcp" / "agent_engine_session_events.jsonl", gsessions)
w(FX / "gcp" / "cloud_monitoring.json", [{"model_invocation_count": 912, "token_count": 1488210, "model_invocation_latencies": 1405.2}], jsonl=False)
print("wrote", sorted(str(p.relative_to(HERE)) for p in list(DATA.glob("*")) + list(FX.rglob("*.json*"))))
