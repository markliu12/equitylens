import type { Argument, RoundScore } from "../api/types";

function Side({
  side,
  data,
  score,
}: {
  side: "bull" | "bear";
  data: Partial<Argument>;
  score?: number;
}) {
  const title = side === "bull" ? "多方" : "空方";
  return (
    <div className={`side ${side}`}>
      <h3>
        {title}
        {score !== undefined && <span className="score">得分 {score.toFixed(2)}</span>}
      </h3>
      <div className="thesis">{data.thesis || "（本轮无可解析论点）"}</div>
      <ul>
        {(data.points ?? []).slice(0, 4).map((point, index) => (
          <li key={index}>
            {point.text}
            {(point.evidence_ids ?? []).length > 0 && (
              <span className="score"> · {point.evidence_ids.length} 条证据</span>
            )}
          </li>
        ))}
      </ul>
      {(data.rebuttals ?? []).length > 0 && (
        <>
          <div className="small muted" style={{ marginTop: 10 }}>
            反驳
          </div>
          <ul>
            {(data.rebuttals ?? []).slice(0, 2).map((item, index) => (
              <li key={index}>{item.text}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function DebatePanel({
  debates,
  argumentsData,
}: {
  debates: RoundScore[];
  argumentsData: { bull: Partial<Argument>; bear: Partial<Argument> };
}) {
  const last = debates[debates.length - 1];
  if (!debates.length) {
    return <div className="empty">本次运行未产生辩论轮次（可能已降级跳过）</div>;
  }
  return (
    <div className="stack">
      <div className="row between small muted">
        <span>
          共 {debates.length} 轮 · 最后领先方：
          {last?.leader === "bull" ? "多方" : last?.leader === "bear" ? "空方" : "持平"}
        </span>
        <span>
          新增信息比：{((last?.new_info_ratio ?? 0) * 100).toFixed(0)}%（低于 20% 提前终止）
        </span>
      </div>
      <div className="debate">
        <Side side="bull" data={argumentsData.bull} score={last?.bull?.total} />
        <Side side="bear" data={argumentsData.bear} score={last?.bear?.total} />
      </div>
      <div className="small muted">
        {debates.map((round) => (
          <span key={round.round} style={{ marginRight: 14 }}>
            第{round.round}轮 · 多 {round.bull?.total?.toFixed(2)} / 空{" "}
            {round.bear?.total?.toFixed(2)}
          </span>
        ))}
      </div>
    </div>
  );
}
