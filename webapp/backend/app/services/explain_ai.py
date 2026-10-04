"""AI-written explanations for the code explainer (normal or "simpler").

The pattern-based explanations (explain.py) always come first and never
need AI. This module asks the AI to rewrite them for one file or function,
in the background, so the page never waits on it:

    status("…::run_choice", "simpler")  ->  {"status": "thinking"}
    … a moment later …                  ->  {"status": "done", "blocks": {"n3": "…"}}

Answers are cached by llm.py, so asking again (even by another student
with the same code) costs nothing.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from .llm import AIUnavailable, FakeProvider, llm, ui_status

LEVELS = {
    "normal": ("Explain each block in 1 or 2 short sentences for a first-year computer science "
               "student. Say what it does and why, in simple English. Keep names of variables "
               "and functions in `backticks`."),
    "simpler": ("Explain each block even more simply, as if to a 12-year-old who has never "
                "programmed: one short sentence, everyday words, and a small comparison from daily "
                "life when it helps (a box, a recipe, a menu). Keep names in `backticks`."),
}
SYSTEM = ("You are a patient teacher who explains code to beginners. Only describe what the code "
          "really does. Never invent behaviour you cannot see.")
MAX_CODE_LINES = 220

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="explain")
_jobs: dict[tuple, Future] = {}
_lock = threading.Lock()


def status(project_id: str, view: dict, level: str) -> dict:
    """Start (or check) the AI explanation of one view."""
    key = (project_id, view.get("id") or view["path"], level)
    with _lock:
        job = _jobs.get(key)
        if job is None or (job.done() and job.exception() is not None):
            job = _pool.submit(_run, project_id, view, level)
            _jobs[key] = job
    try:
        result = job.result(timeout=0.05)          # cached answers come back at once
    except TimeoutError:
        return {"status": "thinking"}
    except AIUnavailable as e:
        with _lock:
            _jobs.pop(key, None)                   # allow a retry later
        return {"status": ui_status(e.reason), "message": str(e)}
    except Exception:
        with _lock:
            _jobs.pop(key, None)
        return {"status": "error", "message": AIUnavailable.MESSAGES["error"]}
    return {"status": "done", "blocks": result}


def _run(project_id: str, view: dict, level: str) -> dict[str, str]:
    answer = llm.ask(f"explain_{level}", SYSTEM, prompt(view, level), tier="fast", json=True,
                     max_tokens=1800, project_id=project_id)
    return parse(answer, {b["id"] for b in view["blocks"]})


def prompt(view: dict, level: str) -> str:
    text = view["source_text"].splitlines()
    first, last = (view["start"], view["end"]) if view["kind"] == "function" else (1, len(text))
    last = min(last, first + MAX_CODE_LINES - 1)
    numbered = "\n".join(f"{i:>4} | {text[i - 1]}" for i in range(first, min(last, len(text)) + 1))
    what = f"the function `{view['name']}` in {view['path']}" if view["kind"] == "function" else f"the file {view['path']}"
    lines = [f"Here is {what} ({view['language']}), with line numbers:", "", numbered, "",
             "It is cut into these blocks (id: lines - what it is):"]
    for b in view["blocks"]:
        if b["lines"][0] and b["lines"][0] <= last:
            lines.append(f"{b['id']}: lines {b['lines'][0]}-{b['lines'][1]} - {b['kind']}: {b['label'].splitlines()[0][:80]}")
    lines += ["", LEVELS[level], "",
              'Reply with JSON only: {"blocks": {"<id>": "<explanation>", ...}} with every id listed above.']
    return "\n".join(lines)


def parse(answer: str, ids: set[str]) -> dict[str, str]:
    try:
        data = json.loads(answer)
    except ValueError:
        return {}
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, dict):
        return {}
    out = {}
    for k, v in blocks.items():
        if k in ids and isinstance(v, str) and v.strip():
            text = " ".join(v.split())
            out[k] = text if len(text) <= 500 else text[:499] + "…"
    return out


# Fake AI for tests and offline development: answers from the block list.
def _fake(level: str):
    def handler(system: str, prompt_text: str) -> str:
        import re
        ids = re.findall(r"^(\w+): lines \d+-\d+ - (\w+): (.*)$", prompt_text, re.M)
        word = "Simply put" if level == "simpler" else "In this step"
        return json.dumps({"blocks": {i: f"{word}: this {kind} block runs `{label.strip()[:40]}`. "
                                         "(Written by the fake test AI.)" for i, kind, label in ids}})
    return handler


FakeProvider.handlers.update({"explain_normal": _fake("normal"), "explain_simpler": _fake("simpler")})
