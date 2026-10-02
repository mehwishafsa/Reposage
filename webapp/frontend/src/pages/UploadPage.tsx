import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type AppConfig } from "../api";
import { CrateIcon } from "../components/Pixel";

type Mode = "paste" | "files" | "github";

const LANGS = [
  { id: "", label: "Guess for me" }, { id: "python", label: "Python" }, { id: "c", label: "C" },
  { id: "java", label: "Java" }, { id: "javascript", label: "JavaScript" }, { id: "typescript", label: "TypeScript" },
];

export default function UploadPage() {
  const nav = useNavigate();
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [mode, setMode] = useState<Mode>("files");
  const [code, setCode] = useState("");
  const [filename, setFilename] = useState("");
  const [language, setLanguage] = useState("");
  const [url, setUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  useEffect(() => { api.config().then(setCfg).catch(() => {}); }, []);

  async function go(start: () => Promise<{ id: string }>) {
    setBusy(true);
    setError("");
    try {
      const { id } = await start();
      nav(`/p/${id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "paste") go(() => api.paste(code, filename, language));
    else if (mode === "github") go(() => api.github(url));
    else go(() => api.upload(files));
  }

  const canSubmit = !busy && (mode === "paste" ? code.trim().length > 0 : mode === "github" ? url.trim().length > 0 : files.length > 0);
  const totalMB = files.reduce((n, f) => n + f.size, 0) / (1024 * 1024);

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 sm:py-12">
      <section className="grid gap-8 lg:grid-cols-[1.05fr_1fr] items-start">
        <div>
          <h1 className="pixel text-[20px] sm:text-[26px] leading-snug m-0">
            Understand any code,<br /><span className="text-[var(--grass)]">one block at a time.</span>
          </h1>
          <p className="text-lg mt-5 max-w-xl">
            Drop in a project - your lab program, a class assignment, or a GitHub repo - and RepoSage
            explains it in plain English: what it does, where it starts, and how the files fit together.
          </p>
          <ul className="mt-5 space-y-2 text-[15px]">
            <li>🤖 <b>Ask questions about your code</b> - agentic AI that shows its steps and points to the exact lines</li>
            <li>🧱 <b>A map of your files</b> and which ones talk to each other</li>
            <li>🚩 <b>Start here:</b> the best order to read the code</li>
            <li>🧭 <b>Flowcharts and plain-English explanations</b> of every function</li>
          </ul>
          <p className="muted text-sm mt-5">
            Works with {cfg ? cfg.languages.join(", ") : "Python, JavaScript, TypeScript, Java and C"}.
            RepoSage only <b>reads</b> code; it never runs it.
          </p>
        </div>

        <form onSubmit={submit} className="tile p-4 sm:p-6" aria-label="Upload your code">
          <div className="flex items-center gap-3 mb-4">
            <CrateIcon size={36} />
            <h2 className="pixel text-[13px] sm:text-[14px] m-0">Bring your code</h2>
          </div>
          <div role="tablist" aria-label="How to add code" className="flex flex-wrap gap-2 mb-4">
            {([["files", "Files or .zip"], ["paste", "Paste code"], ["github", "GitHub link"]] as const).map(([m, label]) => (
              <button key={m} type="button" role="tab" className="tab" aria-selected={mode === m} onClick={() => { setMode(m); setError(""); }}>
                {label}
              </button>
            ))}
          </div>

          {mode === "files" && (
            <div>
              <div
                className={`tile-flat p-5 text-center ${dragging ? "outline-3 outline-dashed outline-[var(--grass)]" : ""}`}
                onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => { e.preventDefault(); setDragging(false); setFiles(Array.from(e.dataTransfer.files)); }}
              >
                <p className="m-0 font-semibold">Drag files or a .zip here</p>
                <p className="muted text-sm mt-1 mb-3">or</p>
                <div className="flex flex-wrap gap-2 justify-center">
                  <button type="button" className="btn" onClick={() => fileInput.current?.click()}>Choose files / .zip</button>
                  <button type="button" className="btn" onClick={() => folderInput.current?.click()}>Choose a folder</button>
                </div>
                <input ref={fileInput} type="file" multiple hidden onChange={(e) => setFiles(Array.from(e.target.files || []))} />
                <input ref={folderInput} type="file" hidden multiple
                       {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
                       onChange={(e) => setFiles(Array.from(e.target.files || []))} />
              </div>
              {files.length > 0 && (
                <p className="text-sm mt-3 mb-0">
                  <b>{files.length === 1 ? files[0].name : `${files.length} files`}</b> selected ({totalMB < 0.1 ? "< 0.1" : totalMB.toFixed(1)} MB)
                </p>
              )}
            </div>
          )}

          {mode === "paste" && (
            <div className="space-y-3">
              <label className="block">
                <span className="sr-only">Your code</span>
                <textarea className="field" rows={10} value={code} onChange={(e) => setCode(e.target.value)}
                          placeholder={"#include <stdio.h>\n\nint main(void) {\n    printf(\"Hello!\\n\");\n    return 0;\n}"} spellCheck={false} />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="text-sm font-semibold">File name (optional)
                  <input className="field mt-1" value={filename} onChange={(e) => setFilename(e.target.value)} placeholder="calculator.c" />
                </label>
                <label className="text-sm font-semibold">Language
                  <select className="field mt-1" value={language} onChange={(e) => setLanguage(e.target.value)}>
                    {LANGS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                  </select>
                </label>
              </div>
            </div>
          )}

          {mode === "github" && (
            <label className="block text-sm font-semibold">Link to a public GitHub repository
              <input className="field mt-1" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/project" inputMode="url" />
              <span className="block muted font-normal mt-2">Only public repositories. We download the code once and read it.</span>
            </label>
          )}

          <p className="notice text-sm mt-4 mb-0" role="note">
            ⚠ Demo uses a free AI service. Don't upload private or company code.
          </p>
          <p className="muted text-xs mt-2 mb-0">
            Up to {cfg?.limits.upload_mb ?? 20} MB and {cfg?.limits.files ?? 500} code files. We skip libraries
            (like node_modules), binary files and secret files such as .env.
          </p>

          {error && <p role="alert" className="mt-3 mb-0 p-3 border-3 border-[var(--danger)] text-[var(--danger)] font-semibold">{error}</p>}

          <button type="submit" className="btn btn-go w-full mt-4 text-[16px]" disabled={!canSubmit}>
            {busy ? "Sending" : "Explain my code ▶"}
          </button>
        </form>
      </section>

      <section className="mt-12">
        <h2 className="pixel text-[13px] sm:text-[15px]">No code handy? Try a sample</h2>
        <div className="grid gap-4 sm:grid-cols-3 mt-4">
          {(cfg?.samples ?? []).map((s) => (
            <button key={s.id} className="tile p-4 text-left cursor-pointer text-[var(--ink)] hover:-translate-y-0.5 transition-transform focusable"
                    disabled={busy} onClick={() => go(() => api.sample(s.id))}>
              <span className="chip">{s.language}</span>
              <span className="block font-bold text-lg mt-2">{s.title}</span>
              <span className="block muted text-sm mt-1">{s.blurb}</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
