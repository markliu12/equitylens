const MAP: Record<string, { label: string; cls: string }> = {
  QUEUED: { label: "排队中", cls: "run" },
  RUNNING: { label: "运行中", cls: "run" },
  COMPLETED: { label: "已完成", cls: "ok" },
  PARTIAL: { label: "部分降级", cls: "warn" },
  FAILED: { label: "失败", cls: "err" },
  CANCELLED: { label: "已取消", cls: "" },
  PENDING: { label: "待执行", cls: "" },
  SUCCEEDED: { label: "成功", cls: "ok" },
  SKIPPED: { label: "跳过", cls: "warn" },
};

export function StatusPill({ status }: { status: string }) {
  const item = MAP[status] ?? { label: status, cls: "" };
  const live = status === "RUNNING" || status === "QUEUED";
  return (
    <span className={`pill ${item.cls}`}>
      <span className={`dot ${live ? "live" : ""}`} />
      {item.label}
    </span>
  );
}

export function TierLabel({ tier }: { tier: number }) {
  const labels: Record<number, string> = {
    1: "公司公告",
    2: "财报/行情",
    3: "财经媒体",
    4: "其他来源",
  };
  return <span className="tier">【{labels[tier] ?? "其他"}】</span>;
}
