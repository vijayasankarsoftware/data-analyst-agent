import { type FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { SourceRecord } from "../types";

type TableDoc = { qualified_name: string; ddl: string; pii_columns: string[] };

export function Sources() {
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [kinds, setKinds] = useState<string[]>(["postgres", "duckdb"]);
  const [form, setForm] = useState({ name: "", kind: "postgres", dsn: "", files: "", database: "", description: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [tables, setTables] = useState<Record<string, TableDoc[]>>({});

  const load = () => api<{ sources: SourceRecord[] }>("/sources").then((r) => setSources(r.sources));

  useEffect(() => {
    load().catch((e) => setMessage({ text: e.message, error: true }));
    api<{ kinds: string[] }>("/sources/kinds").then((r) => setKinds(r.kinds)).catch(() => {});
  }, []);

  async function connect(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage({ text: "Testing connection and indexing schema…" });
    const credentials = form.kind === "postgres"
      ? { dsn: form.dsn }
      : { files: form.files.split(",").map((f) => f.trim()).filter(Boolean), database: form.database || null };
    try {
      const res = await api<{ source: SourceRecord; warnings: string[] }>("/sources", {
        method: "POST",
        body: JSON.stringify({ name: form.name, kind: form.kind, credentials, description: form.description }),
      });
      setMessage({ text: `Connected ${res.source.name}: ${res.source.table_count} tables indexed.` +
        (res.warnings.length ? ` Warnings: ${res.warnings.join(" ")}` : "") });
      setForm({ ...form, name: "", dsn: "", files: "", database: "", description: "" });
      await load();
    } catch (e: any) {
      setMessage({ text: e.message, error: true });
    } finally {
      setBusy(false);
    }
  }

  async function action(id: string, kind: "refresh" | "delete") {
    if (kind === "delete" && !confirm("Disconnect this source? Its stored credentials and schema snapshot are deleted.")) return;
    try {
      if (kind === "refresh") await api(`/sources/${id}/refresh`, { method: "POST" });
      else await api(`/sources/${id}`, { method: "DELETE" });
      await load();
    } catch (e: any) {
      setMessage({ text: e.message, error: true });
    }
  }

  async function toggleTables(id: string) {
    if (tables[id]) return setTables(({ [id]: _, ...rest }) => rest);
    const res = await api<{ tables: TableDoc[] }>(`/sources/${id}/tables`);
    setTables((t) => ({ ...t, [id]: res.tables }));
  }

  return (
    <div className="page">
      <h2>Data sources</h2>
      <p className="muted">Connect any supported database. Use a <strong>read-only</strong> login; the agent also enforces
        read-only sessions, SQL validation and timeouts on every query. Credentials are encrypted at rest.</p>

      <form className="card form" onSubmit={connect}>
        <div className="grid2">
          <label>Name<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="sales_warehouse" /></label>
          <label>Kind
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {kinds.map((k) => <option key={k}>{k}</option>)}
            </select>
          </label>
        </div>
        {form.kind === "postgres" ? (
          <label>Connection string<input required type="password" value={form.dsn} onChange={(e) => setForm({ ...form, dsn: e.target.value })}
            placeholder="postgresql://readonly_user:password@host:5432/dbname" autoComplete="off" /></label>
        ) : (
          <>
            <label>CSV / Parquet files (comma-separated, relative to the server's data directory)
              <input value={form.files} onChange={(e) => setForm({ ...form, files: e.target.value })} placeholder="sales.csv, customers.parquet" /></label>
            <label>…or a .duckdb file<input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })} placeholder="analytics.duckdb" /></label>
          </>
        )}
        <label>Description (helps the planner pick the right source)
          <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Orders, customers and products since 2024" /></label>
        <button className="primary" disabled={busy}>{busy ? "Connecting…" : "Connect source"}</button>
        {message && <p className={message.error ? "error" : "muted"}>{message.text}</p>}
      </form>

      {sources.map((s) => (
        <div className="card" key={s.id}>
          <div className="row between">
            <div>
              <strong>{s.name}</strong> <span className="badge">{s.kind}</span> <span className={`status ${s.status}`}>{s.status}</span>
              <div className="muted small">{s.description || "No description"} · {s.table_count} tables</div>
            </div>
            <div className="row">
              <button onClick={() => toggleTables(s.id)}>{tables[s.id] ? "Hide schema" : "Schema"}</button>
              <button onClick={() => action(s.id, "refresh")}>Refresh</button>
              <button className="danger" onClick={() => action(s.id, "delete")}>Disconnect</button>
            </div>
          </div>
          {s.warnings?.length > 0 && <ul className="warnings">{s.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
          {s.error && <p className="error">{s.error}</p>}
          {tables[s.id]?.map((t) => <pre className="code" key={t.qualified_name}>{t.ddl}</pre>)}
        </div>
      ))}
    </div>
  );
}
