import { ReportView } from "../components/ReportView";
import { StatusPill } from "../components/StatusPill";
import { useRunDetail } from "../hooks/useRunDetail";

export function ReportPage({
  runId,
  navigate,
}: {
  runId: string;
  navigate: (path: string) => void;
}) {
  const { detail } = useRunDetail(runId);

  if (!detail) {
    return <div className="wrap narrow"><div className="empty">加载中…</div></div>;
  }

  return (
    <div className="wrap">
      <div className="row between" style={{ marginBottom: 14 }}>
        <div className="row">
          <button className="btn ghost" onClick={() => navigate(`/runs/${runId}`)}>
            ← 返回运行详情
          </button>
          <StatusPill status={detail.run.status} />
        </div>
        <span className="small muted mono">{runId}</span>
      </div>
      <ReportView report={detail.report} audit={detail.audit} runId={runId} />
    </div>
  );
}
