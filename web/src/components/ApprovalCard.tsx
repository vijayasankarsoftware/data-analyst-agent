import { useState } from "react";
import type { Approval } from "../types";

export type Decision = { action: "approve" | "reject" | "edit"; sql?: string; comment?: string };

/** Human-in-the-loop: the graph is paused (state checkpointed) until one of these buttons is pressed. */
export function ApprovalCard({ approval, onDecide, busy }: {
  approval: Approval;
  onDecide: (d: Decision) => void;
  busy: boolean;
}) {
  const [sql, setSql] = useState(approval.sql ?? "");
  const [comment, setComment] = useState("");
  const edited = approval.sql !== undefined && sql.trim() !== approval.sql.trim();

  if (approval.kind === "export_report") {
    return (
      <div className="approval">
        <strong>Approve report export?</strong>
        <p className="muted">The agent wants to save this answer as a shareable report.</p>
        <div className="row">
          <button className="primary" disabled={busy} onClick={() => onDecide({ action: "approve" })}>Save report</button>
          <button disabled={busy} onClick={() => onDecide({ action: "reject" })}>Don't save</button>
        </div>
      </div>
    );
  }

  return (
    <div className="approval">
      <strong>Approval needed before running this query</strong>
      {approval.source && <div className="muted small">Source: {approval.source}</div>}
      <ul>{(approval.reasons ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
      <label className="small muted" htmlFor={`sql-${approval.id}`}>SQL (you can edit it)</label>
      <textarea id={`sql-${approval.id}`} className="code" rows={Math.min(14, sql.split("\n").length + 2)}
        value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false} />
      <input placeholder="Optional comment (recorded in the audit log)" value={comment}
        onChange={(e) => setComment(e.target.value)} />
      <div className="row">
        {edited ? (
          <button className="primary" disabled={busy} onClick={() => onDecide({ action: "edit", sql, comment })}>Run my edited SQL</button>
        ) : (
          <button className="primary" disabled={busy} onClick={() => onDecide({ action: "approve", comment })}>Approve & run</button>
        )}
        <button className="danger" disabled={busy} onClick={() => onDecide({ action: "reject", comment })}>Reject</button>
      </div>
    </div>
  );
}
