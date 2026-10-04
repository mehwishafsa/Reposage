"""All settings in one place, read from environment variables.

Nothing secret lives in the code: API keys come only from the server's
environment (e.g. Render's "Environment" page) and are never sent to the
browser.
"""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(BACKEND_DIR))

# The analysis engine is the `reposage` package at the repository root.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


# ---- storage --------------------------------------------------------------
DATA_DIR = os.environ.get("REPOSAGE_DATA", os.path.join(BACKEND_DIR, "data"))
FRONTEND_DIST = os.environ.get(
    "REPOSAGE_FRONTEND", os.path.join(REPO_ROOT, "webapp", "frontend", "dist"))
SAMPLES_DIR = os.path.join(REPO_ROOT, "webapp", "samples")

# ---- upload limits (safety) ----------------------------------------------
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_MB", 20) * 1024 * 1024   # what the user sends
MAX_UNPACKED_BYTES = _int("MAX_UNPACKED_MB", 60) * 1024 * 1024  # after unzipping (zip bombs)
MAX_FILES = _int("MAX_FILES", 500)                           # source files kept
MAX_FILE_BYTES = 1_000_000                                   # one file
MAX_PASTE_CHARS = 200_000
MAX_PROJECTS_KEPT = _int("MAX_PROJECTS_KEPT", 200)           # oldest are deleted

# ---- AI provider ----------------------------------------------------------
# AI_PROVIDER picks the main provider, AI_FALLBACK the one tried when the main
# one is out of quota. Switching needs no code change, only these variables.
#   gemini    -> GEMINI_API_KEY   (free tier, default)
#   groq      -> GROQ_API_KEY     (free tier, fallback)
#   anthropic -> ANTHROPIC_API_KEY (optional, paid)
#   fake      -> no key; canned answers for tests and offline development
AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").strip().lower()
AI_FALLBACK = os.environ.get("AI_FALLBACK", "groq").strip().lower()

# Two kinds of work: "fast" (summaries, many small calls) and "smart" (chat).
MODELS = {
    "gemini": {"fast": os.environ.get("GEMINI_MODEL_FAST", "gemini-2.5-flash-lite"),
               "smart": os.environ.get("GEMINI_MODEL_SMART", "gemini-2.5-flash")},
    "groq": {"fast": os.environ.get("GROQ_MODEL_FAST", "llama-3.1-8b-instant"),
             "smart": os.environ.get("GROQ_MODEL_SMART", "llama-3.3-70b-versatile")},
    "anthropic": {"fast": os.environ.get("ANTHROPIC_MODEL_FAST", "claude-haiku-4-5"),
                  "smart": os.environ.get("ANTHROPIC_MODEL_SMART", "claude-sonnet-5-5")},
    "fake": {"fast": "fake-fast", "smart": "fake-smart"},
}

# Free tiers allow only a few requests per minute and per day. We stay under
# them by spacing requests out and counting them per day (per provider).
AI_MIN_INTERVAL = _float("AI_MIN_INTERVAL_SECONDS", 4.5)   # ~13 requests/minute
AI_DAILY_LIMIT = _int("AI_DAILY_LIMIT", 900)
AI_MAX_RETRIES = _int("AI_MAX_RETRIES", 3)
AI_TIMEOUT = _float("AI_TIMEOUT_SECONDS", 60)
AI_SUMMARY_BATCH_FILES = _int("AI_SUMMARY_BATCH_FILES", 8)  # files per summary request
AI_SUMMARY_MAX_FILES = _int("AI_SUMMARY_MAX_FILES", 60)     # per project (most important first)


def api_key(provider: str) -> str:
    return os.environ.get({"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY",
                           "anthropic": "ANTHROPIC_API_KEY"}.get(provider, ""), "").strip()
