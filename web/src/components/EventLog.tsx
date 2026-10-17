import { useEffect, useRef } from "react";

import type { StreamEvent } from "../api/types";

export function EventLog({ events }: { events: StreamEvent[] }) {
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [events.length]);

  if (!events.length) {
    return <div className="empty">等待事件…</div>;
  }

  return (
    <div className="log" ref={boxRef}>
      {events.slice(-80).map((event) => (
        <div key={`${event.seq}-${event.type}`}>
          <span className="t">{String(event.seq).padStart(3, "0")}</span>
          <span className="n">{event.type}</span>
          {event.node_id ? ` · ${event.node_id}` : ""}
        </div>
      ))}
    </div>
  );
}
