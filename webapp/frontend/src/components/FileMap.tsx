import { useMemo } from "react";
import type { Overview } from "../api";

const LANG_COLOR: Record<string, string> = {
  python: "var(--c-py)", javascript: "var(--c-js)", typescript: "var(--c-ts)", java: "var(--c-java)", c: "var(--c-c)",
};

const W = 168, H = 50, GAP_X = 28, GAP_Y = 64, PAD = 24;

interface Placed { path: string; x: number; y: number; level: number }

/** Files as blocks, arranged in rows: the starting file on top, the files
 *  it uses below it, and so on. Arrows point from a file to the file whose
 *  code it uses. Same project -> same picture (no randomness). */
export default function FileMap({ data, selected, onSelect }: { data: Overview; selected: string | null; onSelect: (p: string) => void }) {
  const layout = useMemo(() => buildLayout(data), [data]);
  const byPath = Object.fromEntries(layout.nodes.map((n) => [n.path, n]));
  const files = Object.fromEntries(data.files.map((f) => [f.path, f]));
  const start = data.start_here.file;

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" style={{ maxWidth: layout.width, minWidth: Math.min(layout.width, 560), display: "block", margin: "0 auto" }}
           role="img" aria-label={`Map of ${data.files.length} files and how they use each other`}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="13" markerHeight="13" markerUnits="userSpaceOnUse" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--stone)" />
          </marker>
        </defs>
        {data.map.links.map((l) => {
          const a = byPath[l.source], b = byPath[l.target];
          if (!a || !b) return null;
          const [x1, y1, x2, y2] = a.y < b.y ? [a.x + W / 2, a.y + H, b.x + W / 2, b.y]
                                 : a.y > b.y ? [a.x + W / 2, a.y, b.x + W / 2, b.y + H]
                                 : [a.x + (a.x < b.x ? W : 0), a.y + H / 2, b.x + (a.x < b.x ? 0 : W), b.y + H / 2];
          const bend = a.y === b.y ? 0 : (y2 - y1) / 2;
          const d = a.y === b.y
            ? `M${x1},${y1} C${(x1 + x2) / 2},${y1 - 34} ${(x1 + x2) / 2},${y2 - 34} ${x2},${y2}`
            : `M${x1},${y1} C${x1},${y1 + bend} ${x2},${y2 - bend} ${x2},${y2}`;
          const hot = selected === l.source || selected === l.target;
          const label = l.calls ? `${l.source} calls ${l.calls} function${l.calls > 1 ? "s" : ""} in ${l.target}` : `${l.source} includes/imports ${l.target}`;
          return (
            <path key={`${l.source}>${l.target}`} d={d} fill="none" markerEnd="url(#arrow)"
                  stroke={hot ? "var(--gold-ink)" : "var(--stone)"} strokeWidth={l.calls ? 2 + Math.min(l.calls, 3) : 2}
                  strokeDasharray={l.calls ? undefined : "6 5"} opacity={selected && !hot ? 0.35 : 0.9}>
              <title>{label}</title>
            </path>
          );
        })}
        {layout.nodes.map((n) => {
          const f = files[n.path];
          const isStart = n.path === start;
          const isHeader = n.path.endsWith(".h");
          const name = n.path.split("/").pop()!;
          const folder = n.path.includes("/") ? n.path.slice(0, n.path.lastIndexOf("/") + 1) : "";
          return (
            <g key={n.path} transform={`translate(${n.x},${n.y})`} className="cursor-pointer focusable" tabIndex={0} role="button"
               aria-label={`${n.path}${isStart ? ", the program starts here" : ""}. ${f?.functions.length ?? 0} functions.`}
               onClick={() => onSelect(n.path)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(n.path); } }}>
              <rect x={4} y={4} width={W} height={H} fill="var(--drop)" />
              <rect width={W} height={H} fill={isStart ? "var(--gold)" : isHeader ? "var(--surface-2)" : "var(--surface)"}
                    stroke={selected === n.path ? "var(--gold-ink)" : "var(--edge)"} strokeWidth={selected === n.path ? 4 : 3} />
              <rect x={3} y={3} width={W - 6} height={6} fill={LANG_COLOR[f?.language ?? ""] ?? "var(--stone)"} />
              <text x={10} y={folder ? 26 : 33} fontSize={14} fontWeight={700} fill={isStart ? "#2b2233" : "var(--ink)"} className="mono">
                {name.length > 19 ? name.slice(0, 18) + "…" : name}
              </text>
              {folder && <text x={10} y={42} fontSize={11} fill={isStart ? "#4a3d55" : "var(--muted)"}>{folder.length > 24 ? "…" + folder.slice(-23) : folder}</text>}
              <text x={W - 8} y={folder ? 26 : 33} fontSize={11} textAnchor="end" fill={isStart ? "#4a3d55" : "var(--muted)"}>
                {f ? `${f.functions.length} fn` : ""}
              </text>
              {isStart && (
                <g transform={`translate(${W - 16},-16)`}>
                  <rect x={0} y={0} width={3} height={22} fill="var(--edge)" />
                  <rect x={3} y={0} width={14} height={9} fill="var(--danger)" />
                </g>
              )}
            </g>
          );
        })}
      </svg>
      <p className="muted text-sm mt-2 mb-0">
        <span className="inline-block w-3 h-3 align-middle mr-1" style={{ background: "var(--gold)", border: "2px solid var(--edge)" }} /> starts the program
        <span className="mx-2">·</span>solid arrow = calls functions in that file
        <span className="mx-2">·</span>dashed = only imports / #includes it
      </p>
    </div>
  );
}

