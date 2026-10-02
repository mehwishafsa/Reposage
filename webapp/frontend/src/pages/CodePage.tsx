import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type CodeBlock, type ExplainStatus, type FileView, type FunctionView, type Overview } from "../api";
import CodeView from "../components/CodeView";
import Flowchart from "../components/Flowchart";
import RichText from "../components/RichText";
import { CubeBuddy } from "../components/Pixel";

type Level = "normal" | "simpler";
type Tab = "steps" | "flow";

const KIND_LABEL: Record<string, string> = {
  start: "Start", end: "End", action: "Step", decision: "Decision", switch: "Menu choice", loop: "Loop",
  try: "Try", return: "Give back", stop: "Error", about: "About", imports: "Imports", guard: "Header guard",
  prototypes: "Function list", setup: "Set-up", function: "Function", class: "Class", type: "New type",
  main: "Program start",
};
const KIND_COLOR: Record<string, string> = {
  start: "var(--grass)", end: "var(--grass)", decision: "var(--gold)", switch: "var(--gold)", loop: "var(--sky)",
  return: "var(--grass-hi)", stop: "var(--danger)", function: "var(--c-c)", imports: "var(--c-py)",
};

export default function CodePage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const path = params.get("file") ?? "";
  const fnId = params.get("fn") ?? "";

  const [overview, setOverview] = useState<Overview | null>(null);
  const [file, setFile] = useState<FileView | null>(null);
  const [fn, setFn] = useState<FunctionView | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelectedRaw] = useState<string | null>(null);
  const [userPicked, setUserPicked] = useState(false);
  const setSelected = (b: string | null) => { setSelectedRaw(b); if (b) setUserPicked(true); };
  const [level, setLevel] = useState<Level>("normal");
  const [tab, setTab] = useState<Tab>(fnId ? "flow" : "steps");
  const [ai, setAi] = useState<Record<string, ExplainStatus>>({});

  useEffect(() => { api.overview(id).then(setOverview).catch((e) => setError(e.message)); }, [id]);

  // default file: where the program starts
  useEffect(() => {
    if (!path && overview?.start_here.file) setParams({ file: overview.start_here.file }, { replace: true });
  }, [path, overview, setParams]);

  useEffect(() => {
    if (!path) return;
    setFile(null);
    api.file(id, path).then(setFile).catch((e) => setError(e.message));
  }, [id, path]);

  useEffect(() => {
    setSelectedRaw(null);
    if (!fnId) { setFn(null); return; }
    setFn(null);
    api.func(id, fnId).then((v) => { setFn(v); setSelectedRaw(v.blocks[0]?.id ?? null); })
      .catch((e) => setError(e.message));
  }, [id, fnId]);

  // AI explanations for what is on screen (normal, and "simpler" when asked)
  const target = fnId ? `fn:${fnId}` : `file:${path}`;
  const aiKey = `${target}|${level}`;
  const askAI = useCallback(async () => {
    if (!path) return;
    try {
      const r = await api.explain(id, level, fnId ? { fn: fnId } : { path });
      setAi((m) => ({ ...m, [aiKey]: r }));
    } catch { /* the pattern-based text stays */ }
  }, [id, level, fnId, path, aiKey]);
  useEffect(() => { if (!ai[aiKey]) askAI(); }, [aiKey, ai, askAI]);
  useEffect(() => {
    if (ai[aiKey]?.status !== "thinking") return;
    const t = setTimeout(askAI, 1800);
    return () => clearTimeout(t);
  }, [ai, aiKey, askAI]);

  const view = fn ?? file;
  const blocks = view?.blocks ?? [];
  const status = ai[aiKey];
  const textOf = (b: CodeBlock) => {
    const fromAI = status?.status === "done" ? status.blocks[b.id] : undefined;
    return fromAI ?? (level === "simpler" ? b.simpler : b.explain);
  };
  const chosen = blocks.find((b) => b.id === selected) ?? null;
  // ?hl=22-26 (from a chat citation): highlight those lines until a block is picked
  const hlMatch = /^(\d+)(?:-(\d+))?$/.exec(params.get("hl") ?? "");
  const hl: [number, number] | null = hlMatch ? [+hlMatch[1], +(hlMatch[2] ?? hlMatch[1])] : null;

  const pickLine = (line: number) => {
    const hits = blocks.filter((b) => b.lines[0] <= line && line <= b.lines[1]);
    const inScope = hits.length ? hits : blocks.filter((b) => b.scope[0] <= line && line <= b.scope[1]);
    const best = inScope.sort((a, b) => (a.lines[1] - a.lines[0]) - (b.lines[1] - b.lines[0]))[0];
    if (best) setSelected(best.id);
  };

  const files = overview?.files ?? [];
  const title = useMemo(() => (fn ? `${fn.name}()` : path.split("/").pop()), [fn, path]);

  if (error) return <div className="max-w-6xl mx-auto px-4 py-10"><p role="alert" className="notice">{error}</p><Link to={`/p/${id}`}>Back to the overview</Link></div>;

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <Link to={`/p/${id}`} className="btn !py-1.5 !px-3 text-sm no-underline">← Overview</Link>
        <h1 className="pixel text-[15px] sm:text-[18px] m-0">Code explainer</h1>
        <span className="muted">{overview?.project}</span>
        <span className="flex-1" />
        <Link to={`/p/${id}/ask?file=${encodeURIComponent(path)}${fnId ? `&fn=${encodeURIComponent(fnId)}` : ""}`}
              className="btn btn-gold !py-1.5 !px-3 text-sm no-underline">💬 Ask about {fn ? `${fn.name}()` : "this file"}</Link>
        <label className="text-sm font-semibold flex items-center gap-2">File
          <select className="field !py-1.5 !w-auto max-w-[60vw]" value={path}
                  onChange={(e) => setParams({ file: e.target.value })}>
            {files.map((f) => <option key={f.path} value={f.path}>{f.path}</option>)}
          </select>
        </label>
      </div>

      {file && (
        <div className="flex flex-wrap gap-2 mt-4" role="tablist" aria-label="What to explain">
          <button role="tab" aria-selected={!fnId} className="tab !py-1.5 text-sm" onClick={() => setParams({ file: path })}>
            Whole file
          </button>
          {file.functions.map((f) => (
            <button key={f.id} role="tab" aria-selected={fnId === f.id} className="tab !py-1.5 text-sm mono"
                    onClick={() => { setParams({ file: path, fn: f.id }); setTab("flow"); }}>
              {f.label}()
            </button>
          ))}
        </div>
      )}

      <div className="grid gap-5 mt-4 lg:grid-cols-2 items-start">
        <section className="tile !p-0 overflow-hidden min-w-0" aria-label="Code">
          <div className="flex items-center gap-2 px-4 py-2 border-b-3 border-[var(--edge)] bg-[var(--surface-2)]">
            <span className="mono font-bold break-all">{path}</span>
            {fn && <span className="muted text-sm">lines {fn.start}-{fn.end}</span>}
            <span className="flex-1" />
            <span className="muted text-xs hidden sm:inline">Click a line to explain it</span>
          </div>
          {file ? (
            <CodeView text={file.text} language={file.language} strong={hl && !userPicked ? hl : chosen?.lines ?? null}
                      scope={chosen && chosen.scope[1] > chosen.lines[1] ? chosen.scope : null}
                      firstLine={fn?.start ?? 1} lastLine={fn?.end} onLine={pickLine} />
          ) : <p className="p-4 muted">Loading the code<span className="dots" /></p>}
        </section>

        <section className="tile p-4 sm:p-5 min-w-0" aria-label="Explanation">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="pixel text-[12px] m-0 mr-2">{title}</h2>
            {fn && (
              <div role="tablist" aria-label="How to show it" className="flex gap-2">
                <button role="tab" aria-selected={tab === "flow"} className="tab !py-1.5 text-sm" onClick={() => setTab("flow")}>Flowchart</button>
                <button role="tab" aria-selected={tab === "steps"} className="tab !py-1.5 text-sm" onClick={() => setTab("steps")}>Step by step</button>
              </div>
            )}
            <span className="flex-1" />
            <button className={`btn !py-1.5 !px-3 text-sm ${level === "simpler" ? "btn-gold" : ""}`} aria-pressed={level === "simpler"}
                    onClick={() => setLevel(level === "simpler" ? "normal" : "simpler")}>
              {level === "simpler" ? "↺ Normal explanation" : "🧸 Explain simpler"}
            </button>
          </div>
          <AIStatus status={status} />

          {fn && tab === "flow" ? (
            <>
              {chosen ? <Card b={chosen} text={textOf(chosen)} active onOpen={null} />
                      : <p className="muted">Click a box to see its code and what it does.</p>}
              <Flowchart code={fn.mermaid} selected={selected} onSelect={setSelected}
                         title={`Flowchart of ${fn.name}: ${blocks.length} boxes`} />
              {fn.folded && <p className="muted text-sm">This function is long, so its deepest parts are folded into single boxes.</p>}
              <Legend />
            </>
          ) : (
            <ol className="list-none p-0 m-0 mt-3 space-y-2 max-h-[64vh] overflow-auto pr-1">
              {blocks.map((b) => (
                <li key={b.id}>
                  <Card b={b} text={textOf(b)} active={b.id === selected} onClick={() => setSelected(b.id)}
                        onOpen={b.function_id ? () => { nav(`/p/${id}/code?file=${encodeURIComponent(path)}&fn=${encodeURIComponent(b.function_id!)}`); setTab("flow"); } : null} />
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}

function Card({ b, text, active, onClick, onOpen }: {
  b: CodeBlock; text: string; active: boolean; onClick?: () => void; onOpen: (() => void) | null;
}) {
  const lines = b.lines[0] === b.lines[1] ? `line ${b.lines[0]}` : `lines ${b.lines[0]}-${b.lines[1]}`;
  return (
    <div className={`tile-flat p-3 mt-3 ${onClick ? "cursor-pointer" : ""}`} onClick={onClick}
         onKeyDown={(e) => { if (onClick && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onClick(); } }}
         tabIndex={onClick ? 0 : undefined} role={onClick ? "button" : undefined} aria-pressed={onClick ? active : undefined}
         style={active ? { outline: "3px solid var(--danger)", outlineOffset: "-1px", background: "color-mix(in srgb, var(--gold) 14%, var(--surface))" } : undefined}>
      <div className="flex items-center gap-2 text-sm">
        <span className="inline-block w-3 h-3 border-2 border-[var(--edge)]" style={{ background: KIND_COLOR[b.kind] ?? "var(--surface)" }} />
        <b>{KIND_LABEL[b.kind] ?? b.kind}</b>
        <span className="muted">{lines}</span>
      </div>
      <p className="m-0 mt-1 text-[15.5px]"><RichText text={text} /></p>
      {onOpen && <button className="btn !py-1 !px-2 text-sm mt-2" onClick={(e) => { e.stopPropagation(); onOpen(); }}>See its steps and flowchart →</button>}
    </div>
  );
}

function AIStatus({ status }: { status?: ExplainStatus }) {
  if (!status) return null;
  if (status.status === "done")
    return Object.keys(status.blocks).length
      ? <p className="muted text-xs mt-2 mb-0">✦ AI explanations - check them against the code.</p>
      : <p className="muted text-xs mt-2 mb-0">Explanations written from the code's patterns.</p>;
  if (status.status === "thinking")
    return (
      <p className="notice text-sm mt-3 mb-0 flex items-center gap-2" role="status">
        <span className="hop inline-block"><CubeBuddy size={20} /></span>
        RepoSage is thinking, please wait<span className="dots" /> Meanwhile, these explanations come from the code's patterns.
      </p>
    );
  return <p className="muted text-xs mt-2 mb-0" role="status">{status.message} Explanations below come from the code's patterns.</p>;
}

function Legend() {
  const items = [["var(--grass)", "start / end"], ["var(--gold)", "decision or menu choice"], ["var(--sky)", "loop"], ["var(--surface)", "step"]];
  return (
    <p className="muted text-sm mt-3 mb-0 flex flex-wrap gap-x-4 gap-y-1">
      {items.map(([c, l]) => (
        <span key={l} className="inline-flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 border-2 border-[var(--edge)]" style={{ background: c }} />{l}
        </span>
      ))}
    </p>
  );
}
