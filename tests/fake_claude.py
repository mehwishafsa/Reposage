#!/usr/bin/env python3
"""A stand-in for `claude -p` used by the tests (no network, no cost).

Reads a RepoSage batch on stdin and prints what `claude -p --output-format
json` would: {"result": "<the model's text>", "total_cost_usd": ...}.
FAKE_CLAUDE_MODE=flaky makes the first call for each batch return garbage,
to test the retry.
"""
import json
import os
import re
import sys

batch_text = sys.stdin.read()
batch_no = int(re.search(r"batch (\d+) of", batch_text).group(1))
if os.environ.get("FAKE_CLAUDE_MODE") == "flaky":
    marker = os.path.join(os.environ["FAKE_CLAUDE_DIR"], f"seen-{batch_no}")
    if not os.path.exists(marker):
        open(marker, "w").close()
        print(json.dumps({"result": "Sorry, something went wrong.", "total_cost_usd": 0.001}))
        sys.exit(0)

files = []
for section in batch_text.split("\n## File: ")[1:]:
    path = section.split("\n", 1)[0].strip()
    ids = re.findall(r"id: `([^`]+)`", section)
    files.append({"path": path, "summary": f"Handles the work of {path.rsplit('/', 1)[-1]}.",
                  "layer": "Service", "tags": ["demo"],
                  "symbols": [{"id": i, "summary": "Does one step of the work."} for i in ids]})
answer = "Here you go:\n```json\n" + json.dumps({"batch": batch_no, "files": files}) + "\n```"
print(json.dumps({"result": answer, "total_cost_usd": 0.002, "is_error": False}))
