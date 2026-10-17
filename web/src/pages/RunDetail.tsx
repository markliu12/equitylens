import { useState } from "react";

import { cancelRun } from "../api/client";
import type { NodeStatus } from "../api/types";
import { BudgetMeters } from "../components/BudgetMeters";
import { DebatePanel } from "../components/DebatePanel";
import { EventLog } from "../components/EventLog";
import { NodeTimeline } from "../components/NodeTimeline";
import { StatusPill } from "../components/StatusPill";
import { useRunDetail } from "../hooks/useRunDetail";

export function RunDetailPage({
  runId,
  navigate,
}: {
  runId: string;
  navigate: (path: string) => void;
}) {
  const { detail, events, error } = useRunDetail(runId);
  const [cancelling, setCancelling] = useState(false);

  if (error && !detail) {
    return (
      <div className="wrap narrow">
        <div className="card pad">
          <h2>加载失败</h2>
          <div className="notice">{error}</div>
        </div>
      </div>
    );
  }

  if (!detail) {
    return <div className="wrap narrow"><div className="empty">加载中…</div></div>;
  }

  const durations: Record<string, number> = {};
  for (const item of detail.executions) {
    const seconds = (item.finished_at ?? 0) - (item.started_at ?? 0);
    if (seconds > 0) durations[item.node_id] = seconds;
  }

  const statuses = detail.node_statuses as Record<string, NodeStatus>;
  const total = detail.graph.length;
  const done = Object.values(statuses).filter((s) => s === "SUCCEEDED").length;
  const hasReport = Boolean(detail.report?.sections?.length);

  const onCancel = async () => {
    setCancelling(true);
    try {
      await cancelRun(runId);
    } catch {
      // 已经结束的 run 取消会 409，忽略即可
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="wrap">
      <div className="card pad">
        <div className="row between" style={{ alignItems: "flex-start" }}>
          <div>
            <div className="row" style={{ marginBottom: 8 }}>
              <StatusPill status={detail.run.status} />
              <span className="pill">基准日 {detail.run.as_of}</span>
              <span className="pill mono">{runId}</span>
            </div>
            <h1 style={{ fontSize: 21 }}>{detail.run.question}</h1>
            <div className="small muted">
              {done}/{total} 节点完成 · 事件 {events.length} 条
              {detail.run.error ? ` · ${detail.run.error}` : ""}
            </div>
          </div>
          <div className="row">
            {detail.is_running && (
              <button className="btn" onClick={onCancel} disabled={cancelling}>
                {cancelling ? "取消中…" : "取消运行"}
              </button>
            )}
            <button
              className="btn primary"
              disabled={!hasReport}
              onClick={() => navigate(`/runs/${runId}/report`)}
            >
              查看报告
            </button>
          </div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 18, alignItems: "start" }}>
        <div className="card pad">
          <div className="row between" style={{ marginBottom: 6 }}>
            <h2 style={{ margin: 0 }}>执行时间线</h2>
            <span className="small muted">依赖计数调度 · 分层并发</span>
          </div>
          <NodeTimeline graph={detail.graph} statuses={statuses} durations={durations} />
        </div>

        <div className="stack">
          <div className="card pad">
            <h2>预算</h2>
            <BudgetMeters usage={detail.run.usage ?? {}} />
            <p className="small muted" style={{ marginTop: 12, marginBottom: 0 }}>
              预算在每次模型调用**之前**检查，超限直接中止，而不是事后统计。
            </p>
          </div>

          <div className="card pad">
            <h2>分析师结论</h2>
            {detail.analyst_reports.length === 0 && <div className="empty">尚未产出</div>}
            <div className="stack" style={{ gap: 10 }}>
              {detail.analyst_reports.map((report) => (
                <div key={report.role} className="side">
                  <h3>
                    {report.role}
                    <span className="score">
                      {report.stance} · 置信度 {(report.confidence * 100).toFixed(0)}%
                    </span>
                  </h3>
                  <ul>
                    {report.claims.slice(0, 3).map((claim, index) => (
                      <li key={index}>{claim.text.slice(0, 72)}</li>
                    ))}
                  </ul>
                  {report.gaps.length > 0 && (
                    <div className="small" style={{ color: "var(--warn)", marginTop: 6 }}>
                      缺口：{report.gaps.join("；")}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="card pad" style={{ marginTop: 18 }}>
        <div className="row between">
          <h2 style={{ margin: 0 }}>多空辩论</h2>
          <span className="small muted">最多 3 轮 · 三重终止条件</span>
        </div>
        <div style={{ marginTop: 12 }}>
          <DebatePanel debates={detail.debate} argumentsData={detail.arguments} />
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 18, alignItems: "start" }}>
        <div className="card pad">
          <div className="row between" style={{ marginBottom: 10 }}>
            <h2 style={{ margin: 0 }}>实时事件</h2>
            <span className="small muted">SSE · Last-Event-ID 续传</span>
          </div>
          <EventLog events={events} />
        </div>
        <div className="card pad">
          <h2>质量校验</h2>
          <div className="stack" style={{ gap: 10 }}>
            <div className="row between">
              <span className="muted small">引用覆盖率</span>
              <b>{(Number(detail.audit.coverage ?? 0) * 100).toFixed(1)}%</b>
            </div>
            <div className="row between">
              <span className="muted small">引用命中率</span>
              <b>{(Number(detail.audit.quote_hit_rate ?? 0) * 100).toFixed(1)}%</b>
            </div>
            <div className="row between">
              <span className="muted small">结论条数</span>
              <b>{detail.audit.total_claims ?? 0}</b>
            </div>
            <div className="row between">
              <span className="muted small">合规风控</span>
              <StatusPill status={detail.risk_review.passed === false ? "FAILED" : "SUCCEEDED"} />
            </div>
            {(detail.audit.issues ?? []).length > 0 && (
              <div className="notice">
                发现 {(detail.audit.issues ?? []).length} 条引用问题：
                {(detail.audit.issues ?? [])[0]?.detail}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
