import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type AIStatus, type FileInfo, type Overview, type ProjectStatus } from "../api";
import Loading from "../components/Loading";
import FileMap from "../components/FileMap";
import { CubeBuddy } from "../components/Pixel";

export default function ProjectPage() {
  const { id = "" } = useParams();
  const [status, setStatus] = useState<ProjectStatus | null>(null);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [loadingDone, setLoadingDone] = useState(false);

  // 1. poll the analysis until it is ready
  useEffect(() => {
    let stop = false;
    async function poll() {
      try {
        const s = await api.status(id);
        if (stop) return;
        setStatus(s);
        if (s.status === "working") setTimeout(poll, 600);
      } catch (e) {
        if (!stop) setError((e as Error).message);
      }
    }
    poll();
    return () => { stop = true; };
  }, [id]);

  // 2. load the overview; refresh while the AI notes are still being written
  const loadOverview = useCallback(async () => {
    try {
      setData(await api.overview(id));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);
  useEffect(() => {
    if (status?.status === "ready" && loadingDone) loadOverview();
  }, [status?.status, loadingDone, loadOverview]);
  useEffect(() => {
    if (!data || !["waiting", "thinking"].includes(data.ai_status)) return;
    const t = setTimeout(loadOverview, 2500);
    return () => clearTimeout(t);
  }, [data, loadOverview]);

  if (error) return <Problem message={error} />;
  if (status?.status === "failed") return <Problem message={status.error || "We couldn't read that project."} />;
  if (!loadingDone || !data)
    return <Loading stage={status?.stage ?? "unpacking"} progress={status?.status === "ready" ? 100 : status?.progress ?? 5}
                    onShown={() => setLoadingDone(true)} />;
  return <Dashboard data={data} id={id} onRetry={async () => { await api.retryAI(id); loadOverview(); }} />;
}

function Problem({ message }: { message: string }) {
  return (
    <div className="max-w-2xl mx-auto px-4 py-16">
      <div className="tile p-6">
        <h1 className="pixel text-[15px] mt-0">Oops - a block fell over</h1>
        <p role="alert">{message}</p>
        <Link to="/" className="btn btn-go no-underline">Try another project</Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Dashboard({ data, id, onRetry }: { data: Overview; id: string; onRetry: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(() => new Set(data.start_here.file ? [data.start_here.file] : []));
  const rows = useRef<Record<string, HTMLLIElement | null>>({});

  function select(path: string) {
    setSelected(path);
    setOpen((s) => new Set(s).add(path));
    const el = rows.current[path];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.remove("flash");
      void el.offsetWidth;            // restart the highlight animation
      el.classList.add("flash");
    }
  }

  const s = data.stats;
  const skipped = Object.entries(data.upload?.skipped ?? {});
  return (
    <div className="max-w-6xl mx-auto px-4 py-6 sm:py-8" data-project={id}>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <h1 className="pixel text-[18px] sm:text-[24px] m-0 break-all">{data.project}</h1>
        <div className="flex flex-wrap gap-2">
          {data.languages.map((l) => <span key={l.name} className="chip">{l.name}</span>)}
          <span className="chip">{s.files} files</span>
          <span className="chip">{s.functions} functions</span>
          <span className="chip">{s.lines} lines</span>
        </div>
      </div>

      <AIBanner status={data.ai_status} providers={data.ai.providers} onRetry={onRetry} />

      <div className="grid gap-6 mt-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-6 min-w-0">
          <section className="tile p-5 sm:p-6" aria-labelledby="what">
            <h2 id="what" className="pixel text-[12px] sm:text-[13px] mt-0 mb-3">What is this project?</h2>
            <p className="text-[17px] m-0">{data.overview.text}</p>
            <Source ai={data.overview.source === "ai"} />
          </section>

          <section className="lg:hidden"><StartHere data={data} onSelect={select} /></section>

          <section className="tile p-5 sm:p-6" aria-labelledby="map">
            <h2 id="map" className="pixel text-[12px] sm:text-[13px] mt-0 mb-1">File map</h2>
            <p className="muted mt-0 mb-3 text-[15px]">Each block is a file. Arrows show which file uses code from which. Click a block to see its functions.</p>
            <FileMap data={data} selected={selected} onSelect={select} />
          </section>

          <section className="tile p-5 sm:p-6" aria-labelledby="files">
            <h2 id="files" className="pixel text-[12px] sm:text-[13px] mt-0 mb-3">All files</h2>
            <ul className="list-none p-0 m-0 space-y-3">
              {data.files.map((f) => (
                <FileRow key={f.path} f={f} isStart={f.path === data.start_here.file} open={open.has(f.path)}
                         selected={selected === f.path}
                         refCb={(el) => { rows.current[f.path] = el; }}
                         toggle={() => setOpen((o) => { const n = new Set(o); if (n.has(f.path)) n.delete(f.path); else n.add(f.path); return n; })} />
              ))}
            </ul>
          </section>
        </div>

        <aside className="space-y-6 min-w-0">
          <div className="hidden lg:block"><StartHere data={data} onSelect={select} /></div>
          <section className="tile p-5" aria-labelledby="langs">
            <h2 id="langs" className="pixel text-[12px] mt-0 mb-3">Languages</h2>
            <div className="flex h-5 border-3 border-[var(--edge)]">
              {data.languages.map((l) => (
                <span key={l.name} style={{ width: `${Math.max(l.percent, 2)}%`, background: langColor(l.name) }} title={`${l.name} ${l.percent}%`} />
              ))}
            </div>
            <ul className="list-none p-0 mt-3 mb-0 space-y-1">
              {data.languages.map((l) => (
                <li key={l.name} className="flex items-center gap-2 text-[15px]">
                  <span className="w-3 h-3 border-2 border-[var(--edge)]" style={{ background: langColor(l.name) }} />
                  <b>{l.name}</b><span className="muted">{l.files} file{l.files > 1 ? "s" : ""} · {l.lines} lines · {l.percent}%</span>
                </li>
              ))}
            </ul>
          </section>
          {skipped.length > 0 && (
            <section className="tile p-5" aria-labelledby="skipped">
              <h2 id="skipped" className="pixel text-[12px] mt-0 mb-2">What we left out</h2>
              <p className="muted text-sm mt-0">To keep things safe and simple, these were not read:</p>
              <ul className="text-[15px] m-0 pl-5">
                {skipped.map(([why, n]) => <li key={why}>{n} {why}</li>)}
              </ul>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}

function StartHere({ data, onSelect }: { data: Overview; onSelect: (p: string) => void }) {
  const sh = data.start_here;
  if (!sh.file) return null;
  return (
    <section className="tile p-5 border-[var(--edge)]" aria-labelledby="start" style={{ background: "color-mix(in srgb, var(--gold) 18%, var(--surface))" }}>
      <h2 id="start" className="pixel text-[12px] mt-0 mb-3">🚩 Start here</h2>
      {sh.function && (
        <p className="mt-0 text-[15px]">
          Open <button className="mono font-bold underline cursor-pointer bg-transparent border-0 p-0 text-[var(--link)]" onClick={() => onSelect(sh.file!)}>{sh.file}</button>{" "}
          and find <code className="font-bold">{sh.function.name}()</code> on line {sh.function.line}. That's where the program begins.
        </p>
      )}
      <ol className="list-none p-0 m-0 space-y-2">
        {sh.steps.map((st, i) => (
          <li key={st.file} className="flex gap-3">
            <span className="pixel text-[11px] w-7 h-7 shrink-0 flex items-center justify-center border-3 border-[var(--edge)]"
                  style={{ background: i === 0 ? "var(--gold)" : "var(--surface)", color: "#2b2233" }}>{i + 1}</span>
            <span>
              <button className="mono font-bold underline cursor-pointer bg-transparent border-0 p-0 text-[var(--link)] text-left" onClick={() => onSelect(st.file)}>{st.file}</button>
              <span className="block muted text-sm">{st.reason}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function FileRow({ f, isStart, open, selected, toggle, refCb }: {
  f: FileInfo; isStart: boolean; open: boolean; selected: boolean; toggle: () => void; refCb: (el: HTMLLIElement | null) => void;
}) {
  return (
    <li ref={refCb} className={`tile-flat p-3 sm:p-4 ${selected ? "outline-3 outline-[var(--gold-ink)]" : ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-3 h-3 border-2 border-[var(--edge)] shrink-0" style={{ background: langColor(f.language) }} aria-hidden />
        <span className="mono font-bold break-all">{f.path}</span>
        {isStart && <span className="chip" style={{ background: "var(--gold)", color: "#2b2233" }}>🚩 start here</span>}
        {f.role && !isStart && <span className="chip">{f.role}</span>}
        <span className="muted text-sm ml-auto">{f.lines} lines</span>
      </div>
      <p className="mt-2 mb-0">{f.summary}</p>
      <Source ai={f.summary_source === "ai"} />
      {f.has_errors && <p className="text-sm text-[var(--danger)] mt-1 mb-0">This file has a syntax error; we read what we could.</p>}
      {f.functions.length > 0 && (
        <>
          <button className="btn !py-1 !px-2 text-sm mt-2" aria-expanded={open} onClick={toggle}>
            {open ? "▾ Hide" : "▸ Show"} {f.functions.length} function{f.functions.length > 1 ? "s" : ""}
          </button>
          {open && (
            <ul className="list-none p-0 mt-2 mb-0 space-y-1">
              {f.functions.map((fn) => (
                <li key={fn.id} className="text-[15px] pl-3 border-l-3 border-[var(--grass)]">
                  <code className="font-bold">{fn.parent ? `${fn.parent}.` : ""}{fn.name}{fn.kind === "record" || fn.kind === "class" ? "" : "()"}</code>
                  <span className="muted text-sm"> · line {fn.line}{fn.end > fn.line ? `-${fn.end}` : ""}</span>
                  {fn.summary && <span className="block">{fn.summary}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  );
}

function Source({ ai }: { ai: boolean }) {
  return (
    <span className="block text-xs muted mt-1">{ai ? "✦ AI note - check it against the code" : "From the code's own comments and structure"}</span>
  );
}

function AIBanner({ status, providers, onRetry }: { status: AIStatus; providers: string[]; onRetry: () => void }) {
  if (status === "done") {
    return providers.includes("fake")
      ? <p className="muted text-sm mt-3 mb-0">AI notes: test mode (fake AI).</p> : null;
  }
  const msg: Record<Exclude<AIStatus, "done">, string> = {
    waiting: "RepoSage is thinking, please wait. Plain-English notes will appear here in a moment.",
    thinking: "RepoSage is thinking, please wait. Plain-English notes will appear here in a moment.",
    resting: "The free AI service has reached today's limit. Everything else works - the summaries below come from the code's own comments. Try the AI notes again tomorrow.",
    busy: "The free AI service is busy right now, so the summaries below come from the code's own comments.",
    off: "AI notes are switched off on this server. The summaries below come from the code's own comments.",
  };
  const working = status === "waiting" || status === "thinking";
  return (
    <div className="notice mt-4 flex items-center gap-3" role="status">
      {working && <span className="hop inline-block shrink-0"><CubeBuddy size={28} /></span>}
      <span className="flex-1">{msg[status]}{working && <span className="dots" />}</span>
      {(status === "busy" || status === "resting") && <button className="btn !py-1 !px-3 text-sm" onClick={onRetry}>Try again</button>}
    </div>
  );
}

function langColor(name: string): string {
  const key = name.toLowerCase();
  return ({ python: "var(--c-py)", javascript: "var(--c-js)", typescript: "var(--c-ts)", java: "var(--c-java)", c: "var(--c-c)" } as Record<string, string>)[key] ?? "var(--stone)";
}
