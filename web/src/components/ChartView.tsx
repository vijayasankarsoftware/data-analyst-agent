import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { ChartSpec, TaskResult } from "../types";

const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)"];

/** Renders the agent's chart *spec* against real query rows: the LLM never generates chart code. */
export function ChartView({ spec, results }: { spec: ChartSpec; results: TaskResult[] }) {
  const result = results.find((r) => r.task_id === spec.task_id);
  if (!result?.columns || !result.rows?.length) return null;
  const idx = (c: string) => result.columns!.indexOf(c);
  const data = result.rows.map((r) => {
    const row: Record<string, unknown> = { [spec.x]: r[idx(spec.x)] };
    for (const y of spec.y) row[y] = Number(r[idx(y)]);
    return row;
  });

  return (
    <figure className="chart">
      <figcaption>{spec.title}</figcaption>
      <ResponsiveContainer width="100%" height={280}>
        {spec.type === "line" ? (
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey={spec.x} tick={{ fill: "var(--muted)", fontSize: 12 }} />
            <YAxis tick={{ fill: "var(--muted)", fontSize: 12 }} />
            <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)" }} />
            {spec.y.length > 1 && <Legend />}
            {spec.y.map((y, i) => <Line key={y} type="monotone" dataKey={y} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={false} />)}
          </LineChart>
        ) : spec.type === "pie" ? (
          <PieChart>
            <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)" }} />
            <Legend />
            <Pie data={data} dataKey={spec.y[0]} nameKey={spec.x} outerRadius={100}>
              {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
            </Pie>
          </PieChart>
        ) : (
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey={spec.x} tick={{ fill: "var(--muted)", fontSize: 12 }} />
            <YAxis tick={{ fill: "var(--muted)", fontSize: 12 }} />
            <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)" }} />
            {spec.y.length > 1 && <Legend />}
            {spec.y.map((y, i) => <Bar key={y} dataKey={y} fill={COLORS[i % COLORS.length]} radius={[3, 3, 0, 0]} />)}
          </BarChart>
        )}
      </ResponsiveContainer>
    </figure>
  );
}
