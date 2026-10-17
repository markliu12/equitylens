import type { Usage } from "../api/types";

function Meter({
  label,
  value,
  max,
  unit,
  format,
}: {
  label: string;
  value: number;
  max: number;
  unit?: string;
  format?: (v: number) => string;
}) {
  const safeValue = Number.isFinite(value) ? value : 0;
  const safeMax = Number.isFinite(max) && max > 0 ? max : 1;
  const ratio = Math.min(1, safeValue / safeMax);
  const cls = ratio >= 0.95 ? "err" : ratio >= 0.8 ? "warn" : "";
  const show = format ?? ((v: number) => v.toFixed(0));
  return (
    <div className="meter">
      <div className="label">
        <span>{label}</span>
        <span className="mono">
          {show(safeValue)}
          {unit ?? ""} / {show(safeMax)}
          {unit ?? ""}
        </span>
      </div>
      <div className="bar">
        <span className={cls} style={{ width: `${Math.max(ratio * 100, 2)}%` }} />
      </div>
    </div>
  );
}

export function BudgetMeters({ usage }: { usage: Usage }) {
  // 后端字段可能演进，前端做一次合并兜底，避免某个 key 缺失就整页崩掉
  const limits = {
    max_cost_cny: 1.2,
    max_tokens: 180000,
    max_llm_calls: 60,
    ...(usage.limits ?? {}),
  };
  return (
    <div className="stack" style={{ gap: 14 }}>
      <Meter
        label="成本"
        value={usage.cost_cny ?? 0}
        max={limits.max_cost_cny}
        unit=" 元"
        format={(v) => v.toFixed(3)}
      />
      <Meter
        label="Token"
        value={usage.total_tokens ?? 0}
        max={limits.max_tokens}
        format={(v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0))}
      />
      <Meter label="LLM 调用" value={usage.calls ?? 0} max={limits.max_llm_calls} unit=" 次" />
    </div>
  );
}
