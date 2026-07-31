# RepoSage

Agentic RAG for codebase Q&A. Paste code → see how it connects (flowchart) →
ask questions and watch it follow the **call graph** across functions, the thing
plain keyword search can't do.

Two ways to run it:

| Version | Where | Best for |
|---------|-------|----------|
| **Static visualizer** (`docs/index.html`) | any browser / GitHub Pages | the shareable demo link + flowchart |
| **FastAPI backend** (`app.py`) | your laptop | showing a real backend + AST parsing + LLM answers |

---

## A. Static visualizer — deploy to GitHub Pages

Pure client-side (parses code, builds the call graph, draws the flowchart, runs
retrieval — all in the browser). No backend, no build step.

### Try locally
Just open `docs/index.html` in a browser. Click **Load sample**, then ask a question.

### Deploy the link
```bash
cd reposage
git init
git add .
git commit -m "RepoSage"
git branch -M main
git remote add origin https://github.com/mehwishafsa/reposage.git
git push -u origin main
```
Then on GitHub: **Settings → Pages → Source: Deploy from a branch → Branch: `main`, Folder: `/docs` → Save.**

Live in ~1 min at: **https://mehwishafsa.github.io/reposage/**

### What it shows
1. Paste any Python → **Analyze** parses functions + calls.
2. **Flowchart** draws the call graph (arrows = who calls whom).
3. **Ask** a question → relevant functions light up; retrieved vs graph-expanded
   vs plain keyword search shown side by side.

---

## B. FastAPI backend (real AST + optional LLM)

```bash
pip install -r requirements.txt
python app.py        # open http://localhost:8000
```
- Uses Python's `ast` (real AST parsing) + NetworkX call graph.
- Works with **no API key** (shows retrieved + graph-expanded code).
- Optional AI answers: `export ANTHROPIC_API_KEY=sk-ant-...` before running.
- Point at any repo: `export REPOSAGE_REPO=/path/to/project`.

---

## 90-second demo script
1. Open the flowchart. "This is the code's structure — who calls whom."
2. Ask *"What happens end to end when a user logs in?"*
3. `login()` lights up, then the graph pulls in `verify_password()`,
   `generate_token()`, `get_user()` — across the file.
4. Point at the keyword panel: flat matches that don't know they're connected.
5. Line: **"grep finds text; RepoSage understands structure."**

## Honest note for Q&A
Retrieval uses TF-IDF here to stay dependency-free and deployable. The full
design swaps in ChromaDB + embeddings — a deliberate tradeoff for a
self-contained demo, not a missing piece.

## Files
- `docs/index.html` — static visualizer (deploys to Pages)
- `app.py` — FastAPI server + LLM synthesis
- `engine.py` — AST parse, call graph, TF-IDF retrieval, graph expansion
- `index.html` — backend UI
- `sample_repo/` — bundled demo codebase
