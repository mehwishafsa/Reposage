---
name: diff
description: Explain what the current git changes affect - the changed functions, everything that calls them directly and indirectly (ripple effect), which tests cover them and which affected code has no tests - with a Low/Medium/High risk level, in plain English. Shows the result on the dashboard (changed code, directly and indirectly affected). Use when the user asks what their changes affect, what could break, whether a change is risky, or before committing or opening a pull request.
argument-hint: "[--base <branch>] [--depth N]"
allowed-tools: Bash(python3:*), Bash(python:*)
---

# RepoSage diff

Without arguments this looks at **uncommitted changes** (staged, unstaged
and new files). With `--base main` it looks at everything since this branch
left `main`, including uncommitted work.

## Step 1: analyse the changes

Run with the Bash tool (timeout **600000 ms**; the first RepoSage run sets
up its environment):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" diff $ARGUMENTS
```

If `python3` is not found, use `python`. If it says there are no changes,
tell the user and stop.

The output is an **impact pack**: the computed risk with its reasons, the
changed functions (with their diff), the functions that call them by depth
(with the exact calling line), which tests reach them, and removed
functions that are still called.

## Step 2: explain, for a new team member

Use **only the impact pack** (don't open other files). Plain words, short
sentences, cite `file:line`. About 15-20 lines:

1. **What changed**: 1-3 sentences per changed function, in plain English.
2. **What could break**: for the direct callers, look at the calling line
   and the diff. Does the change alter what the caller gets back or has to
   pass in? Say concretely what might go wrong. Mention the indirect callers
   briefly. Point out links marked **GUESSED**.
3. **Tests**: which tests exercise the change, and which affected
   functions have **no tests** (list them).
4. **Risk: Low / Medium / High**, with the reason. Start from the computed
   risk. You may move it one level if the diff clearly justifies it (e.g. a
   new parameter with a default value keeps callers working), but say why.
5. One suggestion: the most useful test to add, or what to check by hand.

## Step 3: save the explanation to the dashboard

Add a 1-2 sentence plain-English summary (no double quotes, backticks or
`$`) to the change view, which rebuilds the dashboard:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" diff --note "verify_password now rejects short passwords; login and everything above it inherit that."
```

End your reply with the link it prints (**Change view:** `<link>`). The
view shows changed code in red with a Δ, directly affected code in violet,
indirectly affected code faded, and guessed links dashed.
