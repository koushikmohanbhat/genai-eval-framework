"""Record the 60-second terminal demo: init -> run -> baseline -> experiment -> a gate blocking a bad prompt.

    python scripts/record_terminal.py            # writes docs/img/terminal-demo.cast and docs/img/terminal-demo.svg

Every command really runs (offline: the tool-agent pack's simulated agent and mock judges, no keys) and its real
output is recorded. Only the pacing is synthetic: typing speed and pauses are set so the whole thing plays in about a
minute. The SVG is rendered from the cast with svg-term-cli (needs Node: `npx svg-term-cli`); the cast also plays in
docs/demo/terminal.html with asciinema-player (published by scripts/build_demo_site.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAST = ROOT / "docs" / "img" / "terminal-demo.cast"       # build_demo_site.py publishes it with a player page
SVG = ROOT / "docs" / "img" / "terminal-demo.svg"
WIDTH, HEIGHT = 110, 30

STEPS = [
    "# 1. Start from a pack: a tool-using support agent (offline: simulated agent, mock judges)",
    "genai-evals init support-eval --pack tool-agent | head -3",
    "cd support-eval",
    "# 2. Today's agent: every release gate passes. Make it the baseline",
    "genai-evals run suite.yaml --out out/prod --no-html | head -9",
    "genai-evals baseline promote out/prod/result.json --out baseline.json",
    "# 3. Experiment: a cheaper model and a shorter prompt, same 24 conversations x 3 repeats",
    "genai-evals experiment run experiment.yaml --out out/exp | grep '| regresses'",
    "# 4. Ship the shorter prompt anyway? (it lost the refund-limit rule)",
    "echo 'targets: [{name: agent, kind: python, callable: \"sim_system:agent\", options: {refund_limit: 100000}}]' "
    "> targets/candidate.yaml",
    "EVAL_TARGETS=targets/candidate.yaml genai-evals run suite.yaml --baseline baseline.json --no-html",
    "echo \"exit code: $?\"   # non-zero: CI stops the release",
]


def run(cmd: str, cwd: Path, env: dict, state: dict) -> tuple[str, Path]:
    """Run exactly what is shown on screen (in bash, so pipes and $? behave as they would for the viewer)."""
    if cmd.startswith("#"):
        return "", cwd
    if cmd.startswith("cd "):
        return "", cwd / cmd[3:].strip()
    real = cmd.replace("$?", state.get("rc", "0")) if cmd.startswith("echo \"exit code") else cmd
    r = subprocess.run(["bash", "-c", real], cwd=cwd, env=env, capture_output=True, text=True)
    state["rc"] = str(r.returncode)
    return (r.stdout + r.stderr).rstrip("\n"), cwd


def main() -> int:
    work = Path(tempfile.mkdtemp())
    env = dict(os.environ, GENAI_EVALS_TOKEN_KEY="terminal-demo", COLUMNS=str(WIDTH), NO_COLOR="1",
               PATH=f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}")
    env.pop("EVAL_TARGETS", None)
    events, t = [], 0.4
    cwd = work

    def emit(text: str, dt: float) -> None:
        nonlocal t
        events.append([round(t, 3), "o", text])
        t += dt
    state: dict = {}
    try:
        for cmd in STEPS:
            comment = cmd.startswith("#")
            emit("\x1b[32m$\x1b[0m ", 0.25)
            for ch in cmd:                         # typing
                emit(("\x1b[2m" + ch + "\x1b[0m") if comment else ch, 0.016 if comment else 0.028)
            emit("\r\n", 0.35)
            out, cwd = run(cmd, cwd, env, state)
            for line in out.splitlines():
                line = line.replace(str(work) + "/", "")
                colour = "\x1b[31m" if ("FAIL" in line or "regresses" in line) else "\x1b[32m" if "PASSED" in line else ""
                emit((colour + line + "\x1b[0m" if colour else line) + "\r\n", 0.06)
            t += 1.6 if out else (0.5 if comment else 0.3)
        emit("\x1b[32m$\x1b[0m ", 2.5)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    CAST.parent.mkdir(parents=True, exist_ok=True)
    header = {"version": 2, "width": WIDTH, "height": HEIGHT, "timestamp": 0,
              "title": "genai-evals: init -> run -> experiment -> gate blocked", "env": {"TERM": "xterm-256color"}}
    CAST.write_text("\n".join([json.dumps(header)] + [json.dumps(e) for e in events]) + "\n", encoding="utf-8")
    print(f"wrote {CAST.relative_to(ROOT)} ({t:.0f} s)")
    npx = shutil.which("npx")
    if not npx:
        print("npx not found: skipping the SVG (install Node, then: npx svg-term-cli --in docs/img/terminal-demo.cast ...)")
        return 0
    SVG.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([npx, "-y", "svg-term-cli@2.1.1", "--in", str(CAST), "--out", str(SVG), "--window",
                    "--width", str(WIDTH), "--height", str(HEIGHT), "--padding", "12"], check=True)
    print(f"wrote {SVG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
