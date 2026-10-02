"""The "Ask RepoSage" agent: answers a student's question about their code.

How it works (agentic RAG, read-only):
  1. A free first step, no AI: search the code for the question's words
     (TF-IDF from the reposage engine, plus the AI summaries we have).
  2. The AI then picks the next tool, one at a time, looking at what it has
     seen so far:  search_code, read_function, read_file, find_callers,
     find_callees, get_flowchart, project_overview.
  3. When it knows enough (or after CHAT_MAX_AI_CALLS calls), it writes a
     short answer in simple English with [file:line] citations, plus three
     follow-up questions.

Every step is shown live to the student ("Searched 'divide' -> read calc.c
-> checked run_choice"). The tools only READ the uploaded files and the
code graph; nothing is ever run.

Without AI (switched off, busy, or today's free limit reached) the same
steps run without it, and the answer is the best matching code with its
explanation and flowchart.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from .. import config  # noqa: F401  (puts the reposage engine on sys.path)
from reposage.cache import write_json_atomic
from reposage.rag import STOP_WORDS, Index

from . import ai_notes, analyze, code_view
from .flowchart import build_flowchart
from .llm import AIUnavailable, FakeProvider, llm

MAX_AI_CALLS = int(os.environ.get("CHAT_MAX_AI_CALLS", 4))        # free tier: 4 calls per question
CHAT_TIER = os.environ.get("CHAT_MODEL_TIER", "smart")
MAX_QUESTION = 500
TOOL_TEXT_LIMIT = 2600              # characters of each tool result sent to the AI
CODE_LINES_LIMIT = 70

# Beginner words -> code words, so "loop" finds `for`/`while` and "menu" finds `switch`.
SYNONYMS = {
    "loop": "for while repeat", "loops": "for while repeat", "repeat": "for while",
    "menu": "switch case choice menu", "choice": "switch case", "options": "switch case",
    "print": "printf print println puts log", "show": "printf print println display",
    "display": "printf print println", "input": "scanf input readline nextInt read",
    "type": "scanf input", "types": "scanf input", "read": "scanf input read load",
    "save": "save write store dump", "store": "save write store", "load": "load read open",
    "divide": "divide division", "add": "add sum plus", "sum": "sum add total",
    "average": "average mean", "biggest": "max largest", "largest": "max largest",
    "password": "password hash", "log": "login", "logs": "login", "login": "login auth",
    "check": "check verify if", "error": "error except catch raise",
}

SYSTEM = (
    "You are RepoSage, a kind tutor for first-year programming students. You answer questions about "
    "the student's own code by using read-only tools. Rules: (1) Use ONLY facts you saw in tool "
    "results; if the code doesn't answer the question, say so honestly. (2) Very simple English, "
    "short sentences, no jargon (or explain it in a few words). (3) Cite the code for every claim "
    "like [calc.c:22] or [calc.c:22-26], using real file names and line numbers from the tool "
    "results. (4) Reply with ONE JSON object only.")

TOOLS_HELP = """Tools (all read-only):
- search_code {"query": "words"}: find functions about these words
- read_function {"name": "divide"}: the code of a function, with line numbers
- read_file {"path": "main.c", "start": 1, "end": 40}: some lines of a file
- find_callers {"name": "divide"}: which functions call it
- find_callees {"name": "run_choice"}: which functions it calls
- get_flowchart {"name": "main"}: its steps as flowchart boxes (decisions, loops, menu cases)
- project_overview {}: what the whole project is and where it starts

