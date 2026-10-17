import { useEffect, useState } from "react";

import { Home } from "./pages/Home";
import { ReportPage } from "./pages/ReportPage";
import { RunDetailPage } from "./pages/RunDetail";

function currentPath(): string {
  const hash = window.location.hash.replace(/^#/, "");
  return hash || "/";
}

export default function App() {
  const [path, setPath] = useState<string>(currentPath());

  useEffect(() => {
    const onChange = () => setPath(currentPath());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = (next: string) => {
    window.location.hash = next;
    window.scrollTo({ top: 0 });
  };

  const runMatch = path.match(/^\/runs\/([\w-]+)$/);
  const reportMatch = path.match(/^\/runs\/([\w-]+)\/report$/);

  return (
    <>
      <header className="topbar">
        <a
          className="brand"
          href="#/"
          onClick={(event) => {
            event.preventDefault();
            navigate("/");
          }}
        >
          EquityLens <small>研镜 · 多智能体研究工作台</small>
        </a>
        <div className="spacer" />
        <nav>
          <a
            href="#/"
            onClick={(event) => {
              event.preventDefault();
              navigate("/");
            }}
          >
            新建研究
          </a>
          <a href="/docs" target="_blank" rel="noreferrer">
            API 文档
          </a>
          <a href="https://github.com/markliu12/equitylens" target="_blank" rel="noreferrer">
            GitHub
          </a>
        </nav>
      </header>

      {runMatch ? (
        <RunDetailPage runId={runMatch[1]} navigate={navigate} />
      ) : reportMatch ? (
        <ReportPage runId={reportMatch[1]} navigate={navigate} />
      ) : (
        <Home navigate={navigate} />
      )}
    </>
  );
}
