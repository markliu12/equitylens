import { useEffect, useRef, useState } from "react";

import { getRun, streamRun } from "../api/client";
import type { RunDetail, StreamEvent } from "../api/types";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);

export function useRunDetail(runId: string | null) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [error, setError] = useState<string>("");
  const seqRef = useRef(0);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    let timer: number | undefined;

    const tick = async () => {
      try {
        const data = await getRun(runId);
        if (!alive) return;
        setDetail(data);
        setError("");
        if (!TERMINAL.has(data.run.status)) {
          timer = window.setTimeout(tick, 1100);
        }
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : String(err));
        timer = window.setTimeout(tick, 2200);
      }
    };

    tick();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    seqRef.current = 0;
    const controller = new AbortController();
    let retry: number | undefined;

    const connect = () => {
      streamRun(runId, {
        lastSeq: seqRef.current,
        signal: controller.signal,
        onEvent: (event) => {
          seqRef.current = Math.max(seqRef.current, event.seq);
          setEvents((prev) => [...prev.slice(-240), event]);
        },
      })
        .then(() => {
          // 流正常结束（run 已终态）就不再重连；否则等待并续传
          if (!TERMINAL.has((detail?.run.status as string) ?? "")) {
            retry = window.setTimeout(connect, 1500);
          }
        })
        .catch(() => {
          retry = window.setTimeout(connect, 2500);
        });
    };
    connect();

    return () => {
      controller.abort();
      if (retry) window.clearTimeout(retry);
    };
    // detail.run.status 不作为依赖：否则每次轮询都会重连
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return { detail, events, error, setDetail };
}