Reply with ONE of:
{"thought": "why, in at most 12 words", "action": "<tool name>", "input": {...}}
{"thought": "...", "action": "answer", "answer": "<your answer with [file:line] citations>",
 "followups": ["<question 1>", "<question 2>", "<question 3>"]}"""

ANSWER_STYLE = {
    "normal": "Answer in 2-5 short sentences (or up to 5 numbered steps for a process). Name the "
              "functions in `backticks`. Cite lines like [file:line].",
    "simpler": "Answer as if to a 12-year-old: 2-3 very short sentences, everyday words, one small "
               "comparison from daily life. Still cite lines like [file:line].",
}

ICONS = {"search_code": "search", "read_function": "read", "read_file": "read", "find_callers": "callers",
         "find_callees": "callees", "get_flowchart": "flowchart", "project_overview": "overview"}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class Toolbox:
    """Read-only tools over one project's files and code graph."""

    def __init__(self, project_dir: str):
        self.dir = project_dir
        self.src = os.path.join(project_dir, "src")
        self.graph, self.overview, self.nodes = code_view.load(project_dir)
        notes = ai_notes.load_notes(project_dir)
        self.known = code_view.known_summaries(self.overview, notes)
        graph = copy.deepcopy(self.graph)
        for n in graph["nodes"]:                        # search also matches AI summaries
            summary = notes.get("functions", {}).get(f"{n.get('path')}::{n.get('name')}") \
                or notes.get("files", {}).get(n["id"])
            if summary:
                n["summary"] = summary
        self.index = Index(self.src, graph, include_tests=True)
        self.funcs = [n for n in self.graph["nodes"] if n["type"] in ("function", "method")]
        self.lines_cache: dict[str, list[str]] = {}
        self.seen: set[tuple[str, int]] = set()        # (file, line) the agent has looked at

    # -- helpers
    def lines(self, path: str) -> list[str]:
        if path not in self.lines_cache:
            try:
                self.lines_cache[path] = code_view.read_source(self.dir, path).decode("utf-8", "replace").splitlines()
            except code_view.ViewError:
                self.lines_cache[path] = []
        return self.lines_cache[path]

    def numbered(self, path: str, start: int, end: int) -> str:
        lines = self.lines(path)
        end = min(end, len(lines), start + CODE_LINES_LIMIT - 1)
        for i in range(start, end + 1):
            self.seen.add((path, i))
        return "\n".join(f"{i:>4} | {lines[i - 1]}" for i in range(start, end + 1))

    def find(self, name: str) -> Optional[dict]:
        """A function by id, "file::name", "Class.method" or plain name."""
        name = (name or "").strip().strip("`").removesuffix("()").strip()
        if not name:
            return None
        if name in self.nodes and self.nodes[name]["type"] in ("function", "method"):
            return self.nodes[name]
        short = name.split("::")[-1]
        hits = [n for n in self.funcs if n["id"].split("::", 1)[1].split("#")[0] == short or n["name"] == short]
        if "::" in name:
            hits = [n for n in hits if n["path"].endswith(name.split("::")[0])] or hits
        if not hits:
            return None
        # several with that name (e.g. two main()): the best search match first
        sims = self.index.similarities(name)
        return sorted(hits, key=lambda n: (-sims.get(n["id"], 0), n["id"]))[0]

    def ref(self, n: dict) -> dict:
        return {"file": n["path"], "start": n["start_line"], "end": n["end_line"], "label": f"{n['name']}()",
                "function_id": n["id"]}

    # -- tools: each returns (text for the AI, step for the student)
    def search_code(self, query: str) -> tuple[str, dict]:
        query = (query or "").strip()[:200]
        words = query.lower().split()
        keywords = " ".join(w for w in (x.strip("?.,!'\"()`") for x in words) if w and w not in STOP_WORDS) or query
        expanded = query + " " + " ".join(_synonyms(w.strip("?.,!")) for w in words)
        results = self.index.search(expanded, k=6)
        top = results[0][0] if results else 0
        results = [(s, c) for s, c in results if s >= top * 0.3][:5]
        if not results:
            return (f'search_code("{query}"): nothing matched.',
                    {"tool": "search_code", "title": f'Searched "{keywords}"', "detail": "No matching code found.", "refs": []})
        out, refs = [], []
        for _, c in results:
            n = self.nodes[c.id]
            summary = c.summary or self.known.get(n["name"]) or analyze._first_sentence(n.get("doc", ""))
            out.append(f"- {c.qual}() in {c.path} lines {c.start}-{c.end}" + (f": {summary}" if summary else "")
                       + f" [id: {c.id}]")
            refs.append(self.ref(n))
        names = ", ".join(f"{r['label']}" for r in refs[:3])
        return (f'search_code("{query}") found:\n' + "\n".join(out),
                {"tool": "search_code", "title": f'Searched "{keywords}"', "detail": f"Found {names}", "refs": refs})

    def read_function(self, name: str) -> tuple[str, dict]:
        n = self.find(name)
        if n is None:
            return self.not_found("read_function", name)
        code = self.numbered(n["path"], n["start_line"], n["end_line"])
        more = "\n(… longer, cut here)" if n["end_line"] - n["start_line"] + 1 > CODE_LINES_LIMIT else ""
        return (f"read_function({n['name']}) - {n['path']} lines {n['start_line']}-{n['end_line']}:\n{code}{more}",
                {"tool": "read_function", "title": f"Read {n['name']}() in {n['path']}",
                 "detail": f"lines {n['start_line']}-{n['end_line']}", "refs": [self.ref(n)]})

    def read_file(self, path: str, start=1, end=None) -> tuple[str, dict]:
        path = (path or "").strip()
        match = next((p for p in self.graph["files"] if p == path or p.endswith("/" + path)), None)
        if match is None:
            return self.not_found("read_file", path)
        total = len(self.lines(match))
        try:
            start = max(1, int(start or 1))
            end = min(total, int(end or start + 39))
        except (TypeError, ValueError):
            start, end = 1, min(total, 40)
        code = self.numbered(match, start, end)
        return (f"read_file({match}, {start}-{end}):\n{code}",
                {"tool": "read_file", "title": f"Read {match}", "detail": f"lines {start}-{end}",
                 "refs": [{"file": match, "start": start, "end": end, "label": match}]})

    def find_callers(self, name: str) -> tuple[str, dict]:
        n = self.find(name)
        if n is None:
            return self.not_found("find_callers", name)
        callers = sorted({e["source"] for e in self.graph["edges"] if e["type"] == "calls" and e["target"] == n["id"]})
        nodes = [self.nodes[c] for c in callers if c in self.nodes]
        lines = [f"- {c['name']}() in {c['path']} lines {c['start_line']}-{c['end_line']}" for c in nodes]
        text = "\n".join(lines) or "- nobody in this project calls it (it may be the starting point)"
        shown = ", ".join(f"{c['name']}()" for c in nodes[:4]) or "nobody"
        return (f"find_callers({n['name']}):\n{text}",
                {"tool": "find_callers", "title": f"Checked who calls {n['name']}()", "detail": f"Called by {shown}",
                 "refs": [self.ref(c) for c in nodes[:5]]})

    def find_callees(self, name: str) -> tuple[str, dict]:
        n = self.find(name)
        if n is None:
            return self.not_found("find_callees", name)
        callees = sorted({e["target"] for e in self.graph["edges"] if e["type"] == "calls" and e["source"] == n["id"]})
        nodes = [self.nodes[c] for c in callees if c in self.nodes]
        lines = [f"- {c['name']}() in {c['path']} lines {c['start_line']}-{c['end_line']}" for c in nodes]
        text = "\n".join(lines) or "- it calls no other function of this project (only library ones, if any)"
        shown = ", ".join(f"{c['name']}()" for c in nodes[:5]) or "no project functions"
        return (f"find_callees({n['name']}):\n{text}",
                {"tool": "find_callees", "title": f"Checked what {n['name']}() calls", "detail": f"Calls {shown}",
                 "refs": [self.ref(c) for c in nodes[:5]]})

    def get_flowchart(self, name: str) -> tuple[str, dict]:
        n = self.find(name)
        if n is None:
            return self.not_found("get_flowchart", name)
        src = code_view.read_source(self.dir, n["path"])
        fc = build_flowchart(n["path"], src, n["start_line"], n["end_line"], n["name"], self.known)
        if fc is None:
            return self.not_found("get_flowchart", name)
        label = {b["id"]: b["label"].replace("\n", "; ") for b in fc["boxes"]}
        out = []
        for b in fc["boxes"]:
            nxt = [f"{e['label'] + ' -> ' if e['label'] else '-> '}{label[e['to']][:40]}"
                   for e in fc["edges"] if e["from"] == b["id"]]
            out.append(f"- [{b['kind']}] line {b['lines'][0]}: {label[b['id']][:60]}   ({'; '.join(nxt)})")
            self.seen.add((n["path"], b["lines"][0]))
        kinds = [b["kind"] for b in fc["boxes"]]
        parts = [_plural(kinds.count(k), w) for k, w in (("decision", "decision"), ("loop", "loop"), ("switch", "menu"))
                 if kinds.count(k)]
        return (f"get_flowchart({n['name']}) in {n['path']}:\n" + "\n".join(out[:40]),
                {"tool": "get_flowchart", "title": f"Looked at the flowchart of {n['name']}()",
                 "detail": ", ".join(parts) or _plural(len(kinds) - 2, "step") + ", no decisions", "refs": [self.ref(n)],
                 "function_id": n["id"]})

    def project_overview(self) -> tuple[str, dict]:
        ov = ai_notes.apply_notes(copy.deepcopy(self.overview), ai_notes.load_notes(self.dir))
        files = "\n".join(f"- {f['path']}: {f['summary']}" for f in ov["files"][:25])
        sh = ov["start_here"]
        start = f"{sh['file']}" + (f", function {sh['function']['name']}() line {sh['function']['line']}" if sh.get("function") else "")
        return (f"project_overview:\n{ov['overview']['text']}\nThe program starts in {start}.\nFiles:\n{files}",
                {"tool": "project_overview", "title": "Read the project overview",
                 "detail": f"{ov['stats']['files']} files, starts in {sh['file']}", "refs": []})

    def not_found(self, tool: str, name: str) -> tuple[str, dict]:
        return (f"{tool}({name}): not found. Use a name from the search results.",
                {"tool": tool, "title": f"Looked for {name or '?'}", "detail": "Not found in this project", "refs": []})

    def run(self, action: str, args: dict) -> tuple[str, dict]:
        args = args if isinstance(args, dict) else {}
        if action == "search_code":
            return self.search_code(str(args.get("query", "")))
        if action == "read_function":
            return self.read_function(str(args.get("name", "")))
        if action == "read_file":
            return self.read_file(str(args.get("path", "")), args.get("start", 1), args.get("end"))
        if action == "find_callers":
            return self.find_callers(str(args.get("name", "")))
        if action == "find_callees":
            return self.find_callees(str(args.get("name", "")))
        if action == "get_flowchart":
            return self.get_flowchart(str(args.get("name", "")))
        if action == "project_overview":
            return self.project_overview()
        return (f"Unknown tool {action!r}.", {"tool": "unknown", "title": f"Tried {action}", "detail": "", "refs": []})


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

