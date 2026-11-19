"""Connect an existing agent in minutes: one decorator, one small HTTP contract.

Wrap the function that answers a user message, and decorate the tools it calls:

    import genai_evals

    @genai_evals.tool                      # records each call (name, arguments, result) for the graders
    def lookup_order(order_id: str) -> dict: ...

    @genai_evals.agent
    def support_agent(message: str, session: dict, user: str | None = None) -> str:
        ...                                # your LangGraph / LlamaIndex / custom code; session persists per conversation
        return reply

Then either point a Python target at it (`kind: python, callable: "my_agent:support_agent"`), or serve it over HTTP
with the genai-evals contract and point an HTTP target at it (`kind: http, options: {url: ..., contract: v1}`):

    genai_evals.serve(support_agent, port=8080)          # or mount `support_agent.handle(request_json)` in FastAPI

The contract (POST, JSON):
    request:  {"message": str, "session_id": str, "turn": int, "case_id": str, "user": str | null}
    response: {"output": str, "tool_calls": [{"name", "args", "result"}], "state": {...}, "session_id": str,
               "usage": {"input_tokens", "output_tokens"}, "cost_usd": float, "trace_id": str}   (all but output optional)

If the agent can't report its tool calls (no @tool, no tool_calls in the response), only its replies are graded:
final_state, tool and trajectory graders are skipped for it, not passed.
"""
from __future__ import annotations

import functools
import inspect
import json
import threading
import uuid
from contextvars import ContextVar
from typing import Any, Callable

CONTRACT = "genai-evals/agent-v1"
_CALLS: ContextVar[list[dict[str, Any]] | None] = ContextVar("genai_evals_tool_calls", default=None)
_STATE: ContextVar[dict[str, Any] | None] = ContextVar("genai_evals_state", default=None)


def tool(fn: Callable | None = None, *, name: str | None = None):
    """Record each call of a tool function while an @agent turn runs (no effect outside one)."""
    def wrap(f):
        tname = name or f.__name__

        @functools.wraps(f)
        def inner(*a, **kw):
            calls = _CALLS.get()
            try:
                res = f(*a, **kw)
            except Exception as e:
                if calls is not None:
                    calls.append({"name": tname, "args": _args(f, a, kw), "error": f"{type(e).__name__}: {e}"})
                raise
            if calls is not None:
                calls.append({"name": tname, "args": _args(f, a, kw), "result": _jsonable(res)})
            return res

        @functools.wraps(f)
        async def ainner(*a, **kw):
            calls = _CALLS.get()
            res = await f(*a, **kw)
            if calls is not None:
                calls.append({"name": tname, "args": _args(f, a, kw), "result": _jsonable(res)})
            return res
        return ainner if inspect.iscoroutinefunction(f) else inner
    return wrap(fn) if fn else wrap


def report_state(**values: Any) -> None:
    """Report the business outcome of this conversation (e.g. refunds=1), compared by the final_state grader."""
    st = _STATE.get()
    if st is not None:
        st.update(values)


def _args(f, a, kw) -> dict[str, Any]:
    try:
        b = inspect.signature(f).bind_partial(*a, **kw)
        return {k: _jsonable(v) for k, v in b.arguments.items() if k not in ("self", "state")}
    except TypeError:
        return {"args": [_jsonable(x) for x in a], **{k: _jsonable(v) for k, v in kw.items()}}


def _jsonable(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except TypeError:
        return getattr(v, "model_dump", lambda: str(v))()


class Agent:
    """The wrapped agent: a Python-target callable (one call per user turn), and a contract handler for HTTP."""
    per_turn = True

    def __init__(self, fn: Callable):
        self.fn = fn
        self.sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        functools.update_wrapper(self, fn)
        params = inspect.signature(fn).parameters
        self._kw = {p for p in ("session", "user", "history", "turn", "session_id") if p in params}

    async def _turn(self, message: str, session: dict, **extra: Any) -> dict[str, Any]:
        calls: list[dict[str, Any]] = []
        state: dict[str, Any] = session.setdefault("_genai_evals_state", {})
        t1, t2 = _CALLS.set(calls), _STATE.set(state)
        try:
            kw = {k: v for k, v in {"session": session, **extra}.items() if k in self._kw}
            res = self.fn(message, **kw)
            if inspect.isawaitable(res):
                res = await res
        finally:
            _CALLS.reset(t1)
            _STATE.reset(t2)
        out = res if isinstance(res, dict) else {"output": res if isinstance(res, str) else json.dumps(_jsonable(res))}
        out.setdefault("tool_calls", calls)
        if state and "state" not in out:
            out["state"] = dict(state)
        return out

    # Python target: fn(case, ctx), called once per user turn (per_turn = True)
    async def __call__(self, case: Any, ctx: dict[str, Any]) -> dict[str, Any]:
        meta = getattr(case, "metadata", None) or {}
        session = ctx.get("session") if ctx.get("session") is not None else {}
        r = await self._turn(case.input_text(), session, user=meta.get("user") or meta.get("customer"),
                             history=ctx.get("history", []), turn=ctx.get("turn", 1), session_id=ctx.get("session_id"))
        return {"output": r.get("output", ""), "trajectory": r.get("tool_calls", []), "final_state": r.get("state") or {},
                **({"usage": r["usage"]} if r.get("usage") else {}), **({"cost_usd": r["cost_usd"]} if "cost_usd" in r else {})}

    # HTTP contract: one request -> one response. Flask / sync code: handle(); FastAPI: await ahandle(); or serve()
    async def ahandle(self, request: dict[str, Any]) -> dict[str, Any]:
        sid = str(request.get("session_id") or uuid.uuid4().hex)
        with self._lock:
            session = self.sessions.setdefault(sid, {})
        r = await self._turn(str(request.get("message", "")), session, user=request.get("user"),
                             turn=request.get("turn", 1), session_id=sid, history=request.get("history", []))
        r["session_id"] = sid
        return r

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        return asyncio.run(self.ahandle(request))


def agent(fn: Callable) -> Agent:
    """Decorate the function that answers one user message: fn(message, session=..., user=..., history=..., turn=...)
    returning the reply (str) or a dict with output / tool_calls / state / usage / cost_usd."""
    return Agent(fn)


def serve(a: Agent | Callable, host: str = "127.0.0.1", port: int = 8080, block: bool = True):
    """Serve an @agent over HTTP with the genai-evals contract (stdlib only; for evals, not production traffic)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    ag = a if isinstance(a, Agent) else Agent(a)

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                body, code = json.dumps(ag.handle(req), default=str).encode(), 200
            except Exception as e:  # noqa: BLE001 - report the agent's error to the eval, which records it
                body, code = json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), 500
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    if block:
        srv.serve_forever()
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
