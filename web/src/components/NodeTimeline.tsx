import type { GraphNode, NodeStatus } from "../api/types";

const ICON: Record<string, string> = {
  SUCCEEDED: "✓",
  FAILED: "×",
  SKIPPED: "－",
  CANCELLED: "×",
  RUNNING: "●",
};

const KIND_LABEL: Record<string, string> = {
  tool: "工具",
  agent: "Agent",
  judge: "裁决",
  fanout: "并行",
  aggregate: "汇总",
  loop: "循环",
};

export function NodeTimeline({
  graph,
  statuses,
  durations,
}: {
  graph: GraphNode[];
  statuses: Record<string, NodeStatus>;
  durations: Record<string, number>;
}) {
  if (!graph.length) return <div className="empty">暂无节点信息</div>;
  return (
    <div className="timeline">
      {graph.map((node) => {
        const status = statuses[node.id] ?? "PENDING";
        const duration = durations[node.id];
        return (
          <div className={`tl-item ${status}`} key={node.id}>
            <div className="tl-mark">{ICON[status] ?? "○"}</div>
            <div>
              <div className="tl-title">
                {node.label}
                <span className="tl-kind">{KIND_LABEL[node.kind] ?? node.kind}</span>
              </div>
              <div className="tl-sub mono">{node.id}</div>
            </div>
            <div className="tl-sub">
              {duration !== undefined ? `${duration.toFixed(2)}s` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}
