"""AI notes: plain-English summaries of files and functions, and a short
overview of the whole project, written for first-year students.

Runs AFTER the deterministic analysis, in the background. The dashboard is
usable straight away with summaries taken from the code's own comments; the
AI notes replace them as they arrive. If the AI is off or out of quota for
today, nothing breaks - the code-based summaries simply stay.

Cost control: files are sent in batches (one request per batch), only the
most connected files are summarised, code excerpts are trimmed, and every
answer is cached, so analysing the same code again costs nothing.
"""

from __future__ import annotations

import json
import os
import re

from reposage.cache import write_json_atomic

from .. import config
from .llm import AIUnavailable, FakeProvider, llm

SYSTEM = ("You explain code to first-year computer science students. Use simple, friendly "
          "English and short sentences. Avoid jargon, or explain it in a few words. "
          "Only describe what is really in the code; never guess about code you can't see.")

EXCERPT_LINES = 60
EXCERPT_CHARS = 2500


def notes_path(project_dir: str) -> str:
    return os.path.join(project_dir, "ai.json")


def load_notes(project_dir: str) -> dict:
    try:
        with open(notes_path(project_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"files": {}, "functions": {}, "overview": None}


def write_notes(project_dir: str, overview: dict, on_status=lambda s: None) -> str:
    """Fill ai.json; returns the final AI status: done | resting | off | busy."""
    notes = load_notes(project_dir)
    src = os.path.join(project_dir, "src")
    files = sorted(overview["files"], key=lambda f: (-f["connections"], f["path"]))
    files = [f for f in files if f["functions"] or f["lines"]][: config.AI_SUMMARY_MAX_FILES]
    todo = [f for f in files if f["path"] not in notes["files"]]
    on_status("thinking")
    try:
        for i in range(0, len(todo), config.AI_SUMMARY_BATCH_FILES):
            batch = todo[i:i + config.AI_SUMMARY_BATCH_FILES]
            answer = llm.ask("file_summaries", SYSTEM, summary_prompt(batch, src),
                             tier="fast", json=True, max_tokens=2000,
                             project_id=os.path.basename(project_dir))
            merge_summaries(notes, batch, answer)
            write_json_atomic(notes_path(project_dir), notes)
        if notes.get("overview") is None:
            answer = llm.ask("overview", SYSTEM, overview_prompt(overview, notes),
                             tier="fast", json=True, max_tokens=600,
                             project_id=os.path.basename(project_dir))
            data = _loads(answer)
            text = _clean(data.get("overview", ""), 900)
            if text:
                notes["overview"] = {"text": text,
                                     "start_reason": _clean(data.get("start_here", ""), 300)}
            write_json_atomic(notes_path(project_dir), notes)
    except AIUnavailable as e:
        return {"daily_limit": "resting", "off": "off"}.get(e.reason, "busy")
    return "done"


def summary_prompt(batch: list[dict], src: str) -> str:
    parts = ["Explain each file below. Reply with JSON exactly like:",
             '{"files": [{"path": "...", "summary": "one sentence, at most 25 words, '
             'saying what the file is for", "functions": {"name": "what it does, at most 15 words"}}]}',
             "Include every file and every function listed.", ""]
    for f in batch:
        parts.append(f"### FILE: {f['path']}  ({f['language']})")
        if f["summary_source"] == "code" and not f["summary"].startswith("Defines "):
            parts.append(f"top comment: {f['summary']}")
        for fn in f["functions"]:
            parts.append(f"- function {fn['name']} (line {fn['line']})")
        parts.append("code:")
        parts.append(_excerpt(os.path.join(src, f["path"])))
        parts.append("")
    return "\n".join(parts)


def overview_prompt(overview: dict, notes: dict) -> str:
    lines = [f"Project: {overview['project']}",
             f"Languages: {', '.join(l['name'] for l in overview['languages'])}",
             f"{overview['stats']['files']} files, {overview['stats']['functions']} functions.",
             f"The program starts in: {overview['start_here']['file']}", "", "Files:"]
    for f in overview["files"][:40]:
        lines.append(f"- {f['path']}: {notes['files'].get(f['path'], f['summary'])}")
    lines += ["", "Reply with JSON: {\"overview\": \"3 or 4 short sentences: what this program "
              "does and how its files work together\", \"start_here\": \"one sentence: why a "
              "student should start reading at the starting file\"}"]
    return "\n".join(lines)


def merge_summaries(notes: dict, batch: list[dict], answer: str) -> None:
    """Keep only answers for files and functions we actually asked about."""
    wanted = {f["path"]: {fn["name"] for fn in f["functions"]} for f in batch}
    for item in _loads(answer).get("files", []):
        if not isinstance(item, dict) or item.get("path") not in wanted:
            continue
        path = item["path"]
        summary = _clean(item.get("summary", ""), 300)
        if summary:
            notes["files"][path] = summary
        funcs = item.get("functions") or {}
        if isinstance(funcs, dict):
            for name, text in funcs.items():
                if name in wanted[path] and _clean(text, 200):
                    notes["functions"][f"{path}::{name}"] = _clean(text, 200)


def apply_notes(overview: dict, notes: dict) -> dict:
    """Overview + AI notes, for the API. AI text wins over code-based text."""
    for f in overview["files"]:
        if f["path"] in notes.get("files", {}):
            f["summary"], f["summary_source"] = notes["files"][f["path"]], "ai"
        for fn in f["functions"]:
            text = notes.get("functions", {}).get(f"{f['path']}::{fn['name']}")
            if text:
                fn["summary"], fn["summary_source"] = text, "ai"
    if notes.get("overview"):
        overview["overview"] = {"text": notes["overview"]["text"], "source": "ai"}
        if notes["overview"].get("start_reason") and overview["start_here"]["steps"]:
            overview["start_here"]["steps"][0]["reason"] = notes["overview"]["start_reason"]
    return overview


# ---------------------------------------------------------------------------

def _excerpt(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read(EXCERPT_CHARS * 2).splitlines()[:EXCERPT_LINES]
    except OSError:
        return ""
    text = "\n".join(lines)
    return text[:EXCERPT_CHARS] + ("\n..." if len(text) > EXCERPT_CHARS else "")


def _loads(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean(text, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Fake AI (tests and offline development): answers built from the prompt
# itself, so the whole flow can be tried without any API key.
# ---------------------------------------------------------------------------

def _fake_summaries(system: str, prompt: str) -> str:
    files = []
    for block in re.split(r"^### FILE: ", prompt, flags=re.M)[1:]:
        path = block.split("  (", 1)[0].strip()
        top = re.search(r"^top comment: (.+)$", block, re.M)
        funcs = re.findall(r"^- function (\w+)", block, re.M)
        summary = top.group(1) if top else f"Holds {len(funcs)} functions used by this program."
        files.append({"path": path, "summary": summary,
                      "functions": {name: f"Does the {name.replace('_', ' ')} step." for name in funcs}})
    return json.dumps({"files": files})


def _fake_overview(system: str, prompt: str) -> str:
    project = re.search(r"^Project: (.+)$", prompt, re.M).group(1)
    start = re.search(r"^The program starts in: (.+)$", prompt, re.M).group(1)
    return json.dumps({"overview": f"{project} is a small program. It starts in {start}, which "
                                   "uses the other files to do its job. (Written by the fake test AI.)",
                       "start_here": f"{start} is where the program begins, so you can follow it step by step."})


FakeProvider.handlers.update({"file_summaries": _fake_summaries, "overview": _fake_overview})
