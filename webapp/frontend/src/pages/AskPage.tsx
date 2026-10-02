import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type AgentStep, type ChatJob, type Cite, type FileView, type SimplerStatus } from "../api";
import CodeView from "../components/CodeView";
import Flowchart from "../components/Flowchart";
import RichText from "../components/RichText";
import { CubeBuddy, SageIcon } from "../components/Pixel";

interface Turn { q: string; qid: string; job: ChatJob | null; simpler?: SimplerStatus | null; showSimpler?: boolean; error?: string }

const STEP_ICON: Record<string, string> = {
  search_code: "🔍", read_function: "📖", read_file: "📖", find_callers: "📞", find_callees: "📞",
  get_flowchart: "🧭", project_overview: "🗺️", answer: "✍️", unknown: "❔",
};

function storageKey(id: string) { return `chat:${id}`; }

export default function AskPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const context = { file: params.get("file") ?? undefined, fn: params.get("fn") ?? undefined };
  const [turns, setTurns] = useState<Turn[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(storageKey(id)) ?? "[]"); } catch { return []; }
  });
  const [text, setText] = useState("");
  const [suggest, setSuggest] = useState<string[]>([]);
  const [project, setProject] = useState("");
  const [evidence, setEvidence] = useState<Cite | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const askedFromUrl = useRef(false);

  useEffect(() => {
    api.suggestions(id).then((r) => setSuggest(r.questions)).catch(() => {});
    api.status(id).then((s) => setProject(s.name)).catch(() => {});
  }, [id]);
  useEffect(() => {
    try { sessionStorage.setItem(storageKey(id), JSON.stringify(turns.filter((t) => t.job?.status === "done"))); } catch { /* fine */ }
  }, [id, turns]);

  const update = (qid: string, patch: Partial<Turn>) =>
    setTurns((ts) => ts.map((t) => (t.qid === qid ? { ...t, ...patch } : t)));

  const ask = useCallback(async (question: string) => {
    question = question.trim();
    if (!question) return;
    setText("");
    const history = turns.filter((t) => t.job?.answer).slice(-2).map((t) => ({ q: t.q, a: t.job!.answer! }));
    const temp = `pending-${Date.now()}`;
    setTurns((ts) => [...ts, { q: question, qid: temp, job: null }]);
    try {
      const { id: qid } = await api.ask(id, question, context, history);
      setTurns((ts) => ts.map((t) => (t.qid === temp ? { ...t, qid } : t)));
    } catch (e) {
      update(temp, { error: (e as Error).message });
    }
  }, [id, turns, context.file, context.fn]); // eslint-disable-line react-hooks/exhaustive-deps

  // ask the question from the URL (from the dashboard's ask box)
  useEffect(() => {
    const q = params.get("q");
    if (q && !askedFromUrl.current) {
      askedFromUrl.current = true;
      params.delete("q");
      setParams(params, { replace: true });
      ask(q);
    }
  }, [params, setParams, ask]);

  // poll every question that is still being worked on (live steps)
  useEffect(() => {
    const pending = turns.filter((t) => !t.qid.startsWith("pending") && !t.error && t.job?.status !== "done" && t.job?.status !== "error");
    if (!pending.length) return;
    const timer = setTimeout(async () => {
      for (const t of pending) {
        try {
          const job = await api.chat(id, t.qid);
          update(t.qid, { job });
          if (job.status === "done" && job.focus) setEvidence(job.focus);
        } catch (e) { update(t.qid, { error: (e as Error).message }); }
      }
    }, 650);
    return () => clearTimeout(timer);
  }, [turns, id]);

  // poll "explain simpler"
  useEffect(() => {
    const waiting = turns.filter((t) => t.showSimpler && t.simpler?.status === "thinking");
    if (!waiting.length) return;
    const timer = setTimeout(async () => {
      for (const t of waiting) update(t.qid, { simpler: await api.simpler(id, t.qid).catch(() => null) });
    }, 900);
    return () => clearTimeout(timer);
  }, [turns, id]);

  // follow the newest question while its steps and answer come in
  const last = turns[turns.length - 1];
  const progress = `${turns.length}:${last?.job?.steps.length ?? 0}:${last?.job?.status ?? ""}`;
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [progress]);

  async function toggleSimpler(t: Turn) {
    if (t.showSimpler) { update(t.qid, { showSimpler: false }); return; }
    update(t.qid, { showSimpler: true, simpler: t.simpler ?? { status: "thinking" } });
    if (!t.simpler || t.simpler.status !== "done") update(t.qid, { simpler: await api.simpler(id, t.qid).catch(() => null) });
  }

  const cite = (c: Cite) => {
    setEvidence(c);
    if (window.innerWidth < 1024) document.getElementById("evidence")?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <Link to={`/p/${id}`} className="btn !py-1.5 !px-3 text-sm no-underline">← Overview</Link>
        <h1 className="pixel text-[15px] sm:text-[18px] m-0">Ask RepoSage</h1>
        <span className="muted">{project}</span>
        <span className="flex-1" />
        {turns.length > 0 && <button className="btn !py-1.5 !px-3 text-sm" onClick={() => { setTurns([]); setEvidence(null); }}>New chat</button>}
      </div>

      <div className="grid gap-5 mt-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] items-start">
        <section aria-label="Chat" className="min-w-0">
          {turns.length === 0 && (
            <div className="tile p-5 sm:p-6">
              <div className="flex items-center gap-3">
                <SageIcon size={44} />
                <div>
                  <h2 className="pixel text-[12px] m-0">Ask anything about this code</h2>
                  <p className="muted m-0 mt-1 text-[15px]">
                    RepoSage searches your code, reads the right functions and shows you every step it takes.
                    Answers point to the exact lines.
                  </p>
                </div>
              </div>
              <p className="font-semibold mt-5 mb-2">Try one of these:</p>
              <div className="flex flex-wrap gap-2">
                {suggest.map((q) => <button key={q} className="btn !py-1.5 !px-3 text-sm text-left" onClick={() => ask(q)}>{q}</button>)}
              </div>
            </div>
          )}

          <ol className="list-none p-0 m-0 space-y-6">
            {turns.map((t) => (
              <li key={t.qid}>
                <div className="flex justify-end">
                  <p className="tile-flat m-0 px-4 py-2.5 max-w-[85%] font-semibold" style={{ background: "color-mix(in srgb, var(--grass) 16%, var(--surface))" }}>
                    {t.q}
                  </p>
                </div>
                <Steps steps={t.job?.steps ?? []} working={!t.error && t.job?.status !== "done" && t.job?.status !== "error"} onCite={cite} />
                {t.error && <p role="alert" className="notice mt-3">{t.error}</p>}
                {t.job?.status === "error" && <p role="alert" className="notice mt-3">{t.job.message}</p>}
                {t.job?.status === "done" && (
                  <Answer turn={t} onCite={cite} onAsk={ask} onSimpler={() => toggleSimpler(t)} />
                )}
              </li>
            ))}
          </ol>
          <form className="tile p-3 mt-6 sticky bottom-3 z-10" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
            {context.file && (
              <p className="m-0 mb-2 text-sm flex items-center gap-2">
                <span className="chip">Looking at: {context.fn ? `${context.fn.split("::").pop()}() in ` : ""}{context.file}</span>
                <button type="button" className="underline cursor-pointer bg-transparent border-0 text-[var(--link)] text-sm"
                        onClick={() => setParams({})}>clear</button>
              </p>
            )}
            <div className="flex gap-2">
              <label className="sr-only" htmlFor="question">Your question</label>
              <textarea id="question" className="field !py-2 resize-none" rows={2} value={text} maxLength={500}
                        placeholder={suggest[2] ? `e.g. ${suggest[2]}` : "Ask about this code"}
                        onChange={(e) => setText(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(text); } }} />
              <button className="btn btn-go shrink-0" disabled={!text.trim()}>Ask ▶</button>
            </div>
            <p className="muted text-xs m-0 mt-1.5">RepoSage only reads your code. AI answers can be wrong: check the linked lines.</p>
          </form>
          <div ref={bottom} />
        </section>

        <aside id="evidence" className="lg:sticky lg:top-20 min-w-0">
          <Evidence id={id} cite={evidence} />
        </aside>
      </div>
    </div>
  );
}

