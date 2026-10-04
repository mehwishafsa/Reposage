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
      chat_agent.py     "Ask RepoSage": agentic RAG with read-only tools and live steps
      flowchart.py      flowcharts from the syntax tree (no AI)
      explain.py        plain-English explanations from code patterns (no AI)
      code_view.py      code explainer: file blocks and function views
      explain_ai.py     AI rewrites of explanations (normal / simpler)
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
| `CHAT_MAX_AI_CALLS` | most AI calls the chat agent may make for one question (default 4) |
| `CHAT_MODEL_TIER` | `smart` (default) or `fast` model for the chat agent |
| `MAX_QUESTIONS_PER_HOUR` | chat questions per visitor per hour (default 60) |
| `GEMINI_ENDPOINT` | `auto` (default), `aistudio` or `vertex`; see "Gemini keys" below |
| `LOG_LEVEL` | `INFO` (default) logs one line per AI request and the reason when one fails |

### Gemini keys and checking the AI

Google issues two kinds of keys, and each only works with its own endpoint:

- `AIza...`: a Google AI Studio key, used with `generativelanguage.googleapis.com`.
- `AQ....`: a Vertex AI express-mode key, used with `aiplatform.googleapis.com`.

RepoSage tries the endpoint that fits the key first, falls back to the other one,
and remembers which one works. If a model has been retired, it asks Google
which models the key can use and switches to the closest one (shown in the log).

To check the setup, open `/api/ai/check` on the running server. It sends one
tiny request and reports the provider, model, endpoint, the key's type and
length (never the key itself) and, if it failed, the reason: `auth` (bad key or
wrong endpoint), `model`, `region`, `rate_limit`, `daily_limit` or `bad_request`.
The same reasons appear in the server log as `AI request failed: ... reason=...`.

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
