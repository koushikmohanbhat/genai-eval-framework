"""Exposing an @genai_evals.agent through the customer's own FastAPI app (illustrative; needs fastapi).

Mount one extra route next to the production ones, behind the same auth; the HTTP target then uses
`options: {url: "https://.../evals/chat", contract: v1, headers_env: {Authorization: EVAL_API_TOKEN}}`.
"""
from fastapi import FastAPI
from plain_agent import support_agent

app = FastAPI()


@app.post("/evals/chat")
async def evals_chat(req: dict) -> dict:
    return await support_agent.ahandle(req)
