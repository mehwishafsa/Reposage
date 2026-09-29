"""Architectural layers: UI, API, Service, Data, Utility (+ Tests).

The real classification is written into graph.json by an AI agent later
(the `layer` field). Until then, `guess_layer` makes a transparent,
deterministic guess from the file's path and name, e.g.
    src/components/Button.tsx    -> UI
    app/routes/users.py          -> API
    shop/models.py               -> Data
Anything we can't place is "Unclassified" -- we'd rather say "don't know"
than guess wildly.
"""

from __future__ import annotations

import re

# Display order, top to bottom: callers sit above the things they call.
LAYERS = ("UI", "API", "Service", "Data", "Utility")
UNCLASSIFIED = "Unclassified"
TESTS = "Tests"
ALL_GROUPS = LAYERS + (UNCLASSIFIED, TESTS)

# Folder names (any path segment) that suggest a layer.
_FOLDER_HINTS = {
    "UI": {"ui", "components", "component", "views", "view", "pages", "page",
           "screens", "widgets", "templates", "layouts", "frontend", "client",
           "static", "public", "styles", "hooks"},
    "API": {"api", "apis", "routes", "router", "routers", "controllers",
            "controller", "handlers", "endpoints", "rest", "graphql",
            "resolvers", "server", "web", "cli", "commands", "middleware"},
    "Service": {"services", "service", "core", "domain", "business", "logic",
                "usecases", "engine", "workers", "jobs", "tasks"},
    "Data": {"models", "model", "db", "database", "databases", "repository",
             "repositories", "repo", "repos", "dao", "entities", "entity",
             "schema", "schemas", "migrations", "store", "stores", "storage",
             "persistence", "orm", "data"},
    "Utility": {"utils", "util", "utilities", "helpers", "helper", "common",
                "shared", "lib", "libs", "tools", "support", "misc", "internal"},
}

# File-name patterns (checked against the name without extension).
_NAME_HINTS = [
    ("API", re.compile(r"^(main|app|server|cli|__main__|index|routes?|api|urls|views)$"
                       r"|(controller|handler|resource|endpoint|router)s?$", re.I)),
    ("Data", re.compile(r"^(models?|db|database|schema|entities|store)$"
                        r"|(repository|repo|dao|entity|model|schema|record)s?$", re.I)),
    ("Service", re.compile(r"(service|manager|engine|processor|provider|usecase)s?$", re.I)),
    ("Utility", re.compile(r"^(utils?|helpers?|common|constants?|config|settings)$"
                           r"|(util|utils|helper|helpers)$", re.I)),
]

_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e"}
_TEST_FILE = re.compile(r"^(test_.*|.*_test|.*\.(test|spec)|.*Tests?|conftest)$")


def guess_layer(path: str) -> str:
    """Best guess of a file's layer from its path. Deterministic."""
    parts = path.lower().split("/")
    name = path.rsplit("/", 1)[-1]
    base = name.rsplit(".", 1)[0]          # "user.test.ts" -> "user.test"
    stem = base.split(".")[0]              # "user.test"    -> "user"

    if any(p in _TEST_DIRS for p in parts[:-1]) or _TEST_FILE.match(base):
        return TESTS
    if name.endswith((".tsx", ".jsx")):
        return "UI"
    # The file name is the most specific hint...
    for layer, pattern in _NAME_HINTS:
        if pattern.search(stem):
            return layer
    # ...then the nearest folder wins (deepest first).
    for folder in reversed(parts[:-1]):
        for layer in LAYERS:
            if folder in _FOLDER_HINTS[layer]:
                return layer
    return UNCLASSIFIED
