import { useState } from "react";
import { getApiKey, setApiKey } from "./api";
import { Chat } from "./pages/Chat";
import { Documents } from "./pages/Documents";
import { Reports } from "./pages/Reports";
import { Sources } from "./pages/Sources";

type Tab = "chat" | "sources" | "documents" | "reports";

function KeyGate({ onSave }: { onSave: (k: string) => void }) {
  const [key, setKey] = useState("");
  return (
    <div className="page narrow">
      <h1>Data Analyst Agent</h1>
      <p className="muted">Enter your workspace API key. Create one with <code>uv run analyst create-workspace &lt;name&gt;</code>.</p>
      <form className="card form" onSubmit={(e) => { e.preventDefault(); onSave(key.trim()); }}>
        <label>API key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="dak_…" autoFocus /></label>
        <button className="primary" disabled={!key.trim()}>Continue</button>
      </form>
    </div>
  );
}

export default function App() {
  const [key, setKey] = useState(getApiKey());
  const [tab, setTab] = useState<Tab>("chat");
  const [reportId, setReportId] = useState<string | null>(null);

  if (!key) return <KeyGate onSave={(k) => { setApiKey(k); setKey(k); }} />;

  return (
    <div className="app">
      <header>
        <strong>Data Analyst Agent</strong>
        <nav>
          {(["chat", "sources", "documents", "reports"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
          ))}
        </nav>
        <button className="link" onClick={() => { setApiKey(""); setKey(""); }}>Sign out</button>
      </header>
      <main>
        {tab === "chat" && <Chat onOpenReport={(id) => { setReportId(id); setTab("reports"); }} />}
        {tab === "sources" && <Sources />}
        {tab === "documents" && <Documents />}
        {tab === "reports" && <Reports openId={reportId} />}
      </main>
    </div>
  );
}
