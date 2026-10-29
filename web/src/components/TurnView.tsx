import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Step, Turn } from "../types";
import { ApprovalCard, type Decision } from "./ApprovalCard";
import { ChartView } from "./ChartView";
import { ResultTable } from "./ResultTable";

const STATUS_ICON: Record<Step["status"], string> = { pending: "○", started: "◐", done: "●", failed: "✕" };

function StepView({ step }: { step: Step }) {
  const r = step.result;
  return (
    <details className={`step ${step.status}`} open={step.status === "failed"}>
      <summary>
        <span className="icon">{STATUS_ICON[step.status]}</span>
        <span className="badge">{step.agent.toUpperCase()}</span>
        {step.question}
        {step.attempts && step.attempts > 1 ? <span className="muted small"> · {step.attempts} attempts</span> : null}
      </summary>
      {step.sql && <pre className="code">{r?.sql ?? step.sql}</pre>}
      {r?.columns && r.rows && <ResultTable columns={r.columns} rows={r.rows} rowCount={r.row_count} truncated={r.truncated} />}
      {r?.answer && <p>{r.answer}</p>}
      {r?.citations && r.citations.length > 0 && (
        <ul className="citations">
          {r.citations.map((c) => <li key={c.chunk_id}><strong>{c.document}{c.page ? ` p.${c.page}` : ""}</strong>: {c.snippet}…</li>)}
        </ul>
      )}
      {step.error && <p className="error">{step.error}</p>}
    </details>
  );
}

export function TurnView({ turn, onDecide, busy, onOpenReport }: {
  turn: Turn;
  onDecide: (d: Decision) => void;
  busy: boolean;
  onOpenReport: (id: string) => void;
}) {
  const steps = Object.values(turn.steps).sort((a, b) => a.taskId - b.taskId);
  return (
    <article className="turn">
      <div className="question">{turn.question}</div>
      {(turn.reasoning || steps.length > 0) && (
        <section className="timeline">
          {turn.reasoning && <div className="muted small">Plan: {turn.reasoning}</div>}
          {steps.map((s) => <StepView key={s.taskId} step={s} />)}
        </section>
      )}
      {turn.approval && turn.status === "awaiting_approval" && (
        <ApprovalCard key={turn.approval.id} approval={turn.approval} onDecide={onDecide} busy={busy} />
      )}
      {turn.answer && (
        <div className="answer">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.answer}</ReactMarkdown>
        </div>
      )}
      {turn.chart && <ChartView spec={turn.chart} results={turn.results} />}
      {turn.reportId && (
        <button className="link" onClick={() => onOpenReport(turn.reportId!)}>Open saved report</button>
      )}
      {turn.status === "running" && !turn.answer && <div className="muted small pulse">Working…</div>}
      {turn.error && <p className="error">{turn.error}</p>}
    </article>
  );
}