function Steps({ steps, working, onCite }: { steps: AgentStep[]; working: boolean; onCite: (c: Cite) => void }) {
  return (
    <ol className="list-none p-0 mt-3 mb-0 ml-1 border-l-3 border-dashed border-[var(--stone)]" aria-label="What RepoSage did" aria-live="polite">
      {steps.map((s, i) => (
        <li key={i} className="relative pl-6 py-1.5 drop-in">
          <span className="absolute -left-[13px] top-2 w-6 h-6 flex items-center justify-center text-[13px] bg-[var(--surface)] border-2 border-[var(--edge)]" aria-hidden>
            {STEP_ICON[s.tool] ?? "•"}
          </span>
          <span className="font-semibold">{s.title}</span>
          {s.detail && <span className="muted"> → {s.detail}</span>}
          {s.refs.length > 0 && s.tool !== "search_code" && (
            <span className="ml-1">{s.refs.slice(0, 1).map((r, j) => (
              <button key={j} className="cite" onClick={() => onCite(r)}>{r.file.split("/").pop()}:{r.start}</button>
            ))}</span>
          )}
          {s.thought && <span className="block text-sm muted italic">“{s.thought}”</span>}
        </li>
      ))}
      {working && (
        <li className="relative pl-6 py-1.5">
          <span className="absolute -left-[13px] top-1.5 hop"><CubeBuddy size={24} /></span>
          <span className="font-semibold">{steps.length ? "Thinking about the next step" : "Starting"}<span className="dots" /></span>
        </li>
      )}
    </ol>
  );
}

