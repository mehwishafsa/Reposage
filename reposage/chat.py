"""/reposage:chat: answer questions about the code, and remember the route.

  ask   -> search + graph expansion (rag.py), then print a compact, numbered
           "context pack" with ONLY the relevant code (with line numbers),
           each item's summary, and the call links between the items.
           Claude answers from this pack alone.
  path  -> after answering, Claude lists the items it used, in order
           ("2:login checks the password", ...). We save that as an
           Answer Path (.reposage/answers/<id>.json); the dashboard shows it
           as a numbered route with everything else faded.

Keeping it cheap: the pack is capped (~12 items, ~60 lines each), so a
question costs a few thousand tokens however large the repository is.
"""

from __future__ import annotations

import json
import os
import re
import time

from .cache import write_json_atomic
from .rag import Index

AI_DIR = "ai"
ANSWERS_DIR = "answers"
MAX_ITEMS = 12
MAX_LINES_PER_ITEM = 60
MAX_PACK_CHARS = 40_000
EXT_LANG = {".py": "python", ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
            ".cjs": "javascript", ".ts": "typescript", ".tsx": "tsx", ".java": "java"}


# ----------------------------------------------------------------------
# ask
# ----------------------------------------------------------------------

def ask(repo: str, graph: dict, question: str, include_tests: bool = False) -> str:
    """Build the context pack for a question; also saved for `path`."""
    index = Index(repo, graph, include_tests=include_tests)
    found = index.retrieve(question, max_items=MAX_ITEMS)
    items = []
    for k, hit in enumerate(found.hits, start=1):
        c = index.by_id[hit.id]
        items.append({"n": k, "id": c.id, "path": c.path, "start": c.start, "end": c.end,
                      "kind": c.kind, "qual": c.qual, "layer": c.layer, "summary": c.summary,
                      "reason": hit.reason, "guess": hit.via_guess, "score": round(hit.score, 3)})
    number = {it["id"]: it["n"] for it in items}
    links = [{"from": number[e["source"]], "to": number[e["target"]], "line": e.get("line"),
              "confidence": e.get("confidence", "high")}
             for e in found.links]
    links.sort(key=lambda x: (x["from"], x["to"]))

    pack = _render_pack(repo, question, items, links)
    ctx = {"question": question, "created": _now(), "items": items, "links": links}
    write_json_atomic(os.path.join(repo, ".reposage", AI_DIR, "chat-context.json"), ctx)
    return pack


def _render_pack(repo: str, question: str, items: list[dict], links: list[dict]) -> str:
    out = [f"# RepoSage context for: {question}", ""]
    if not items:
        out.append("No code in this repository matched the question. "
                   "Tell the user, and suggest different words or `/reposage:summarize` "
                   "(summaries make plain-English questions match better).")
        return "\n".join(out)
    out += ["Answer ONLY from the numbered items below. Cite code as `file:line`. "
            "Items were found by search or by following calls from a search hit.", ""]
    budget = MAX_PACK_CHARS
    sources: dict[str, list[str]] = {}
    for it in items:
        if it["path"] not in sources:
            sources[it["path"]] = _read_lines(os.path.join(repo, it["path"]))
        lines = sources[it["path"]]
        how = "search hit" if it["reason"] == "search" else _describe_reason(it["reason"])
        if it["guess"]:
            how += " (through a GUESSED link: matched by name only)"
        head = [f"## [{it['n']}] {it['qual']}  ({it['kind']}, {it['layer']} layer)",
                f"- where: {it['path']}:{it['start']}-{it['end']}",
                f"- found by: {how}"]
        if it["summary"]:
            head.append(f"- summary: {it['summary']}")
        start, end = it["start"], it["end"]
        shown_end = min(end, start + MAX_LINES_PER_ITEM - 1)
        code = [f"{i:>5} | {lines[i - 1]}" for i in range(start, shown_end + 1) if i - 1 < len(lines)]
        if shown_end < end:
            code.append(f"      … lines {shown_end + 1}-{end} not shown")
        lang = EXT_LANG.get(os.path.splitext(it["path"])[1], "")
        block = "\n".join(head + [f"~~~{lang}"] + code + ["~~~", ""])
        if len(block) > budget:
            block = "\n".join(head + ["(code left out to keep this short)", ""])
        budget -= len(block)
        out.append(block)
    if links:
        out.append("## Calls between these items")
        for l in links:
            conf = "" if l["confidence"] == "high" else "  (GUESSED: matched by name only)"
            out.append(f"- [{l['from']}] calls [{l['to']}] at line {l['line']}{conf}")
    return "\n".join(out)