CITE = re.compile(r"\[([\w./\\-]+\.\w+):(\d+)(?:\s*[-–]\s*(\d+))?\]")
GENERAL = re.compile(r"\b(program|project|app|application|whole|overall|start|starts|begin|main idea)\b", re.I)


class Agent:
    def __init__(self, project_dir: str, emit: Callable[[dict], None]):
        self.tools = Toolbox(project_dir)
        self.emit = emit
        self.steps: list[dict] = []
        self.transcript: list[str] = []
        self.calls = 0

    def step(self, text: str, step: dict, thought: str = "") -> None:
        if thought:
            step["thought"] = thought[:140]
        self.steps.append(step)
        self.transcript.append(text[:TOOL_TEXT_LIMIT])
        self.emit({"steps": self.steps})

    def run(self, question: str, context: dict, history: list[dict], project_id: str) -> dict:
        t = self.tools
        # 1. free steps (no AI): search, and the overview for general questions
        self.step(*t.search_code(question))
        hits = self.steps[0]["refs"]
        if GENERAL.search(question) or not hits:
            self.step(*t.project_overview())
        focus = context.get("fn") or ""
        if focus and re.search(r"\b(this|here|it)\b", question, re.I):
            self.step(*t.read_function(focus))

        if not llm.chain():
            return self.without_ai("off", question)
        # 2. the AI chooses tools until it can answer
        for i in range(MAX_AI_CALLS):
            final = i == MAX_AI_CALLS - 1
            try:
                reply = llm.ask("chat_agent", SYSTEM, self.prompt(question, context, history, final),
                                tier=CHAT_TIER, json=True, max_tokens=1100, project_id=project_id)
            except AIUnavailable as e:
                return self.without_ai({"daily_limit": "resting", "off": "off"}.get(e.reason, "busy"), question)
            self.calls += 1
            data = _loads(reply)
            action = str(data.get("action", "")).strip()
            if action == "answer" or final or not action:
                if not data.get("answer"):
                    if final:
                        return self.without_ai("busy", question)
                    self.transcript.append("(Reply with a valid JSON object, please.)")
                    continue
                return self.finish(data, question)
            text, step = t.run(action, data.get("input") or {})
            if any(s.get("title") == step["title"] for s in self.steps):
                self.transcript.append(f"(You already did {step['title']}; use what you have.)")
                continue
            self.step(text, step, thought=str(data.get("thought", "")))
        return self.without_ai("busy", question)

    def prompt(self, question: str, context: dict, history: list[dict], final: bool) -> str:
        parts = [TOOLS_HELP, ""]
        if history:
            parts.append("Earlier in this chat:")
            for h in history[-2:]:
                parts.append(f"Q: {h.get('q', '')[:200]}\nA: {h.get('a', '')[:400]}")
            parts.append("")
        if context.get("file"):
            where = f"function {context['fn'].split('::')[-1]}() in " if context.get("fn") else ""
            parts.append(f"The student is looking at {where}{context['file']}.")
        parts.append(f"Student's question: {question}")
        parts.append("")
        parts.append("What you have found so far:")
        parts.append("\n\n".join(self.transcript)[-9000:])
        parts.append("")
        parts.append(ANSWER_STYLE["normal"])
        if final:
            parts.append("This is your LAST turn: you must reply with action \"answer\" now.")
        else:
            parts.append(f"You may use {MAX_AI_CALLS - 1 - self.calls} more tools before answering. "
                         "Answer as soon as you have enough.")
        return "\n".join(parts)

    def finish(self, data: dict, question: str) -> dict:
        answer, cites = clean_citations(str(data.get("answer", "")), self.tools)
        followups = [str(f).strip()[:120] for f in (data.get("followups") or []) if str(f).strip()][:3]
        if len(followups) < 3:
            followups += [f for f in default_followups(self.tools, self.steps, question) if f not in followups]
        return {"mode": "ai", "answer": answer, "citations": cites, "followups": followups[:3],
                "steps": self.steps + [{"tool": "answer", "title": "Wrote the answer",
                                        "detail": f"{len(cites)} code reference{'s' if len(cites) != 1 else ''}",
                                        "refs": []}],
                "focus": self.focus(cites)}

    def without_ai(self, reason: str, question: str) -> dict:
        """No AI: finish the investigation with the tools alone and show the best code."""
        t = self.tools
        msg = AIUnavailable.MESSAGES.get({"resting": "daily_limit"}.get(reason, reason), "")
        best = next((r for s in self.steps for r in s["refs"] if r.get("function_id")), None)
        general = any(s["tool"] == "project_overview" for s in self.steps) and GENERAL.search(question)
        if best is None or general:
            ov = ai_notes.apply_notes(copy.deepcopy(t.overview), ai_notes.load_notes(t.dir))
            sh = ov["start_here"]
            text = ov["overview"]["text"] if best is not None or general else \
                "I couldn't find code that matches those words. Here is what the whole project does: " + ov["overview"]["text"]
            focus = None
            if sh.get("function"):
                n = t.find(f"{sh['file']}::{sh['function']['name']}")
                if n:
                    text += f" Start reading at `{n['name']}()` [{n['path']}:{n['start_line']}]."
                    focus = {"function_id": n["id"], "file": n["path"], "start": n["start_line"], "end": n["end_line"]}
            text, cites = clean_citations(text, t)
            return {"mode": "no_ai", "reason": reason, "message": msg, "answer": text, "citations": cites,
                    "followups": default_followups(t, self.steps, question), "steps": self.steps, "focus": focus}
        n = t.nodes[best["function_id"]]
        if not any(s["tool"] == "read_function" and s["refs"] and s["refs"][0]["function_id"] == n["id"] for s in self.steps):
            self.step(*t.read_function(n["id"]))
        self.step(*t.find_callers(n["id"]))
        self.step(*t.get_flowchart(n["id"]))
        summary = t.known.get(n["name"]) or analyze._first_sentence(n.get("doc", ""))
        callers = self.steps[-2]["refs"]
        caller_text = (" It is used by " + ", ".join(f"`{c['label']}` [{c['file']}:{c['start']}]" for c in callers[:3]) + "."
                       ) if callers else ""
        answer = (f"The code that best matches your question is `{n['name']}()` in {n['path']} "
                  f"[{n['path']}:{n['start_line']}-{n['end_line']}]."
                  + (f" {summary.rstrip('.')}." if summary else "") + caller_text
                  + " Its flowchart and code are shown here, step by step.")
        answer += self.matching_box(n, question)
        answer, cites = clean_citations(answer, t)
        return {"mode": "no_ai", "reason": reason, "message": msg, "answer": answer, "citations": cites,
                "followups": default_followups(t, self.steps, question), "steps": self.steps,
                "focus": {"function_id": n["id"], "file": n["path"], "start": n["start_line"], "end": n["end_line"]}}

    def matching_box(self, n: dict, question: str) -> str:
        """No AI: if the question is about a loop / decision / menu, explain that box."""
        words = set(re.findall(r"[a-z]+", question.lower()))
        wanted = [k for k, ws in KIND_WORDS.items() if words & set(ws)]
        if not wanted:
            return ""
        src = code_view.read_source(self.tools.dir, n["path"])
        fc = build_flowchart(n["path"], src, n["start_line"], n["end_line"], n["name"], self.tools.known)
        box = next((b for k in wanted for b in (fc or {}).get("boxes", []) if b["kind"] == k), None)
        if box is None:
            return ""
        return f" The {'menu' if box['kind'] == 'switch' else box['kind']} at [{n['path']}:{box['lines'][0]}]: {box['explain']}"

    def focus(self, cites: list[dict]) -> Optional[dict]:
        """What the evidence panel shows first: the function of the first citation."""
        for c in cites:
            for n in self.tools.funcs:
                if n["path"] == c["file"] and n["start_line"] <= c["start"] <= n["end_line"]:
                    return {"function_id": n["id"], "file": c["file"], "start": c["start"], "end": c["end"]}
            return {"function_id": None, "file": c["file"], "start": c["start"], "end": c["end"]}
        best = next((r for s in self.steps for r in s["refs"] if r.get("function_id")), None)
        return {"function_id": best["function_id"], "file": best["file"], "start": best["start"], "end": best["end"]} if best else None


