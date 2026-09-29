# RepoSage

Agentic RAG for codebase Q&A. Paste code → see how it connects (flowchart) →
ask questions and watch it follow the **call graph** across functions, the thing
plain keyword search can't do.

Two ways to run it:

| Version | Where | Best for |
|---------|-------|----------|
| **Static visualizer** (`docs/index.html`) | any browser / GitHub Pages | the shareable demo link + flowchart |
| **FastAPI backend** (`app.py`) | your laptop | showing a real backend + AST parsing + LLM answers |
| **Claude Code plugin** (`reposage/`) | inside Claude Code | a knowledge graph of any repo (Python, JS/TS, Java) — see section C |

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

## C. Claude Code plugin (in progress)

RepoSage as a Claude Code plugin. Step 1 (available now) is the scanner:
Tree-sitter reads the code and writes a **knowledge graph** to
`.reposage/graph.json`: files, classes, functions and methods, plus
`contains`, `imports` and `calls` links between them.

Coming next: dashboard, chat, explain, diff and onboarding commands.

### Install
In Claude Code:
```
/plugin marketplace add mehwishafsa/reposage
/plugin install reposage@reposage
```
To try a local checkout without installing it: `claude --plugin-dir /path/to/reposage`.

### Use
```
/reposage:reposage          # scan the current repo
/reposage:reposage --full   # ignore the cache, re-parse everything
```
The first run sets up a private Python environment in `~/.reposage/venv`
(about a minute, only once). It needs Python 3.10 or newer; set `REPOSAGE_HOME`
to use a different folder. Later scans only re-parse files whose content
changed.

Without Claude Code: `python3 reposage/bootstrap.py scan /path/to/repo`

### How it works
| Step | File | What it does |
|------|------|--------------|
| 1 | `reposage/scanner.py` | lists source files (respects `.gitignore`), hashes each one |
| 2 | `reposage/cache.py` | reuses parse results for files whose hash didn't change |
| 3 | `reposage/parsers/*.py` | Tree-sitter parses one file → definitions, imports, calls |
| 4 | `reposage/graph_builder.py` | links names across files → nodes + edges, sorted |

The output is **deterministic**: the same code always gives a byte-identical
`graph.json` (no timestamps, no absolute paths, everything sorted). Each
`calls` edge has a `confidence`:
- `high`: resolved exactly, through an import, the same file, `self`/`this`, or a declared Java type.
- `medium`: `obj.method()` where the type of `obj` is unknown, but only one method with that name exists in the repo.

The `summary`, `layer` and `tags` fields are left empty for AI agents to fill
in later. They are kept across re-scans for files that didn't change.

### Sharing `graph.json` with your team
`.reposage/` contains its own `.gitignore`:
- **`graph.json` is not ignored.** Commit it if you want your team to share one
  map of the codebase. It only changes when code changes, so diffs stay
  meaningful, and teammates can use it without scanning first.
- **`cache/` is ignored.** It is a local, machine-specific parse cache.
- **To keep everything private**, replace the contents of `.reposage/.gitignore`
  with a single `*` line (or add `.reposage/` to your root `.gitignore`).
  RepoSage never overwrites that file once it exists.

### Tests
```bash
~/.reposage/venv/bin/python -m unittest discover -s tests -t .
```

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
- `.claude-plugin/`, `commands/` — Claude Code plugin manifest and slash commands
- `reposage/` — plugin engine (Tree-sitter parsers, graph builder, cache, bootstrap)
- `requirements-plugin.txt` — plugin dependencies (installed into `~/.reposage/venv`)
- `tests/` — plugin tests and small fixture projects