def _describe_reason(reason: str) -> str:
    how, _, rank = reason.partition(" #")
    return f"{'called by' if how == 'calls' else 'calls'} [{rank}]" if rank else reason


# ----------------------------------------------------------------------
# path
# ----------------------------------------------------------------------

def save_path(repo: str, steps: list[str], answer: str = "") -> dict:
    """Save an Answer Path. `steps` look like "3:login checks the password"
    (item number from the last `ask`, a colon, a short label)."""
    ctx_path = os.path.join(repo, ".reposage", AI_DIR, "chat-context.json")
    with open(ctx_path, encoding="utf-8") as fh:
        ctx = json.load(fh)
    by_n = {it["n"]: it for it in ctx["items"]}
    parsed, problems = [], []
    for raw in steps:
        num, _, label = raw.partition(":")
        try:
            item = by_n[int(num.strip().lstrip("[").rstrip("]"))]
        except (ValueError, KeyError):
            problems.append(f"step {raw!r}: no item with that number")
            continue
        parsed.append({"id": item["id"], "path": item["path"], "line": item["start"],
                       "qual": item["qual"], "label": " ".join(label.split())[:160]})
    if not parsed:
        raise ValueError("no valid steps (use item numbers from the last question)")

    # How sure are we? Look at the link between each pair of consecutive steps.
    # Each hop is "high" (a real call), "medium" (a guessed call) or "none".
    link_conf: dict[tuple[str, str], str] = {}
    for l in ctx["links"]:
        a, b = by_n[l["from"]]["id"], by_n[l["to"]]["id"]
        for key in ((a, b), (b, a)):              # either direction counts
            if link_conf.get(key) != "high":
                link_conf[key] = l["confidence"]
    # A step is backed by a call if ANY earlier step calls it (or it calls
    # back): "login calls get_user, then verify_password" is two real calls
    # from login, even though get_user never calls verify_password.
    hops = []
    for j in range(1, len(parsed)):
        found = [link_conf[(parsed[i]["id"], parsed[j]["id"])] for i in range(j)
                 if (parsed[i]["id"], parsed[j]["id"]) in link_conf]
        hops.append("high" if "high" in found else "medium" if found else "none")
    guessed = hops.count("medium")
    unlinked = hops.count("none")
    if guessed:
        confidence = "medium"
        note = (f"{guessed} step(s) rely on a guessed link (a call matched by name only); "
                "check those in the code.")
    elif unlinked:
        confidence = "medium"
        note = (f"{unlinked} step(s) are not directly connected by a call we found; "
                "the answer connects them by reading the code.")
    else:
        confidence = "high"
        note = "Every step is linked by a call found directly in the code."

    created = _now()
    slug = re.sub(r"[^a-z0-9]+", "-", ctx["question"].lower()).strip("-")[:40] or "answer"
    answer_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
    record = {"id": answer_id, "question": ctx["question"], "answer": " ".join(answer.split())[:600],
              "created": created, "confidence": confidence, "confidence_note": note,
              "steps": parsed, "hops": hops, "problems": problems}
    folder = os.path.join(repo, ".reposage", ANSWERS_DIR)
    write_json_atomic(os.path.join(folder, f"{answer_id}.json"), record)
    return record


def load_answers(repo: str, limit: int = 20) -> list[dict]:
    """The most recent saved Answer Paths (newest first)."""
    folder = os.path.join(repo, ".reposage", ANSWERS_DIR)
    if not os.path.isdir(folder):
        return []
    records = []
    for name in sorted(os.listdir(folder), reverse=True):
        if name.endswith(".json"):
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    records.append(json.load(fh))
            except (OSError, ValueError):
                continue
        if len(records) >= limit:
            break
    return records


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