def clean_citations(text: str, tools: Toolbox) -> tuple[str, list[dict]]:
    """Keep only citations of real lines in real files; drop the rest from the text."""
    cites: list[dict] = []
    files = list(tools.graph["files"])

    def fix(m: re.Match) -> str:
        name, a, b = m.group(1).replace("\\", "/"), int(m.group(2)), int(m.group(3) or m.group(2))
        path = name if name in tools.graph["files"] else next((p for p in files if p.endswith("/" + name)), None)
        total = len(tools.lines(path)) if path else 0
        if not path or a < 1 or a > total:
            return ""
        b = min(max(a, b), total)
        c = {"file": path, "start": a, "end": b}
        if c not in cites:
            cites.append(c)
        return f"[{path}:{a}" + (f"-{b}]" if b != a else "]")

    text = CITE.sub(fix, text)
    text = re.sub(r"\s+([.,;])", r"\1", re.sub(r"[ \t]{2,}", " ", text))
    text = re.sub(r"([,;])(?:\s*[,;])+", r"\1", text)            # "a, , and b" after removed citations
    text = re.sub(r"(?<![\w`])\(\s*\)", "", text).strip()           # "( )" left behind, not `name()`
    return text, cites


def default_followups(tools: Toolbox, steps: list[dict], question: str) -> list[str]:
    """Follow-up questions without AI, from what the steps found."""
    out = []
    names = [r["label"].removesuffix("()") for s in steps for r in s["refs"] if r.get("function_id")]
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    if seen:
        out.append(f"Who calls {seen[0]}()?")
        out.append(f"Show me the steps of {seen[0]}() in a flowchart")
    if len(seen) > 1:
        out.append(f"What does {seen[1]}() do?")
    sh = tools.overview["start_here"]
    if sh.get("function"):
        out.append(f"What happens when the program starts in {sh['function']['name']}()?")
    out.append("What does this program do?")
    out += [q for q in suggestions(tools.dir) if q not in out]
    return [q for q in out if q.lower() != question.lower()][:3]


