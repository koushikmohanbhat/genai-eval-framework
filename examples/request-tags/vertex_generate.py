"""One labelled Vertex generateContent call, to check that trace ids reach the request-response logs in BigQuery.

    pip install google-genai opentelemetry-sdk
    export GCP_PROJECT=... GCP_LOCATION=... VERTEX_MODEL=...   # request-response logging on for this model
    python examples/request-tags/vertex_generate.py
    # logging is sampled: set the logging sampling rate to 1.0 for this check. Then (sources.yaml with a
    # gcp_vertex_request_response join, no `fixture:`):
    genai-evals sources check sources.yaml --trace <printed trace id>

This script's span isn't exported anywhere, so `--trace` reports "the payload log carries it" when the tag reached
the log (exit 0), or "in neither" when it didn't (exit 1). Run it inside the instrumented agent (exported spans) to
test the whole join, which `--trace` then reports as "matched by trace_id".

In your agent, do the same on every model call: pass the current span's trace id and session id as request `labels`.
Label keys must start with a lowercase letter; keys and values use lowercase letters, digits, `_` and `-` (up to 63
characters), so a 32-hex-character trace id fits. Labels are a documented request field (they feed billing
reports). That the request-response log keeps them inside `full_request` is what this script checks: it is not
yet verified against a live project (docs/limitations.md). If they don't appear, map trace and session columns
with `columns:` on the source instead.
"""
from __future__ import annotations

import os
import uuid


def current_trace_id() -> str:
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except ImportError:
        pass
    return uuid.uuid4().hex


def main() -> None:
    from google import genai
    from google.genai import types
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        trace.set_tracer_provider(TracerProvider())
        span_cm = trace.get_tracer("request-tags").start_as_current_span("chat")
    except ImportError:
        from contextlib import nullcontext
        span_cm = nullcontext()
    with span_cm:
        trace_id = current_trace_id()
        session_id = os.environ.get("SESSION_ID", f"tag-check-{uuid.uuid4().hex[:8]}")
        client = genai.Client(vertexai=True, project=os.environ["GCP_PROJECT"], location=os.environ["GCP_LOCATION"])
        resp = client.models.generate_content(
            model=os.environ["VERTEX_MODEL"], contents="Reply with the word ok.",
            config=types.GenerateContentConfig(labels={"trace_id": trace_id, "session_id": session_id}),
        )
    print("reply:", resp.text)
    print("trace id:", trace_id)
    print("session id:", session_id)
    print(f"next: genai-evals sources check sources.yaml --trace {trace_id}")


if __name__ == "__main__":
    main()
