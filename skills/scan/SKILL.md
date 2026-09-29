---
name: scan
description: Scan the current repository with Tree-sitter and build or update the RepoSage knowledge graph (.reposage/graph.json) of files, classes, functions, imports and calls. Use when the user asks to scan, index or map the codebase, or before other RepoSage skills if .reposage/graph.json is missing.
argument-hint: "[path] [--full]"
allowed-tools: Bash(python3:*), Bash(python:*), Read
---

# RepoSage scan

Build (or incrementally update) the RepoSage knowledge graph for this repository.

## Step 1: run the scanner

Run this command with the Bash tool. Use a **timeout of 600000 ms**, because
the very first run creates RepoSage's private Python environment (about a minute):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" scan $ARGUMENTS
```

- If no arguments were given, the current directory is scanned.
- If `python3` is not found (common on Windows), run the same command with `python`.
- If the output starts with "First run: setting up RepoSage", tell the user this
  is a one-time setup before showing the results.

## Step 2: report back

Give the user a short summary based on the scanner output:

1. How many files were scanned per language, and how many were re-parsed vs.
   reused from the cache (incremental scan).
2. How many classes, functions/methods, call links and import links were found.
3. Where the graph was saved (`.reposage/graph.json`).

Then, to make the result tangible, read `.reposage/graph.json` and name the
3–5 most connected functions (the most `calls` edges in + out). These are
usually the heart of the codebase. Keep the whole reply under ~15 lines, and
end by suggesting `/reposage:dashboard` to explore the graph visually.

If the scanner printed "RepoSage setup failed", show the user the error and the
hint it printed. Don't try to install packages any other way.

## Notes for the user (mention only if relevant)

- Re-run `/reposage:scan` any time; only changed files are parsed again.
- Use `/reposage:scan --full` to ignore the cache and re-parse everything.
- `.reposage/graph.json` can be committed to share one map of the codebase with
  the team. `.reposage/cache/` is local and already git-ignored.
