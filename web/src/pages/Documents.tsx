import { useEffect, useState } from "react";
import { api } from "../api";
import type { DocumentRecord } from "../types";

export function Documents() {
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);

  const load = () => api<{ documents: DocumentRecord[] }>("/documents").then((r) => setDocs(r.documents));
  useEffect(() => { load().catch((e) => setMessage({ text: e.message, error: true })); }, []);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    for (const file of Array.from(files)) {
      const body = new FormData();
      body.append("file", file);
      try {
        const res = await api<{ document: DocumentRecord; warning: string | null }>("/documents", { method: "POST", body });
        setMessage({ text: `${file.name}: ${res.document.n_chunks} chunks indexed.${res.warning ? " ⚠ " + res.warning : ""}`,
          error: Boolean(res.warning) });
      } catch (e: any) {
        setMessage({ text: `${file.name}: ${e.message}`, error: true });
      }
    }
    setBusy(false);
    await load();
  }

  async function remove(id: string) {
    if (!confirm("Delete this document and its search index?")) return;
    await api(`/documents/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="page">
      <h2>Documents</h2>
      <p className="muted">Policies, data dictionaries, notes: PDF, DOCX, Markdown, TXT. The agent cites them in answers
        and uses definitions when writing SQL. Content is treated as data, never as instructions; chunks that look like
        prompt injection are flagged and excluded from search.</p>
      <label className="card upload">
        <input type="file" multiple accept=".pdf,.docx,.md,.markdown,.txt,.csv,.json,.html" disabled={busy}
          onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
        <span>{busy ? "Uploading and indexing…" : "Choose files to upload"}</span>
      </label>
      {message && <p className={message.error ? "error" : "muted"}>{message.text}</p>}
      <table>
        <thead><tr><th>File</th><th>Chunks</th><th>Flagged</th><th>Uploaded</th><th /></tr></thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>{d.filename}</td>
              <td className="num">{d.n_chunks}</td>
              <td className={d.flagged_chunks ? "num error" : "num"}>{d.flagged_chunks}</td>
              <td>{new Date(d.created_at).toLocaleString()}</td>
              <td><button className="danger" onClick={() => remove(d.id)}>Delete</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
