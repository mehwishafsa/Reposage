import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import UploadPage from "./pages/UploadPage";
import ProjectPage from "./pages/ProjectPage";
import CodePage from "./pages/CodePage";
import { SageIcon } from "./components/Pixel";

type Theme = "light" | "dark";

function currentTheme(): Theme {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch { /* private mode: fine */ }
  }, [theme]);

  return (
    <div className="min-h-screen flex flex-col">
      <header className="tile !shadow-none border-x-0 border-t-0 sticky top-0 z-20">
        <div className="max-w-6xl mx-auto flex items-center gap-3 px-4 py-3">
          <Link to="/" className="flex items-center gap-3 no-underline text-[var(--ink)]">
            <SageIcon size={34} />
            <span className="pixel text-[15px] sm:text-[17px]">RepoSage</span>
          </Link>
          <span className="hidden sm:inline muted text-sm">understand any code, one block at a time</span>
          <span className="flex-1" />
          <button className="btn !py-2 !px-3 text-sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
                  aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
            {theme === "dark" ? "☀ Light" : "☾ Dark"}
          </button>
        </div>
      </header>
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/p/:id" element={<ProjectPage />} />
          <Route path="/p/:id/code" element={<CodePage />} />
          <Route path="*" element={<div className="max-w-6xl mx-auto p-6">Page not found. <Link to="/">Go home</Link></div>} />
        </Routes>
      </main>
      <footer className="max-w-6xl w-full mx-auto px-4 py-6 text-sm muted">
        RepoSage only reads your code - it never runs it. Projects are deleted automatically after a while.
      </footer>
    </div>
  );
}
