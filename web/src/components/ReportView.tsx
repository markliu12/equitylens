import { useMemo, useState } from "react";

import type { Audit, Claim, Evidence, Report } from "../api/types";
import { TierLabel } from "./StatusPill";

function ClaimLine({
  claim,
  order,
  onCite,
  activeId,
}: {
  claim: Claim;
  order: Record<string, number>;
  onCite: (id: string) => void;
  activeId: string | null;
}) {
  return (
    <li>
      {claim.text}
      {claim.strength === "观点" && <span className="tag">观点</span>}
      {(claim.evidence_ids ?? []).map((id) => {
        const number = order[id];
        if (!number) return null;
        return (
          <button
            type="button"
            key={id}
            className={`cite ${activeId === id ? "active" : ""}`}
            onClick={() => onCite(id)}
            title="查看原文"
          >
            [{number}]
          </button>
        );
      })}
    </li>
  );
}

export function ReportView({
  report,
  audit,
  runId,
}: {
  report: Partial<Report>;
  audit: Partial<Audit>;
  runId: string;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const evidence = useMemo<Evidence[]>(() => (report.evidence ?? []) as Evidence[], [report.evidence]);
  const order = useMemo(() => {
    const map: Record<string, number> = {};
    evidence.forEach((item, index) => {
      map[item.id] = index + 1;
    });
    return map;
  }, [evidence]);

  if (!report.sections?.length) {
    return <div className="empty">报告尚未生成，请等待运行完成。</div>;
  }

  const handleCite = (id: string) => {
    setActiveId(id);
    const element = document.getElementById(`ev-${id}`);
    element?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div className="report-layout">
      <div className="card pad report-body">
        <div className="row between" style={{ marginBottom: 6 }}>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            <span className="pill">
              {report.entity_name}
              {report.entity_code ? `（${report.entity_code}）` : ""}
            </span>
            <span className="pill">基准日 {report.as_of}</span>
            {audit.coverage !== undefined && (
              <span className="pill ok">
                引用覆盖率 {(audit.coverage * 100).toFixed(0)}%
              </span>
            )}
            {audit.quote_hit_rate !== undefined && (
              <span className="pill ok">
                命中率 {(audit.quote_hit_rate * 100).toFixed(0)}%
              </span>
            )}
          </div>
          <div className="row">
            <a className="btn ghost" href={`/api/runs/${runId}/report.md`} download>
              下载 MD
            </a>
            <a className="btn ghost" href={`/api/runs/${runId}/report.html`} target="_blank" rel="noreferrer">
              打印 / PDF
            </a>
          </div>
        </div>

        {report.summary && <div className="summary-box">{report.summary}</div>}

        {(report.sections ?? []).map((section) => (
          <section key={section.title}>
            <h2>{section.title}</h2>
            {section.narrative && <p className="muted small">{section.narrative}</p>}
            <ul className="claim-list">
              {section.claims.map((claim, index) => (
                <ClaimLine
                  key={`${section.title}-${index}`}
                  claim={claim}
                  order={order}
                  onCite={handleCite}
                  activeId={activeId}
                />
              ))}
            </ul>
          </section>
        ))}

        {(report.key_metrics ?? []).length > 0 && (
          <section>
            <h2>关键指标</h2>
            <table className="metrics-table">
              <thead>
                <tr>
                  <th>指标</th>
                  <th>数值</th>
                  <th>期间</th>
                </tr>
              </thead>
              <tbody>
                {(report.key_metrics ?? []).map((metric) => (
                  <tr key={metric.name}>
                    <td>{metric.name}</td>
                    <td className="mono">{metric.value}</td>
                    <td className="muted">{metric.period ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {(report.risks ?? []).length > 0 && (
          <section>
            <h2>风险提示</h2>
            <ul className="claim-list">
              {(report.risks ?? []).map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </section>
        )}

        {(report.open_questions ?? []).length > 0 && (
          <section>
            <h2>待核实问题</h2>
            <ul className="claim-list">
              {(report.open_questions ?? []).map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </section>
        )}

        <div className="disclaimer">{report.disclaimer}</div>
      </div>

      <aside className="card pad evidence-panel">
        <div className="row between" style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>证据链</h2>
          <span className="pill">{evidence.length} 条</span>
        </div>
        <p className="small muted">
          每条结论都挂在本列表的原文上。点击正文中的编号可定位，编号来自稳定的内容哈希。
        </p>
        {evidence.map((item, index) => (
          <div
            id={`ev-${item.id}`}
            key={item.id}
            className={`evidence-item ${activeId === item.id ? "active" : ""}`}
          >
            <div>
              <b>[{index + 1}]</b> <TierLabel tier={item.tier} /> {item.title}
            </div>
            <div className="src">
              {item.source_name}
              {item.published_at ? ` · ${item.published_at}` : ""}
            </div>
            <div className="snippet">{item.snippet}</div>
          </div>
        ))}
      </aside>
    </div>
  );
}
