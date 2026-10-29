type Props = { columns: string[]; rows: unknown[][]; rowCount?: number; truncated?: boolean; max?: number };

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}

export function ResultTable({ columns, rows, rowCount, truncated, max = 20 }: Props) {
  const shown = rows.slice(0, max);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === "number" ? "num" : ""}>{fmt(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <div className="muted small">
        {rows.length > max ? `Showing ${max} of ${rowCount ?? rows.length} rows` : `${rowCount ?? rows.length} rows`}
        {truncated ? " · result truncated at the row limit" : ""}
      </div>
    </div>
  );
}