def suggestions(project_dir: str) -> list[str]:
    """Starter questions for the chat box (no AI)."""
    graph, overview, nodes = code_view.load(project_dir)
    out = ["What does this program do?"]
    sh = overview["start_here"]
    funcs = [n for n in graph["nodes"] if n["type"] in ("function", "method")]
    if sh.get("function"):
        name = sh["function"]["name"]
        n = next((f for f in funcs if f["path"] == sh["file"] and f["name"] == name), None)
        if n:
            src = code_view.read_source(project_dir, n["path"])
            fc = build_flowchart(n["path"], src, n["start_line"], n["end_line"], name, {})
            kinds = [b["kind"] for b in fc["boxes"]] if fc else []
            if "loop" in kinds:
                out.append(f"What does the loop in {name}() do?")
            else:
                out.append(f"What happens when {name}() runs?")
    called = {}
    for e in graph["edges"]:
        if e["type"] == "calls" and e["target"] in nodes:
            called[e["target"]] = called.get(e["target"], 0) + 1
    for fid, _ in sorted(called.items(), key=lambda kv: (-kv[1], kv[0]))[:2]:
        out.append(f"How does {nodes[fid]['name']}() work?")
    return out[:4]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _synonyms(word: str) -> str:
    """Beginner words -> code words, also for "saved", "loops", "printing"."""
    for form in (word, word[:-1] if word.endswith("s") else "", word[:-1] if word.endswith("d") else "",
                 word[:-2] if word.endswith("ed") else "", word[:-3] if word.endswith("ing") else ""):
        if form and form in SYNONYMS:
            return SYNONYMS[form] + " " + form
    return ""


