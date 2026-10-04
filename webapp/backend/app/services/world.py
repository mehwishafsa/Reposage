"""Turn a code graph into a small blocky world ("Play the Program").

    folders            -> regions   (big areas of land)
    files              -> villages  (a plaza with a sign)
    functions/classes  -> buildings (taller = more lines of code)
    calls              -> roads between buildings
    imports            -> main roads between villages

The world is DETERMINISTIC: the same code always gives the same world.
Everything is sorted, roads are routed in a fixed order, and the few
decorations (trees, flowers) come from a random generator seeded with a
hash of the code graph - so "random" looks natural but never changes.

Input : graph.json written by the reposage engine.
Output: a plain dict (JSON) that the frontend draws as isometric tiles.

Run it by hand:  python -m app.services.world path/to/graph.json > world.json
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import random
import sys
from collections import defaultdict

SYMBOL_TYPES = ("class", "interface", "enum", "record", "function", "method")

PLOT = 4            # each building gets a 4x4 plot (footprint + space for a path)
VILLAGE_GAP = 4     # tiles of open land between villages (room for roads)
REGION_GAP = 5
MAX_HEIGHT = 8      # blocks


def build_world(graph: dict) -> dict:
    nodes = {n["id"]: n for n in graph["nodes"]}
    files = sorted(n["id"] for n in graph["nodes"] if n["type"] == "file")
    symbols_by_file: dict[str, list[dict]] = defaultdict(list)
    for n in graph["nodes"]:
        # Only top-level functions/classes become buildings; methods live
        # inside their class building (one building per class keeps it readable).
        if n["type"] in SYMBOL_TYPES and not n.get("parent"):
            symbols_by_file[n["path"]].append(n)
    for lst in symbols_by_file.values():
        lst.sort(key=lambda n: (n["start_line"], n["id"]))

    seed = int(hashlib.sha256("\n".join(sorted(nodes)).encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)

    # ---- 1. regions (folders), villages (files), buildings (functions) ----
    by_folder: dict[str, list[str]] = defaultdict(list)
    for f in files:
        by_folder[f.rsplit("/", 1)[0] if "/" in f else "(root)"].append(f)

    regions, villages, buildings = [], [], []
    region_x = 2
    for folder in sorted(by_folder):
        vfiles = by_folder[folder]
        cols = max(1, math.ceil(math.sqrt(len(vfiles))))
        sizes = [_village_size(len(symbols_by_file[f])) for f in vfiles]
        col_w = [0] * cols
        row_h = [0] * math.ceil(len(vfiles) / cols)
        for k, (w, d) in enumerate(sizes):
            col_w[k % cols] = max(col_w[k % cols], w)
            row_h[k // cols] = max(row_h[k // cols], d)
        for k, f in enumerate(vfiles):
            c, r = k % cols, k // cols
            vx = region_x + 2 + sum(col_w[:c]) + VILLAGE_GAP * c
            vy = 3 + sum(row_h[:r]) + VILLAGE_GAP * r
            w, d = sizes[k]
            village = {"id": f, "name": f.rsplit("/", 1)[-1], "x": vx, "y": vy, "w": w, "d": d,
                       "summary": nodes[f].get("summary") or nodes[f].get("doc") or ""}
            villages.append(village)
            buildings += _place_buildings(village, symbols_by_file[f])
        region_w = sum(col_w) + VILLAGE_GAP * (cols - 1) + 4
        region_d = sum(row_h) + VILLAGE_GAP * (len(row_h) - 1) + 5
        regions.append({"id": folder, "name": folder.rsplit("/", 1)[-1] + "/", "x": region_x,
                        "y": 1, "w": region_w, "d": region_d})
        region_x += region_w + REGION_GAP

    width = max(r["x"] + r["w"] for r in regions) + 2 if regions else 10
    depth = max(r["y"] + r["d"] for r in regions) + 2 if regions else 10

    # ---- 2. roads: calls between buildings, imports between villages ----
    blocked = set()
    for b in buildings:
        for x in range(b["x"], b["x"] + b["w"]):
            for y in range(b["y"], b["y"] + b["d"]):
                blocked.add((x, y))
    by_id = {b["id"]: b for b in buildings}
    top_of = {}                      # method id -> id of its top-level building
    for n in graph["nodes"]:
        if n["type"] in SYMBOL_TYPES:
            top = n
            while top.get("parent") and top["parent"] in nodes:
                top = nodes[top["parent"]]
            top_of[n["id"]] = top["id"]

    road_tiles: dict[tuple, int] = {}
    roads = []
    seen = set()
    calls = sorted((e for e in graph["edges"] if e["type"] == "calls"),
                   key=lambda e: (e["source"], e["target"]))
    for e in calls:
        a, b = top_of.get(e["source"]), top_of.get(e["target"])
        if a not in by_id or b not in by_id or a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        cells = _route(by_id[a]["door"], by_id[b]["door"], blocked, road_tiles, width, depth)
        for c in cells:
            road_tiles[tuple(c)] = road_tiles.get(tuple(c), 0) + 1
        roads.append({"kind": "call", "from": a, "to": b, "cells": cells,
                      "guessed": e.get("confidence") != "high"})
    vby = {v["id"]: v for v in villages}
    for e in sorted((e for e in graph["edges"] if e["type"] == "imports"),
                    key=lambda e: (e["source"], e["target"])):
        a, b = vby.get(e["source"]), vby.get(e["target"])
        if not a or not b:
            continue
        cells = _route(a["gate"], b["gate"], blocked, road_tiles, width, depth)
        for c in cells:
            road_tiles[tuple(c)] = road_tiles.get(tuple(c), 0) + 1
        roads.append({"kind": "import", "from": a["id"], "to": b["id"], "cells": cells, "guessed": False})

    # ---- 3. decoration: trees and flowers on empty grass (seeded) ----
    plaza = set()
    for v in villages:
        for x in range(v["x"], v["x"] + v["w"]):
            for y in range(v["y"], v["y"] + v["d"]):
                plaza.add((x, y))
    decor = []
    for y in range(depth):
        for x in range(width):
            p = rng.random()
            if (x, y) in blocked or (x, y) in road_tiles or (x, y) in plaza:
                continue
            near_edge = x < 2 or y < 1 or x > width - 3 or y > depth - 2
            if p < (0.30 if near_edge else 0.07):
                decor.append({"x": x, "y": y, "kind": "tree", "v": rng.randrange(3)})
            elif p < (0.36 if near_edge else 0.12):
                decor.append({"x": x, "y": y, "kind": "flower", "v": rng.randrange(3)})

    return {"format": 1, "seed": seed, "width": width, "depth": depth,
            "regions": regions, "villages": villages, "buildings": buildings,
            "roads": roads, "decor": decor}


def _village_size(n_buildings: int) -> tuple[int, int]:
    cols = min(3, max(1, n_buildings))
    rows = max(1, math.ceil(n_buildings / cols))
    return cols * PLOT + 1, rows * PLOT + 2          # +2: a row for the sign and gate


def _place_buildings(village: dict, symbols: list[dict]) -> list[dict]:
    """Buildings in reading order (top of the file = first plot)."""
    out = []
    cols = min(3, max(1, len(symbols)))
    for k, s in enumerate(symbols):
        c, r = k % cols, k // cols
        lines = s["end_line"] - s["start_line"] + 1
        is_class = s["type"] in ("class", "interface", "enum", "record")
        w = d = 3 if is_class else 2
        x = village["x"] + 1 + c * PLOT
        y = village["y"] + 2 + r * PLOT
        out.append({
            "id": s["id"], "name": s["name"], "kind": s["type"], "file": s["path"],
            "x": x, "y": y, "w": w, "d": d,
            "h": max(1, min(MAX_HEIGHT, 1 + lines // 3)),   # taller = more lines of code
            "lines": lines, "start": s["start_line"], "end": s["end_line"],
            "door": (x + w // 2, y + d),                    # the tile in front of the door
            "summary": s.get("summary") or s.get("doc") or "",
        })
    village["gate"] = (village["x"] + village["w"] // 2, village["y"] + village["d"])
    return out


def _route(start, goal, blocked, road_tiles, width, depth) -> list:
    """Shortest path on the tile grid (A*). Existing roads are cheaper, so
    roads join up into shared streets instead of running side by side."""
    start, goal = tuple(start), tuple(goal)
    frontier = [(0, 0, start)]
    came = {start: None}
    cost = {start: 0}
    while frontier:
        _, g, cur = heapq.heappop(frontier)
        if cur == goal:
            break
        x, y = cur
        for nxt in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):   # fixed order
            nx, ny = nxt
            if not (0 <= nx < width and 0 <= ny < depth) or (nxt in blocked and nxt != goal):
                continue
            step = 0.4 if nxt in road_tiles else 1.0
            ng = g + step
            if nxt not in cost or ng < cost[nxt] - 1e-9:
                cost[nxt] = ng
                came[nxt] = cur
                h = abs(nx - goal[0]) + abs(ny - goal[1])
                heapq.heappush(frontier, (ng + h * 0.4, ng, nxt))
    if goal not in came:
        return []
    path, cur = [], goal
    while cur is not None:
        path.append(list(cur))
        cur = came[cur]
    return path[::-1]


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        print(json.dumps(build_world(json.load(fh)), indent=1))
