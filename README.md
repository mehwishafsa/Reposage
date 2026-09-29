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

| Skill | Status | What it does |
|-------|--------|--------------|
| `/reposage:scan` | ✅ ready | scan the repo, build `.reposage/graph.json` |
| `/reposage:dashboard` | ✅ ready | interactive map of the code in the browser |
| `/reposage:summarize` | ✅ ready | AI summaries, real layers and tags, in plain English |
| `/reposage:chat` | planned | ask questions about the codebase |
| `/reposage:explain` | planned | deep-dive into one file |
| `/reposage:diff` | planned | what do my current changes affect? |
| `/reposage:onboard` | planned | onboarding guide for new team members |

Claude Code puts the plugin name in front of every skill, which is why they
all start with `reposage:`. Claude can also run a skill on its own when you
simply ask, e.g. "map this repo for me".

### Install
In Claude Code:
```
/plugin marketplace add mehwishafsa/reposage
/plugin install reposage@reposage
```
To try a local checkout without installing it: `claude --plugin-dir /path/to/reposage`.

### Use
```
/reposage:scan          # scan the current repo
/reposage:scan --full   # ignore the cache, re-parse everything
```
The first run sets up a private Python environment in `~/.reposage/venv`
(about a minute, only once). It needs Python 3.10 or newer; set `REPOSAGE_HOME`
to use a different folder. Later scans only re-parse files whose content
changed.

Without Claude Code: `python3 reposage/bootstrap.py scan /path/to/repo`

### The dashboard (`/reposage:dashboard`)
Scans (incrementally), then writes **`.reposage/dashboard.html`** and opens it.
It is one self-contained file: no server and no internet needed, and you can
share it by sending the file.

- **Strata layout**: one horizontal band per architectural layer (UI → API →
  Service → Data → Utility). Callers sit above the code they use, so an
  arrow pointing *up* is worth a second look.
- **Files / Symbols**: a map of files, or of every class and function.
- **Click** a node for its summary, docs, signature and connections (solid
  arrows = what it uses, dashed = what uses it). **/** searches everything;
  **Table** is a sortable list (and the accessible alternative to the map).
- Layers are **guessed from folder and file names** until the AI step
  classifies them. Tests are hidden by default (toggle them in the legend).
- Stays fast on big repos: gson (264 files, 4,249 symbols, 7,095 calls) opens
  in about 0.5 s and redraws in about 3 ms per frame. `graph.json` is repacked into a compact
  column format for the page (6.4 MB → 0.9 MB).

Without Claude Code: `python3 reposage/bootstrap.py dashboard /path/to/repo`

### AI summaries (`/reposage:summarize`)
Adds a 2-3 sentence plain-English summary, a layer (UI / API / Service / Data
/ Utility) and a few tags to every source file, plus a one-line summary for
its key functions and classes. Written for someone in their first week on
the team.

1. **Plan**: picks the files that need it and shows an estimate before anything runs:
   ```
   To summarize   31 files (+ 64 key functions/classes) in 4 batches
   Skipped        56 test files, 0 generated/vendored
   Estimated size ~30,000 input + ~7,000 output tokens (Haiku agents)
   ```
2. **Run**: sends the batches to Claude **Haiku** (4 at a time, no tools, the
   instructions in `agents/summarizer.md`) through your own Claude Code.
   No API key needed. Each answer is checked and saved as soon as it arrives,
   so an interrupted run just continues next time.
3. **Dashboard**: summaries, tags and AI layers appear in it; search also
   looks inside summaries.

It stays affordable:
- **Incremental:** only files whose content changed are summarized again.
- **Skipped:** tests and generated code are left out by default, and near-empty
  files get a fixed summary without asking the AI.
- **Measured costs:** ky (31 files) took $0.19 in 1.5 min, RepoSage (19 files)
  $0.19, and re-summarizing one changed file $0.02.

Options: `--include-tests`, `--limit N` (only the N most connected files),
`--force` (redo everything). Without Claude Code:
`python3 reposage/bootstrap.py summarize plan .` then `summarize run .`.
**The AI's layer always wins** over the folder-name guess.

### How it works
| Step | File | What it does |
|------|------|--------------|
| 1 | `reposage/scanner.py` | lists source files (respects `.gitignore`), hashes each one |
| 2 | `reposage/cache.py` | reuses parse results for files whose hash didn't change |
| 3 | `reposage/parsers/*.py` | Tree-sitter parses one file → definitions, imports, calls |
| 4 | `reposage/graph_builder.py` | links names across files → nodes + edges, sorted |
| 5 | `reposage/summarize.py` + `agents/summarizer.md` | AI summaries, layers and tags (batched, incremental) |
| 6 | `reposage/dashboard/` | packs the graph + page into `dashboard.html` (`template.html` is the app) |

The output is **deterministic**: the same code always gives a byte-identical
`graph.json` (no timestamps, no absolute paths, everything sorted). Each
`calls` edge has a `confidence`:
- `high`: resolved exactly, through an import, the same file, `self`/`this`, or a declared Java type.
- `medium`: `obj.method()` where the type of `obj` is unknown, but only one method with that name exists in the repo.

The `summary`, `layer` and `tags` fields are filled in by `/reposage:summarize`.
They are kept across re-scans for files that didn't change.

### Sharing `graph.json` with your team
`.reposage/` contains its own `.gitignore`:
- **`graph.json` is not ignored.** Commit it if you want your team to share one
  map of the codebase. It only changes when code changes, so diffs stay
  meaningful, and teammates can use it without scanning first.
- **`cache/`, `ai/` and `dashboard.html` are ignored.** They are local files
  that RepoSage rebuilds whenever it needs to (`ai/` holds copies of your code
  sent for summarizing). Summaries themselves live in `graph.json`, so a
  committed graph shares them with the team.
- **To keep everything private**, replace the contents of `.reposage/.gitignore`
  with a single `*` line (or add `.reposage/` to your root `.gitignore`).
  RepoSage never overwrites that file once it exists.

### Tests
```bash
~/.reposage/venv/bin/python -m unittest discover -s tests -t .
# optional, needs Node + Playwright: click through a dashboard in a real browser
node tests/browser_smoke.js .reposage/dashboard.html
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
- `.claude-plugin/` — Claude Code plugin manifest + marketplace entry
- `skills/` — the plugin's skills (`/reposage:scan`, ...), one folder each
- `agents/summarizer.md` — instructions for the AI that writes summaries
- `reposage/` — plugin engine (Tree-sitter parsers, graph builder, cache, bootstrap)
- `requirements-plugin.txt` — plugin dependencies (installed into `~/.reposage/venv`)
- `tests/` — plugin tests and small fixture projects
- `LICENSE` — MIT (bundled d3 modules: ISC, see `reposage/dashboard/vendor/LICENSE-d3.txt`)
