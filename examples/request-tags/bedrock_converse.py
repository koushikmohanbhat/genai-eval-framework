"""One tagged Bedrock Converse call, to check that trace ids reach the model invocation logs.

    pip install boto3 opentelemetry-sdk
    export AWS_REGION=... BEDROCK_MODEL_ID=...       # model invocation logging on, text delivery to CloudWatch Logs
    python examples/request-tags/bedrock_converse.py
    # wait a minute for delivery, then (sources.yaml with an aws_bedrock_invocation_logs join, no `fixture:`):
    genai-evals sources check sources.yaml --trace <printed trace id>

This script's span isn't exported anywhere, so `--trace` reports "the payload log carries it" when the tag reached
the log (exit 0), or "in neither" when it didn't (exit 1). Run it inside the instrumented agent (exported spans) to
test the whole join, which `--trace` then reports as "matched by trace_id".

In your agent, do the same on every model call: read the current span's trace id and session id and pass them as
`requestMetadata`. Bedrock records the map under the log entry's top-level `requestMetadata` field, which the
`aws_bedrock_invocation_logs` source reads. Limits: at most 16 entries, keys and values up to 256 characters.
"""
from __future__ import annotations

import os
import uuid

import boto3


def current_ids() -> tuple[str, str | None]:
    """The active OTel trace id (32 hex chars, as spans store it), or a fresh one when OTel isn't set up."""
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            return format(ctx.trace_id, "032x"), None
    except ImportError:
        pass
    return uuid.uuid4().hex, None


def main() -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        trace.set_tracer_provider(TracerProvider())
        span_cm = trace.get_tracer("request-tags").start_as_current_span("chat")
    except ImportError:
        from contextlib import nullcontext
        span_cm = nullcontext()
    with span_cm:
        trace_id, _ = current_ids()
        session_id = os.environ.get("SESSION_ID", f"tag-check-{uuid.uuid4().hex[:8]}")
        client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
        resp = client.converse(
            modelId=os.environ["BEDROCK_MODEL_ID"],
            messages=[{"role": "user", "content": [{"text": "Reply with the word ok."}]}],
            requestMetadata={"trace_id": trace_id, "session_id": session_id},
        )
    print("reply:", resp["output"]["message"]["content"][0]["text"])
    print("trace id:", trace_id)
    print("session id:", session_id)
    print(f"next: genai-evals sources check sources.yaml --trace {trace_id}")


if __name__ == "__main__":
    main()
