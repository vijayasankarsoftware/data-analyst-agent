export type Task = { id: number; kind: "sql" | "rag"; question: string; source_id: string | null };

export type Citation = { chunk_id: number; document: string; page: number | null; snippet: string };

export type TaskResult = {
  task_id: number;
  kind: "sql" | "rag";
  question: string;
  ok: boolean;
  error?: string | null;
  source?: string | null;
  sql?: string | null;
  columns?: string[];
  rows?: unknown[][];
  row_count?: number;
  truncated?: boolean;
  answer?: string | null;
  citations?: Citation[];
};

export type ChartSpec = { type: "bar" | "line" | "pie"; title: string; task_id: number; x: string; y: string[] };

export type Approval = {
  id: string;
  kind: "sql_approval" | "export_report";
  question?: string;
  source?: string;
  sql?: string;
  reasons?: string[];
  title?: string;
  preview?: string;
};

/** Live view of one task while a run streams in. */
export type Step = {
  taskId: number;
  agent: "sql" | "rag";
  question: string;
  status: "pending" | "started" | "done" | "failed";
  sql?: string;
  attempts?: number;
  error?: string;
  result?: TaskResult;
};

export type Turn = {
  question: string;
  reasoning?: string;
  steps: Record<number, Step>;
  answer: string;
  chart?: ChartSpec | null;
  results: TaskResult[];
  approval?: Approval | null;
  reportId?: string | null;
  status: "running" | "awaiting_approval" | "completed" | "error";
  error?: string;
};

export type SourceRecord = {
  id: string;
  name: string;
  kind: string;
  description: string;
  status: string;
  error?: string | null;
  warnings: string[];
  table_count: number;
};

export type DocumentRecord = {
  id: string;
  filename: string;
  size_bytes: number;
  n_chunks: number;
  flagged_chunks: number;
  created_at: string;
};