function buildLayout(data: Overview): { nodes: Placed[]; width: number; height: number } {
  const paths = data.files.map((f) => f.path).sort();
  // Each file goes one row below the lowest file that uses it (longest path
  // from the start), so arrows point downwards. Bounded loop: cycles are fine.
  const level = new Map<string, number>();
  const start = data.start_here.file;
  if (start) {
    level.set(start, 0);
    for (let round = 0, changed = true; changed && round < paths.length; round++) {
      changed = false;
      for (const l of [...data.map.links].sort((a, b) => (a.source + a.target).localeCompare(b.source + b.target))) {
        const from = level.get(l.source);
        if (from === undefined || l.target === start) continue;
        if ((level.get(l.target) ?? -1) < from + 1 && from + 1 < paths.length) { level.set(l.target, from + 1); changed = true; }
      }
    }
  }
  // Files not reached from the start: place them by what they use, below the rest.
  let deepest = Math.max(-1, ...level.values());
  const rest = paths.filter((p) => !level.has(p));
  if (rest.length) deepest += 1;
  const rows = new Map<number, string[]>();
  for (const p of paths) {
    const lv = level.get(p) ?? deepest;
    rows.set(lv, [...(rows.get(lv) ?? []), p]);
  }
  // wrap very wide rows so the map stays readable
  const MAX_PER_ROW = 6;
  const finalRows: string[][] = [];
  for (const lv of [...rows.keys()].sort((a, b) => a - b)) {
    const row = rows.get(lv)!;
    for (let i = 0; i < row.length; i += MAX_PER_ROW) finalRows.push(row.slice(i, i + MAX_PER_ROW));
  }
  const widest = Math.max(1, ...finalRows.map((r) => r.length));
  const width = PAD * 2 + widest * W + (widest - 1) * GAP_X;
  const nodes: Placed[] = [];
  finalRows.forEach((row, r) => {
    const rowWidth = row.length * W + (row.length - 1) * GAP_X;
    const x0 = (width - rowWidth) / 2;
    row.forEach((p, i) => nodes.push({ path: p, x: x0 + i * (W + GAP_X), y: PAD + 10 + r * (H + GAP_Y), level: r }));
  });
  return { nodes, width, height: PAD * 2 + 10 + finalRows.length * H + (finalRows.length - 1) * GAP_Y };
}
