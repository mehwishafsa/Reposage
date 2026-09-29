---
name: summarize
description: Add AI-written plain-English summaries, architectural layers (UI, API, Service, Data, Utility) and tags to the RepoSage knowledge graph, then refresh the dashboard. Incremental - only new or changed files are summarized. Use when the user asks to summarize, explain or classify the codebase, or to add summaries and layers to RepoSage.
argument-hint: "[path] [--include-tests] [--limit N] [--force] [--yes]"
allowed-tools: Bash(python3:*), Bash(python:*)
---

# RepoSage summarize

Adds a short plain-English summary, a layer and tags to each source file,
and one-line summaries to its key functions and classes. The writing is done
by a small, cheap Claude model (Haiku) following `agents/summarizer.md`;
RepoSage batches the files and sends them itself.

A path argument (if any) replaces the `.` in every command below. If
`python3` is not found, use `python`.

## Step 1: make a plan

Run with the Bash tool (timeout **600000 ms**; the first RepoSage run sets
up its environment):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" summarize plan . $ARGUMENTS
```

If the plan says **"Nothing to do"**, tell the user every file is already
summarized and stop.

## Step 2: confirm the cost

Show the user the plan in 3-4 lines: how many files and batches, what was
skipped, and the estimated tokens. Then **ask whether to go ahead**, unless
the arguments contained `--yes`. Mention `--limit N` to do only the N most
important files first. Stop if the user says no.

## Step 3: summarize

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" summarize run .
```

Use a timeout of **600000 ms**. It sends 4 batches at a time and saves each
answer as soon as it arrives, printing one line per batch and the real cost
at the end. If it stops early (timeout) or reports incomplete batches, run
the same command once more: it continues with the unfinished batches only.

## Step 4: show the result

Rebuild and open the dashboard so the summaries and real layers appear:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" dashboard .
```

Finish with a short report (under ~10 lines): how many files and functions
were summarized, what it cost, and 2-3 example summaries of the most
important files (read them from `.reposage/graph.json`). If the dashboard
didn't open by itself, give the `file://` link it printed.

Re-running `/reposage:summarize` later only summarizes files that changed.