KIND_WORDS = {"loop": ("loop", "repeat", "again", "while", "for"), "decision": ("if", "check", "zero", "decide", "else", "wrong", "fail"),
              "switch": ("menu", "choice", "choose", "option", "switch", "case")}


def _loads(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Jobs: questions run in the background; the page polls for live steps.
# ---------------------------------------------------------------------------

_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="chat")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _cache_path(project_dir: str, key: str) -> str:
    return os.path.join(project_dir, "chat", key + ".json")


def ask(project_id: str, project_dir: str, question: str, context: dict, history: list[dict]) -> str:
    question = " ".join(question.split())[:MAX_QUESTION]
    context = {k: str(v)[:300] for k, v in (context or {}).items() if k in ("file", "fn") and v}
    history = [{"q": str(h.get("q", ""))[:300], "a": str(h.get("a", ""))[:600]} for h in (history or [])[-2:]
               if isinstance(h, dict)]
    key = hashlib.sha256(json.dumps([question.lower(), context, history], sort_keys=True).encode()).hexdigest()[:24]
    qid = key
    with _lock:
        job = _jobs.get(qid)
        # a finished AI answer is reused; a no-AI answer is redone (the AI may be back)
        if job and (job["status"] == "working" or (job["status"] == "done" and job.get("mode") == "ai")):
            return qid
        cached = analyze.load_json(_cache_path(project_dir, key))
        if cached:
            _jobs[qid] = {**cached, "status": "done", "cached": True, "question": question}
            return qid
        _jobs[qid] = {"status": "working", "question": question, "steps": [], "started": time.time()}

    def emit(update: dict) -> None:
        with _lock:
            _jobs[qid].update(copy.deepcopy(update))

    def work() -> None:
        try:
            result = Agent(project_dir, emit).run(question, context, history, project_id)
            if result["mode"] == "ai":
                write_json_atomic(_cache_path(project_dir, key), result)
            with _lock:
                _jobs[qid] = {**result, "status": "done", "question": question}
        except Exception as e:                          # never leave the student waiting
            with _lock:
                _jobs[qid].update({"status": "error", "message": "Something went wrong while answering. "
                                                                 f"Please try again. ({type(e).__name__})"})

    os.makedirs(os.path.join(project_dir, "chat"), exist_ok=True)
    _pool.submit(work)
    return qid


