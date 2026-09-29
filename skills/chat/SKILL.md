---
name: chat
description: Answer a question about this codebase in plain English, using only the relevant code that RepoSage finds (search over code, AI summaries and tags, then callers/callees in the call graph), with file:line citations and an honest confidence note. Saves an Answer Path that the dashboard shows as a numbered route. Use when the user asks how something works, where something happens, or what happens when something occurs in this repository.
argument-hint: "<question>"
allowed-tools: Bash(python3:*), Bash(python:*)
---

# RepoSage chat

If no question was given, ask the user for one and stop.

## Step 1: find the relevant code

Run with the Bash tool (timeout **600000 ms**; the first RepoSage run sets up
its environment). Put the question in double quotes; replace any double
quotes inside it with single quotes:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" chat ask "$ARGUMENTS"
```

If `python3` is not found, use `python`. The output is a **context pack**:
numbered items `[1]`, `[2]`, … each with its location, how it was found,
its AI summary and its code with line numbers, then the calls between them.

## Step 2: answer

- Use **only the context pack**. Don't open other files or search further:
  that keeps the answer grounded and cheap. If the pack doesn't answer the
  question, say so plainly and suggest other words to try.
- Write for a **new team member**: simple words, short sentences; explain
  any unavoidable jargon in a few words.
- Shape (about 15 lines at most):
  1. A direct answer in 1-3 sentences.
  2. **Step by step**: a numbered list in the order things happen. Each
     step says what happens and cites `file:line` (use the line numbers
     shown in the pack).
  3. **How sure**: one line. If a step uses an item or call marked
     **GUESSED** (matched by name only), name it and say it should be
     checked. If everything is a real call, say so briefly.

## Step 3: save the Answer Path

Record the items your step-by-step list used, in the same order: 3-8
steps, each as `"<item number>:<short label>"` (at most ~8 words, no double
quotes, backticks or `$`). Add the direct answer (1-2 sentences) with
`--answer`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/reposage/bootstrap.py" chat path "3:route sends the request to the handler" "1:login checks the password" --answer "Login looks up the user, checks the password and returns a token."
```

It rebuilds the dashboard and prints a link that opens it with this path
highlighted. End your reply with that link (**Answer Path:** `<link>`); if
it says the browser opened, just mention it. Saved paths can be reopened
later from the **Answer paths** menu of `/reposage:dashboard`.
