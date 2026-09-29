---
name: dashboard
description: Open an interactive map of the codebase in the browser, built from the RepoSage knowledge graph. Files and functions are arranged in bands by architectural layer (UI, API, Service, Data, Utility), with search, clickable nodes, summaries and dependency arrows. Use when the user wants to see, visualize, explore or browse the structure of the repository.
argument-hint: "[path]"
allowed-tools: Bash(python3:*), Bash(python:*)
---

# RepoSage dashboard

## Step 1: build and open the dashboard

Run this with the Bash tool (timeout **600000 ms**: the first run of RepoSage
sets up its environment). It refreshes the knowledge graph first (only changed
files are re-parsed), then writes `.reposage/dashboard.html` and opens it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" dashboard $ARGUMENTS
```

If `python3` is not found (common on Windows), use `python` instead.

## Step 2: tell the user

Keep it short (under ~10 lines):

1. Say whether it opened in their browser. If the output says "Open this in a
   browser", give them the `file://` link it printed. That happens on remote
   machines or when no browser is available.
2. A 3-line tour of the page:
   - **Bands** are architectural layers, callers on top, the code they use below.
   - **Click** a node for details; solid arrows = what it uses, dashed = what uses it.
   - **Files / Symbols** switches between the file map and the function map;
     **Table** gives a sortable list; press **/** to search.
3. If the page notes that layers are "guessed from path", mention that AI
   classification and summaries arrive in a later RepoSage step.

The dashboard is a single self-contained HTML file: it works offline and can
be shared by sending the file. It is git-ignored by default.
