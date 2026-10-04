"""Retrieval for /reposage:chat: find the code that answers a question.

This is the search from the original RepoSage (engine.py), moved onto the
knowledge graph:

  1. TF-IDF search over every function/class ("chunk"). A chunk's text is
     its name, docstring, signature and source, PLUS the AI summary and
     tags when /reposage:summarize has run, so plain-English questions
     ("how does retry work?") match code that never uses those words.
  2. Graph expansion: the best hits pull in their callers and callees, so
     the answer can follow the call chain across files. Links the scanner
     only guessed ("medium" confidence) are remembered, so the answer can
     say how sure it is.

Pure standard library, like the original.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .layers import TESTS, guess_layer, load_overrides, resolve_layer

SYMBOL_TYPES = ("class", "interface", "enum", "record", "function", "method")
CLASS_TYPES = ("class", "interface", "enum", "record")

# Words that say nothing about the code ("how does X work?").
STOP_WORDS = set("""
a an and are as at be by can could do does done for from get gets has have how i
if in into is it its me my of on or should so that the their them then there
these this to use used uses using was what when where which who why will with
work works working would you your happen happens happening explain tell show
code file function method class project app""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def tokenize(text: str) -> list[str]:
    """"retryFromError, max_retries" -> retry, from, error, max, retry."""
    out = []
    for word in _WORD.findall(text or ""):
        for part in _CAMEL.findall(word):
            t = part.lower()
            if len(t) < 2 or t in STOP_WORDS:
                continue
            # tiny stemmer: retries/retrying/retried -> retry
            for suffix, repl in (("ies", "y"), ("ied", "y"), ("ing", ""), ("es", ""), ("s", ""), ("ed", "")):
                if len(t) - len(suffix) >= 3 and t.endswith(suffix):
                    t = t[: -len(suffix)] + repl
                    break
            out.append(t)
    return out


@dataclass
class Chunk:
    id: str
    name: str
    qual: str
    kind: str
    path: str
    start: int
    end: int
    layer: str
    summary: str
    text: str                    # what the search looks at


@dataclass
class Hit:
    id: str
    score: float
    reason: str                  # "search", "calls [1]", "called by [2]"
    via_guess: bool = False      # reached through a medium-confidence link


@dataclass
class Retrieval:
    hits: list[Hit]
    links: list[dict] = field(default_factory=list)   # links among the hits


