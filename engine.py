"""RepoSage engine: structure-aware indexing + graph-augmented retrieval.

Pure-stdlib (+ networkx) so it runs anywhere with no model downloads.
- Parsing: Python's built-in `ast` module -> real function/class chunks.
- Graph: networkx call graph (caller -> callee).
- Retrieval: TF-IDF cosine over chunk source (the "semantic" layer;
  swappable for ChromaDB + embeddings later).
- Graph expansion: pull callers/callees of top hits.
"""

import ast
import math
import os
import re
from collections import defaultdict

import networkx as nx


# ----------------------------- Parsing (AST) -----------------------------

def _extract_calls(node):
    """Collect names of functions called inside an AST node."""
    calls = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
    return calls


def parse_repo(repo_path):
    """Walk a repo and return a list of code chunks (functions + classes)."""
    chunks = []
    for root, _dirs, files in os.walk(repo_path):
        if any(part in root for part in (".git", "__pycache__", ".venv")):
            continue
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, repo_path)
            try:
                src = open(fpath, "r", encoding="utf-8").read()
                tree = ast.parse(src)
            except (SyntaxError, UnicodeDecodeError):
                continue
            lines = src.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = node.lineno
                    end = getattr(node, "end_lineno", start)
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    source = "\n".join(lines[start - 1:end])
                    chunks.append({
                        "id": f"{rel}::{node.name}",
                        "name": node.name,
                        "type": kind,
                        "file": rel,
                        "start_line": start,
                        "end_line": end,
                        "source": source,
                        "doc": (ast.get_docstring(node) or "").strip(),
                        "calls": sorted(_extract_calls(node)),
                    })
    return chunks


# ----------------------------- Graph (networkx) --------------------------

def build_graph(chunks):
    """Build a caller -> callee call graph over known functions."""
    g = nx.DiGraph()
    name_to_id = {}
    for c in chunks:
        g.add_node(c["id"], name=c["name"], file=c["file"], type=c["type"])
        name_to_id.setdefault(c["name"], c["id"])
    for c in chunks:
        for callee in c["calls"]:
            if callee in name_to_id and name_to_id[callee] != c["id"]:
                g.add_edge(c["id"], name_to_id[callee])
    return g


# ----------------------------- Retrieval (TF-IDF) ------------------------

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tokenize(text):
    toks = []
    for raw in _TOKEN.findall(text.lower()):
        toks.append(raw)
        # split snake_case / expand identifiers for better recall
        toks.extend(p for p in raw.split("_") if p)
    return toks


class Index:
    """Minimal in-memory TF-IDF index over chunk text."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.by_id = {c["id"]: c for c in chunks}
        self._build()

    def _doc_text(self, c):
        return f"{c['name']} {c['doc']} {c['source']}"

    def _build(self):
        df = defaultdict(int)
        self.tfs = []
        for c in self.chunks:
            counts = defaultdict(int)
            for t in _tokenize(self._doc_text(c)):
                counts[t] += 1
            self.tfs.append(counts)
            for t in counts:
                df[t] += 1
        n = max(len(self.chunks), 1)
        self.idf = {t: math.log((n + 1) / (dfi + 1)) + 1 for t, dfi in df.items()}
        self.vecs = [self._vec(tf) for tf in self.tfs]
        self.norms = [math.sqrt(sum(v * v for v in vec.values())) or 1.0 for vec in self.vecs]

    def _vec(self, tf):
        return {t: c * self.idf.get(t, 0.0) for t, c in tf.items()}

    def search(self, query, k=4):
        qcounts = defaultdict(int)
        for t in _tokenize(query):
            qcounts[t] += 1
        qvec = self._vec(qcounts)
        qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        scored = []
        for i, c in enumerate(self.chunks):
            vec, norm = self.vecs[i], self.norms[i]
            dot = sum(qvec.get(t, 0.0) * w for t, w in vec.items())
            scored.append((dot / (qnorm * norm), c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for s, c in scored[:k] if s > 0]


# ------------------------- Graph-augmented retrieval ---------------------

def expand_with_graph(hit_ids, graph, by_id, max_neighbors=6):
    """Given top hit chunk ids, pull in their callers and callees."""
    added = []
    seen = set(hit_ids)
    for hid in hit_ids:
        if hid not in graph:
            continue
        neighbors = list(graph.successors(hid)) + list(graph.predecessors(hid))
        for nb in neighbors:
            if nb not in seen and nb in by_id:
                seen.add(nb)
                added.append(nb)
            if len(added) >= max_neighbors:
                break
    return added


# ------------------------------ Keyword baseline -------------------------

def keyword_search(query, chunks, k=4):
    """Dumb baseline: rank chunks by raw token-overlap count (grep-like)."""
    qtokens = set(_tokenize(query))
    scored = []
    for c in chunks:
        text_tokens = _tokenize(c["source"] + " " + c["name"])
        overlap = sum(1 for t in text_tokens if t in qtokens)
        if overlap:
            scored.append((overlap, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for s, c in scored[:k]]
