// Small typed wrapper around the RepoSage API.

export type AIStatus = "waiting" | "thinking" | "done" | "resting" | "off" | "busy";

export interface Sample { id: string; title: string; language: string; blurb: string }
export interface AppConfig {
  limits: { upload_mb: number; files: number };
  languages: string[];
  samples: Sample[];
  ai: { enabled: boolean; providers: string[]; resting: boolean };
}

export interface ProjectStatus {
  id: string; name: string; source: string; source_label: string;
  status: "working" | "ready" | "failed"; stage: string; progress: number;
  error: string; ai_status: AIStatus;
  stats: Partial<Stats>;
}

export interface Stats { files: number; functions: number; classes: number; lines: number; calls: number; imports: number }
export interface FunctionInfo { id: string; name: string; kind: string; line: number; end: number; summary: string; summary_source?: "ai"; parent: string | null }
export interface FileInfo {
  path: string; language: string; lines: number; summary: string; summary_source: "ai" | "code";
  role: string; connections: number; has_errors: boolean; functions: FunctionInfo[];
}
export interface Overview {
  project: string;
  stats: Stats;
  languages: { name: string; files: number; lines: number; percent: number }[];
  overview: { text: string; source: "ai" | "code" };
  start_here: { file: string | null; function: { name: string; line: number } | null; steps: { file: string; reason: string }[] };
  files: FileInfo[];
  map: { links: { source: string; target: string; imports: number; calls: number }[] };
  upload: { kept: number; skipped: Record<string, number> };
  ai_status: AIStatus;
  ai: { providers: string[] };
}

export interface CodeBlock {
  id: string; kind: string; label: string; lines: [number, number]; scope: [number, number];
  explain: string; simpler: string; function_id?: string | null;
}
export interface FileView {
  kind: "file"; path: string; language: string; text: string;
  functions: { id: string; name: string; label: string; line: number; end: number }[];
  blocks: CodeBlock[];
}
export interface FunctionView {
  kind: "function"; id: string; name: string; path: string; language: string; start: number; end: number;
  signature: string; mermaid: string; folded: boolean; blocks: CodeBlock[];
}
export type ExplainStatus =
  | { status: "thinking" }
  | { status: "done"; blocks: Record<string, string> }
  | { status: "resting" | "off" | "busy"; message: string };

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch {
    throw new Error("We couldn't reach RepoSage. Check your internet connection and try again.");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.";
    throw new Error(detail);
  }
  return data as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
});

export const api = {
  config: () => call<AppConfig>("/api/config"),
  paste: (code: string, filename: string, language: string) =>
    call<{ id: string }>("/api/projects/paste", json({ code, filename, language })),
  github: (url: string) => call<{ id: string }>("/api/projects/github", json({ url })),
  sample: (sample: string) => call<{ id: string }>("/api/projects/sample", json({ sample })),
  upload: (files: File[]) => {
    const form = new FormData();
    for (const f of files) {
      form.append("files", f);
      // folder uploads: keep each file's path inside the folder
      form.append("paths", (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name);
    }
    return call<{ id: string }>("/api/projects/upload", { method: "POST", body: form });
  },
  status: (id: string) => call<ProjectStatus>(`/api/projects/${id}`),
  overview: (id: string) => call<Overview>(`/api/projects/${id}/overview`),
  file: (id: string, path: string) => call<FileView>(`/api/projects/${id}/code?path=${encodeURIComponent(path)}`),
  func: (id: string, fn: string) => call<FunctionView>(`/api/projects/${id}/function?id=${encodeURIComponent(fn)}`),
  explain: (id: string, level: "normal" | "simpler", target: { path?: string; fn?: string }) =>
    call<ExplainStatus>(`/api/projects/${id}/explain?level=${level}` +
      (target.fn ? `&id=${encodeURIComponent(target.fn)}` : `&path=${encodeURIComponent(target.path ?? "")}`)),
  retryAI: (id: string) => call<{ ok: boolean }>(`/api/projects/${id}/ai/retry`, { method: "POST" }),
};