def job(qid: str) -> Optional[dict]:
    with _lock:
        j = _jobs.get(qid)
        return copy.deepcopy(j) if j else None


# ---- "Explain simpler" for an answer -----------------------------------------

def simpler(project_id: str, qid: str) -> dict:
    j = job(qid)
    if j is None or j.get("status") != "done":
        return {"status": "missing"}
    if j.get("simpler"):
        return {"status": "done", "answer": j["simpler"]["answer"], "citations": j["simpler"]["citations"]}
    if j.get("mode") != "ai":
        return {"status": "done", "answer": _simple_fallback(j), "citations": j.get("citations", [])}
    with _lock:
        if not _jobs[qid].get("simpler_started"):
            _jobs[qid]["simpler_started"] = True
            _pool.submit(_make_simpler, project_id, qid, j)
    j = job(qid)
    if j.get("simpler"):
        return {"status": "done", "answer": j["simpler"]["answer"], "citations": j["simpler"]["citations"]}
    if j.get("simpler_state") in ("off", "resting", "busy"):
        return {"status": j["simpler_state"], "message": j.get("simpler_message", ""),
                "answer": _simple_fallback(j), "citations": j.get("citations", [])}
    return {"status": "thinking"}


def _make_simpler(project_id: str, qid: str, j: dict) -> None:
    prompt = (f"A student asked: {j['question']}\n\nThis answer was correct but too hard for them:\n{j['answer']}\n\n"
              f"{ANSWER_STYLE['simpler']} Keep the same [file:line] citations. "
              'Reply with JSON: {"answer": "..."}')
    try:
        text = llm.ask("chat_simpler", SYSTEM, prompt, tier="fast", json=True, max_tokens=500, project_id=project_id)
        data = _loads(text)
        allowed = {(c["file"], c["start"]) for c in j.get("citations", [])}
        answer = data.get("answer") or ""
        # never let the rewrite invent new citations
        answer = CITE.sub(lambda m: m.group(0) if any(m.group(1).endswith(f) or f.endswith(m.group(1))
                                                       for f, s in allowed if s == int(m.group(2))) else "", answer)
        with _lock:
            _jobs[qid]["simpler"] = {"answer": " ".join(answer.split()) or _simple_fallback(j),
                                     "citations": j.get("citations", [])}
    except AIUnavailable as e:
        with _lock:
            _jobs[qid]["simpler_state"] = {"daily_limit": "resting", "off": "off"}.get(e.reason, "busy")
            _jobs[qid]["simpler_message"] = str(e)