function Answer({ turn, onCite, onAsk, onSimpler }: {
  turn: Turn; onCite: (c: Cite) => void; onAsk: (q: string) => void; onSimpler: () => void;
}) {
  const job = turn.job!;
  const s = turn.simpler;
  const simplerText = turn.showSimpler && s && "answer" in s ? s.answer : null;
  return (
    <div className="tile p-4 sm:p-5 mt-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <SageIcon size={22} />
        <b className="pixel text-[11px]">RepoSage</b>
        {job.mode === "ai" && <span className="chip">✦ AI answer</span>}
        {job.mode === "no_ai" && <span className="chip">found without AI</span>}
        {job.cached && <span className="chip" title="Asked before, so this answer costs nothing">⚡ saved answer</span>}
        <span className="flex-1" />
        <button className={`btn !py-1 !px-2.5 text-sm ${turn.showSimpler ? "btn-gold" : ""}`} aria-pressed={!!turn.showSimpler} onClick={onSimpler}>
          {turn.showSimpler ? "↺ Normal answer" : "🧸 Explain simpler"}
        </button>
      </div>
      {job.mode === "no_ai" && job.message && <p className="notice text-sm mt-1">{job.message} Here is the best matching code and its flowchart instead.</p>}
      <p className="text-[16.5px] leading-relaxed m-0 whitespace-pre-line">
        {turn.showSimpler && s?.status === "thinking"
          ? <span className="muted">Making it simpler<span className="dots" /></span>
          : <RichText text={simplerText ?? job.answer ?? ""} onCite={onCite} />}
      </p>
      {(job.followups?.length ?? 0) > 0 && (
        <div className="mt-4">
          <p className="text-sm font-semibold m-0 mb-1.5">Ask next:</p>
          <div className="flex flex-wrap gap-2">
            {job.followups!.map((q) => <button key={q} className="btn !py-1 !px-2.5 text-sm text-left" onClick={() => onAsk(q)}>{q}</button>)}
          </div>
        </div>
      )}
    </div>
  );
}

function Evidence({ id, cite }: { id: string; cite: Cite | null }) {
  const [file, setFile] = useState<FileView | null>(null);
  const [mermaid, setMermaid] = useState<{ fn: string; code: string } | null>(null);
  const [tab, setTab] = useState<"code" | "flow">("code");
  const [box, setBox] = useState<string | null>(null);

  useEffect(() => {
    if (!cite || file?.path === cite.file) return;
    api.file(id, cite.file).then(setFile).catch(() => setFile(null));
  }, [id, cite, file?.path]);

  // the function around the cited lines, for its flowchart
  const fn = cite && file?.path === cite.file
    ? (cite.function_id && file.functions.find((f) => f.id === cite.function_id))
      || file.functions.find((f) => f.line <= cite.start && cite.start <= f.end) : undefined;
  useEffect(() => {
    if (!fn) { setMermaid(null); return; }
    if (mermaid?.fn === fn.id) return;
    api.func(id, fn.id).then((v) => setMermaid({ fn: fn.id, code: v.mermaid })).catch(() => setMermaid(null));
  }, [id, fn?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!cite) {
    return (
      <div className="tile p-5 muted">
        <h2 className="pixel text-[12px] mt-0 text-[var(--ink)]">The evidence</h2>
        The code behind each answer shows up here. Click any <span className="cite">file:line</span> tag to see those lines.
      </div>
    );
  }
  const explainerLink = `/p/${id}/code?file=${encodeURIComponent(cite.file)}${fn ? `&fn=${encodeURIComponent(fn.id)}` : ""}&hl=${cite.start}-${cite.end}`;
  return (
    <div className="tile !p-0 overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b-3 border-[var(--edge)] bg-[var(--surface-2)]">
        <span className="mono font-bold break-all">{cite.file}</span>
        <span className="muted text-sm">lines {cite.start}{cite.end !== cite.start ? `-${cite.end}` : ""}</span>
        <span className="flex-1" />
        {mermaid && (
          <div role="tablist" className="flex gap-1.5">
            <button role="tab" aria-selected={tab === "code"} className="tab !py-1 !px-2 text-sm" onClick={() => setTab("code")}>Code</button>
            <button role="tab" aria-selected={tab === "flow"} className="tab !py-1 !px-2 text-sm" onClick={() => setTab("flow")}>Flowchart</button>
          </div>
        )}
      </div>
      {tab === "flow" && mermaid ? (
        <div className="p-3"><Flowchart code={mermaid.code} selected={box} onSelect={setBox} title={`Flowchart of ${fn?.name}`} /></div>
      ) : file?.path === cite.file ? (
        <CodeView text={file.text} language={file.language} strong={[cite.start, cite.end]}
                  scope={fn ? [fn.line, fn.end] : null}
                  firstLine={fn ? Math.max(1, fn.line - 1) : Math.max(1, cite.start - 8)}
                  lastLine={fn ? fn.end + 1 : cite.end + 12} />
      ) : <p className="p-4 muted">Loading<span className="dots" /></p>}
      <div className="px-4 py-2 border-t-3 border-[var(--edge)] text-sm">
        <Link to={explainerLink}>Open in the code explainer →</Link>
      </div>
    </div>
  );
}
