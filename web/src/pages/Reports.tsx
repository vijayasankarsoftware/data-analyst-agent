import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api";

type Report = { id: string; title: string; created_at: string };

export function Reports({ openId }: { openId: string | null }) {
  const [reports, setReports] = useState<Report[]>([]);
  const [selected, setSelected] = useState<string | null>(openId);
  const [content, setContent] = useState("");

  useEffect(() => { api<{ reports: Report[] }>("/reports").then((r) => setReports(r.reports)).catch(() => {}); }, []);
  useEffect(() => { if (openId) setSelected(openId); }, [openId]);
  useEffect(() => {
    if (selected) api<string>(`/reports/${selected}`).then(setContent).catch((e) => setContent(`Error: ${e.message}`));
  }, [selected]);

  function download() {
    const blob = new Blob([content], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `report-${selected?.slice(0, 8)}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="page reports">
      <h2>Reports</h2>
      <div className="split">
        <ul className="list">
          {reports.length === 0 && <li className="muted">No reports yet. Ask the agent to "create a report on …".</li>}
          {reports.map((r) => (
            <li key={r.id}>
              <button className={`thread ${r.id === selected ? "active" : ""}`} onClick={() => setSelected(r.id)}>
                {r.title}<div className="muted small">{new Date(r.created_at).toLocaleString()}</div>
              </button>
            </li>
          ))}
        </ul>
        {selected && (
          <div className="card answer">
            <button onClick={download}>Download .md</button>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