class Index:
    """Minimal in-memory TF-IDF index (same idea as the original engine.py)."""

    def __init__(self, repo: str, graph: dict, include_tests: bool = False):
        self.graph = graph
        nodes = {n["id"]: n for n in graph["nodes"]}
        self.nodes = nodes
        file_nodes = {n["id"]: n for n in graph["nodes"] if n["type"] == "file"}
        sources: dict[str, list[str]] = {}
        overrides, _ = load_overrides(repo)
        self.chunks: list[Chunk] = []
        for n in graph["nodes"]:
            if n["type"] not in SYMBOL_TYPES:
                continue
            if not include_tests and guess_layer(n["path"]) == TESTS:
                continue
            if n["path"] not in sources:
                sources[n["path"]] = _read_lines(os.path.join(repo, n["path"]))
            lines = sources[n["path"]]
            # Classes: only their header + summary (their methods are chunks too).
            end = n["end_line"] if n["type"] not in CLASS_TYPES else min(n["end_line"], n["start_line"] + 5)
            body = "\n".join(lines[n["start_line"] - 1: min(end, n["start_line"] + 150)])
            f = file_nodes.get(n["path"], {})
            qual = n["id"].split("::", 1)[1]
            summary = n.get("summary") or ""
            # The AI summary and the name count double: they are the best
            # description of what a chunk is *for*.
            text = " ".join([qual, qual, n.get("doc", ""), summary, summary,
                             " ".join(f.get("tags") or []), n["path"], body])
            self.chunks.append(Chunk(n["id"], n["name"], qual, n["type"], n["path"],
                                     n["start_line"], n["end_line"],
                                     resolve_layer(n["path"], f.get("layer"), overrides)[0],
                                     summary, text))
        self._build()
        self.edges_out: dict[str, list[dict]] = defaultdict(list)
        self.edges_in: dict[str, list[dict]] = defaultdict(list)
        for e in graph["edges"]:
            if e["type"] == "calls":
                self.edges_out[e["source"]].append(e)
                self.edges_in[e["target"]].append(e)

    def _build(self) -> None:
        df: dict[str, int] = defaultdict(int)
        self.tfs = []
        for c in self.chunks:
            counts: dict[str, int] = defaultdict(int)
            for t in tokenize(c.text):
                counts[t] += 1
            self.tfs.append(counts)
            for t in counts:
                df[t] += 1
        n = max(len(self.chunks), 1)
        self.idf = {t: math.log((n + 1) / (d + 1)) + 1 for t, d in df.items()}
        self.vecs = [{t: (1 + math.log(c)) * self.idf[t] for t, c in tf.items()} for tf in self.tfs]
        self.norms = [math.sqrt(sum(v * v for v in vec.values())) or 1.0 for vec in self.vecs]
        self.by_id = {c.id: c for c in self.chunks}

    def similarities(self, question: str) -> dict[str, float]:
        """Cosine similarity of every chunk with the question (0 if unrelated)."""
        q: dict[str, float] = defaultdict(float)
        concept_of: dict[str, set] = defaultdict(set)     # vocabulary word -> question words
        words = set(tokenize(question))
        for t in words:
            q[t] += 1
            concept_of[t].add(t)
            # "log" (from "logs in") should also find "login": short words
            # also match slightly longer words that start with them.
            if len(t) >= 3:
                for v in self.idf:
                    if v != t and v.startswith(t) and len(v) <= len(t) + 4:
                        q[v] += 0.5
                        concept_of[v].add(t)
        qvec = {t: c * self.idf.get(t, 0.0) for t, c in q.items()}
        qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        sims = {}
        for i, c in enumerate(self.chunks):
            vec = self.vecs[i]
            dot = sum(w * vec.get(t, 0.0) for t, w in qvec.items())
            if dot > 0:
                # Reward chunks that cover MORE of the question's words:
                # "user logs in" should prefer login() over get_user().
                covered = {w for t in qvec if t in vec for w in concept_of[t]}
                coverage = len(covered) / max(1, len(words))
                sims[c.id] = dot / (qnorm * self.norms[i]) * (0.4 + 0.6 * coverage)
        return sims

    def search(self, question: str, k: int = 6) -> list[tuple[float, Chunk]]:
        sims = self.similarities(question)
        scored = sorted(((s, self.by_id[i]) for i, s in sims.items()),
                        key=lambda x: (-x[0], x[1].id))   # ties broken by id: deterministic
        return scored[:k]

    def retrieve(self, question: str, k: int = 6, max_items: int = 12) -> Retrieval:
        """Search, then expand one hop through the call graph."""
        sims = self.similarities(question)
        found = sorted(((s, self.by_id[i]) for i, s in sims.items()), key=lambda x: (-x[0], x[1].id))[:k]
        if not found:
            return Retrieval([])
        top = found[0][0]
        # Keep hits that are reasonably close to the best one.
        hits = [Hit(c.id, s, "search") for s, c in found if s >= top * 0.35]
        chosen = {h.id for h in hits}
        extra: list[Hit] = []
        for rank, h in enumerate(list(hits)):
            neighbours = [(e["target"], e, "calls") for e in self.edges_out.get(h.id, [])] + \
                         [(e["source"], e, "called by") for e in self.edges_in.get(h.id, [])]
            # Neighbours that also match the question first, then confident links.
            neighbours.sort(key=lambda x: (-sims.get(x[0], 0.0), x[1].get("confidence") != "high", x[0]))
            # The two best hits may pull in any neighbour; weaker hits only
            # neighbours that are themselves about the question.
            limit = 4 if rank < 2 else 2
            for other, e, how in neighbours[:limit]:
                if other in chosen or other not in self.by_id:
                    continue
                if rank >= 2 and sims.get(other, 0.0) <= 0:
                    continue
                chosen.add(other)
                extra.append(Hit(other, h.score * 0.5, f"{how} #{rank + 1}",
                                 via_guess=e.get("confidence") != "high"))
        items = (hits + sorted(extra, key=lambda x: -x.score))[:max_items]
        ids = {h.id for h in items}
        links = [e for e in self.graph["edges"]
                 if e["type"] == "calls" and e["source"] in ids and e["target"] in ids]
        return Retrieval(items, links)


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []
