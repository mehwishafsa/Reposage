# RepoSage web app

A beginner-friendly website version of RepoSage: upload code (paste, files,
.zip or a public GitHub link) and get a plain-English tour of it.

It uses the same analysis engine as the Claude Code plugin (the `reposage`
package at the root of this repository), so Python, JavaScript, TypeScript,
Java and **C** are understood the same way in both.

```
webapp/
  backend/            FastAPI + SQLite
    app/main.py         API routes, serves the built frontend
    app/config.py       every setting, from environment variables
    app/db.py           projects, AI cache, daily AI usage
    app/services/
      ingest.py         safe upload handling (limits, path tricks, secrets)
      analyze.py        engine -> overview, "Start here", file map (no AI)
      ai_notes.py       plain-English summaries (AI, in the background)
      llm.py            one interface for Gemini / Groq / Claude / fake
      projects.py       project lifecycle and background jobs
      world.py          "Play the Program" world generator (game mode, later)
    tests/              run with the fake AI, no network
  frontend/           React + Vite + TypeScript + Tailwind ("Blocky" theme)
  samples/            miniauth (Python), calculator-c (C), todo-python
  mockups/            game-mode mockup
```

## Run it locally

```bash
# backend
cd webapp/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
AI_PROVIDER=fake .venv/bin/uvicorn app.main:app --reload     # http://localhost:8000

# frontend (second terminal)
cd webapp/frontend
npm install
npm run dev          # http://localhost:5173, forwards /api to :8000
npm run build        # or build once; the backend then serves it on :8000
```

Tests: `cd webapp/backend && .venv/bin/python -m unittest discover -s tests -t .`

## AI providers (switch with environment variables, no code changes)

| Variable | Meaning |
|---|---|
| `AI_PROVIDER` | `gemini` (default, free tier), `groq` (free tier), `anthropic` (optional, paid) or `fake` (tests) |
| `AI_FALLBACK` | provider tried when the main one is out of quota (default `groq`) |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY` | keys, **only** in the server environment |
| `GEMINI_MODEL_FAST` / `_SMART` (same for `GROQ_`, `ANTHROPIC_`) | override the models |
| `AI_DAILY_LIMIT` | stop sending requests after this many per provider per day (default 900) |
| `AI_MIN_INTERVAL_SECONDS` | spacing between requests to one provider (default 4.5 s) |

Free-tier friendly: requests are queued one at a time, retried politely on
"slow down" answers, counted per day, and every answer is cached in SQLite.
If no AI is available, everything that doesn't need AI keeps working
(overview from the code's comments, file map, Start here), and the page
says so in plain words.

## Safety

- Uploaded code is **never run** - only read and parsed with Tree-sitter.
- Limits: 20 MB upload, 60 MB unpacked, 500 code files, 1 MB per file.
- Refused: absolute paths, `..`, drive letters and links inside zips.
- Skipped and never stored: `node_modules`/build folders, binaries,
  `.env` files, private keys and credential files.
- Max 30 new projects per visitor per hour; old projects are deleted.
- API keys never reach the browser.

## Deploy on Render (free plan)

1. Render dashboard -> **New** -> **Blueprint** -> choose this repository
   (it reads `render.yaml` at the root; branch `fullstack-app`).
2. When asked, paste `GEMINI_API_KEY` (and `GROQ_API_KEY` if you have one).
3. Wait for the Docker build; the app is then live at `https://<name>.onrender.com`.

On the free plan the service sleeps after 15 minutes without visitors (the
first visit then takes about a minute) and its disk is temporary, so
uploaded projects and the AI cache are cleared when it restarts.