def _simple_fallback(j: dict) -> str:
    focus = j.get("focus") or {}
    name = (focus.get("function_id") or "").split("::")[-1]
    if name:
        return (f"In short: look at `{name}()`. It is like a small recipe inside the program. "
                "Follow its flowchart box by box, from start to end.")
    return "In short: this program is made of small recipes (functions). Start where the program starts and follow it."


# ---------------------------------------------------------------------------
# Fake AI (tests, offline development): a tiny scripted agent.
# ---------------------------------------------------------------------------

def _fake_agent(system: str, prompt: str) -> str:
    found = prompt.split("What you have found so far:", 1)[-1]
    first = re.search(r"\[id: ([^\]]+)\]", found)
    if "read_function(" not in found and first:
        return json.dumps({"thought": "Read the best match first", "action": "read_function",
                           "input": {"name": first.group(1)}})
    if "find_callers(" not in found and first:
        return json.dumps({"thought": "See who uses it", "action": "find_callers",
                           "input": {"name": first.group(1)}})
    m = re.search(r"read_function\((\w+)\) - ([\w./-]+) lines (\d+)-(\d+)", found)
    if m:
        name, path, a, b = m.groups()
        answer = f"The function `{name}` does this [{path}:{a}-{b}]. (Written by the fake test AI.)"
    else:
        answer = "I could not find it in the code. (Written by the fake test AI.)"
    return json.dumps({"thought": "I have enough", "action": "answer", "answer": answer,
                       "followups": ["Fake follow-up one?", "Fake follow-up two?", "Fake follow-up three?"]})


def _fake_simpler(system: str, prompt: str) -> str:
    cites = CITE.findall(prompt.split("too hard for them:", 1)[-1])
    ref = f" [{cites[0][0]}:{cites[0][1]}]" if cites else ""
    return json.dumps({"answer": f"It is like a recipe card.{ref} (Simpler, by the fake test AI.)"})


FakeProvider.handlers.update({"chat_agent": _fake_agent, "chat_simpler": _fake_simpler})
