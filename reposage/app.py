"""RepoSage Lite - FastAPI server.

Run:  python app.py
Then open http://localhost:8000

Optional (for AI answers): set an Anthropic API key first:
    export ANTHROPIC_API_KEY=sk-ant-...
Without a key it still fully works -- it shows the retrieved + graph-expanded
code, which is the core of the demo.
"""

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import engine

# ------------------------------- Index the repo --------------------------

REPO_PATH = os.environ.get("REPOSAGE_REPO", "sample_repo")

print(f"[RepoSage] Indexing repo: {REPO_PATH} ...")
CHUNKS = engine.parse_repo(REPO_PATH)
GRAPH = engine.build_graph(CHUNKS)
INDEX = engine.Index(CHUNKS)
BY_ID = {c["id"]: c for c in CHUNKS}
print(f"[RepoSage] Indexed {len(CHUNKS)} code units, "
      f"{GRAPH.number_of_edges()} call-graph edges.")

app = FastAPI(title="RepoSage Lite")


# ------------------------------- LLM synthesis ---------------------------

def synthesize_answer(question, context_chunks):
    """Ask the LLM to answer using ONLY the retrieved code. Graceful fallback."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None  # UI will show the retrieved code instead

    context = "\n\n".join(
        f"# {c['file']}:{c['start_line']}  ({c['name']})\n{c['source']}"
        for c in context_chunks
    )
    prompt = (
        "You are RepoSage, answering questions about a codebase. "
        "Use ONLY the code below. Cite file:line for every claim. "
        "Be concise.\n\n"
        f"=== RETRIEVED CODE ===\n{context}\n\n"
        f"=== QUESTION ===\n{question}"
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")
    except Exception as e:  # never crash the demo
        return f"(LLM call failed: {e})"


# ------------------------------- API -------------------------------------

class Ask(BaseModel):
    question: str


def _slim(c):
    return {
        "id": c["id"], "name": c["name"], "type": c["type"],
        "file": c["file"], "start_line": c["start_line"],
        "end_line": c["end_line"], "source": c["source"],
    }


@app.get("/stats")
def stats():
    return {
        "repo": REPO_PATH,
        "units": len(CHUNKS),
        "edges": GRAPH.number_of_edges(),
        "files": sorted({c["file"] for c in CHUNKS}),
    }


@app.get("/graph")
def graph():
    return {
        "nodes": [{"id": n, "name": GRAPH.nodes[n]["name"]} for n in GRAPH.nodes],
        "edges": [{"source": u, "target": v} for u, v in GRAPH.edges],
    }


@app.post("/ask")
def ask(body: Ask):
    q = body.question
    hits = INDEX.search(q, k=3)
    hit_ids = [h["id"] for h in hits]
    expanded_ids = engine.expand_with_graph(hit_ids, GRAPH, BY_ID)
    expanded = [BY_ID[i] for i in expanded_ids]
    context = hits + expanded

    kw = engine.keyword_search(q, CHUNKS, k=3)

    # edges among the retrieved set, for the mini graph view
    ctx_ids = {c["id"] for c in context}
    sub_edges = [
        {"source": BY_ID[u]["name"], "target": BY_ID[v]["name"]}
        for u, v in GRAPH.edges if u in ctx_ids and v in ctx_ids
    ]

    return {
        "question": q,
        "answer": synthesize_answer(q, context),
        "retrieved": [_slim(h) for h in hits],
        "expanded": [_slim(e) for e in expanded],
        "keyword": [_slim(k) for k in kw],
        "subgraph": sub_edges,
    }


@app.get("/", response_class=HTMLResponse)
def home():
    return open("index.html", encoding="utf-8").read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
