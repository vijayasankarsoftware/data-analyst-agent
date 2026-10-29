import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamEvents } from "../api";
import type { Decision } from "../components/ApprovalCard";
import { TurnView } from "../components/TurnView";
import type { Approval, SourceRecord, Step, TaskResult, Turn } from "../types";

type ThreadSummary = { id: string; title: string; updated_at: string };

/** Fold one SSE event into the turn being displayed. Pure function: easy to reason about. */
function applyEvent(turn: Turn, event: string, data: any): Turn {
  const steps = { ...turn.steps };
  const step = (id: number): Step => steps[id] ?? { taskId: id, agent: "sql", question: "", status: "pending" };
  switch (event) {
    case "plan":
      for (const t of data.tasks ?? []) steps[t.id] = { taskId: t.id, agent: t.kind, question: t.question, status: "pending" };
      return { ...turn, steps, reasoning: data.revision ? turn.reasoning : data.reasoning };
    case "step":
      if (data.task_id == null) return turn;
      steps[data.task_id] = { ...step(data.task_id), status: data.status, error: data.error ?? undefined };
      return { ...turn, steps };
    case "sql":
      steps[data.task_id] = { ...step(data.task_id), sql: data.sql, attempts: data.attempt };
      return { ...turn, steps };
    case "sql_result":
      steps[data.task_id] = {
        ...step(data.task_id), sql: data.sql,
        result: { task_id: data.task_id, kind: "sql", question: step(data.task_id).question, ok: true, sql: data.sql,
          columns: data.columns, rows: data.rows, row_count: data.row_count, truncated: data.truncated },
      };
      return { ...turn, steps };
    case "rag_result":
      steps[data.task_id] = {
        ...step(data.task_id),
        result: { task_id: data.task_id, kind: "rag", question: step(data.task_id).question, ok: data.found,
          answer: data.answer, citations: data.citations },
      };
      return { ...turn, steps };
    case "token":
      return { ...turn, answer: turn.answer + data.text };
    case "approval_required":
      return { ...turn, approval: data as Approval, status: "awaiting_approval" };
    case "export":
      return data.status === "saved" ? { ...turn, reportId: data.report_id } : turn;
    case "final":
      return { ...turn, answer: data.answer ?? turn.answer, chart: data.chart, results: data.results ?? [],
        reportId: data.report_id ?? turn.reportId };
    case "error":
      return { ...turn, status: "error", error: data.message };
    case "done":
      return { ...turn, status: data.status === "awaiting_approval" ? "awaiting_approval" : data.status === "error" ? "error" : "completed" };
    default:
      return turn;
  }
}

function turnsFromHistory(t: any): Turn[] {
  const turns: Turn[] = [];
  for (const m of t.messages as { role: string; content: string }[]) {
    if (m.role === "user") turns.push({ question: m.content, steps: {}, answer: "", results: [], status: "completed" });
    else if (turns.length) turns[turns.length - 1].answer = m.content;
  }
  const last = turns[turns.length - 1];
  if (last) {
    const results: TaskResult[] = t.results ?? [];
    for (const r of results) {
      last.steps[r.task_id] = { taskId: r.task_id, agent: r.kind, question: r.question, status: r.ok ? "done" : "failed",
        sql: r.sql ?? undefined, error: r.error ?? undefined, result: r };
    }
    last.results = results;
    last.chart = t.final?.chart ?? null;
    last.reportId = t.final?.report_id ?? null;
    if (t.pending_approvals?.length) {
      last.approval = t.pending_approvals[0];
      last.status = "awaiting_approval";
    }
  }
  return turns;
}

export function Chat({ onOpenReport }: { onOpenReport: (id: string) => void }) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const bottom = useRef<HTMLDivElement>(null);

  const loadThreads = useCallback(async () => {
    const res = await api<{ threads: ThreadSummary[] }>("/threads");
    setThreads(res.threads);
  }, []);

  useEffect(() => {
    loadThreads().catch((e) => setError(e.message));
    api<{ sources: SourceRecord[] }>("/sources").then((r) => setSources(r.sources)).catch(() => {});
  }, [loadThreads]);

  // Braces matter: newer browsers return a Promise from scrollIntoView, and React treats
  // anything an effect returns as its cleanup function ("destroy is not a function").
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function openThread(id: string) {
    setThreadId(id);
    setError(null);
    try {
      setTurns(turnsFromHistory(await api(`/threads/${id}`)));
    } catch (e: any) {
      setError(e.message);
    }
  }

  const updateLast = (fn: (t: Turn) => Turn) => setTurns((ts) => (ts.length ? [...ts.slice(0, -1), fn(ts[ts.length - 1])] : ts));

  async function runStream(path: string, body: unknown) {
    setBusy(true);
    setError(null);
    try {
      await streamEvents(path, body, (event, data) => updateLast((t) => applyEvent(t, event, data)));
    } catch (e: any) {
      updateLast((t) => ({ ...t, status: "error", error: e.message }));
    } finally {
      setBusy(false);
      loadThreads().catch(() => {});
    }
  }

  async function ask() {
    const question = input.trim();
    if (!question || busy) return;
    let id = threadId;
    if (!id) {
      const t = await api<{ id: string }>("/threads", { method: "POST", body: JSON.stringify({}) });
      id = t.id;
      setThreadId(id);
      setTurns([]);
    }
    setInput("");
    setTurns((ts) => [...ts, { question, steps: {}, answer: "", results: [], status: "running" }]);
    await runStream(`/threads/${id}/runs`, { message: question, source_ids: sourceFilter ? [sourceFilter] : [] });
  }

  async function decide(decision: Decision) {
    if (!threadId) return;
    updateLast((t) => ({ ...t, status: "running", approval: null }));
    await runStream(`/threads/${threadId}/resume`, { decision });
  }

  const awaiting = turns[turns.length - 1]?.status === "awaiting_approval";

  return (
    <div className="chat">
      <aside className="threads">
        <button className="primary block" onClick={() => { setThreadId(null); setTurns([]); }}>+ New analysis</button>
        {threads.map((t) => (
          <button key={t.id} className={`thread ${t.id === threadId ? "active" : ""}`} onClick={() => openThread(t.id)}>
            {t.title}
          </button>
        ))}
      </aside>
      <section className="conversation">
        {turns.length === 0 && (
          <div className="empty">
            <h2>Ask a question about your data</h2>
            <p className="muted">e.g. "Why did revenue in India drop in Q3 2025, and is it related to the new returns policy?"</p>
          </div>
        )}
        {turns.map((t, i) => (
          <TurnView key={i} turn={t} busy={busy} onDecide={decide} onOpenReport={onOpenReport} />
        ))}
        {error && <p className="error">{error}</p>}
        <div ref={bottom} />
        <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} title="Restrict to one source">
            <option value="">All sources</option>
            {sources.filter((s) => s.status === "ready").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <textarea
            value={input}
            placeholder={awaiting ? "Approve or reject the pending step first…" : "Ask about your data… (Enter to send, Shift+Enter for newline)"}
            disabled={busy || awaiting}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }}
            rows={2}
          />
          <button className="primary" type="submit" disabled={busy || awaiting || !input.trim()}>Ask</button>
        </form>
      </section>
    </div>
  );
}
