import type { RunDetail, RunRecord, StreamEvent } from "./types";

const BASE = "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${detail.slice(0, 200)}`);
  }
  return (await response.json()) as T;
}

export function createRun(question: string, asOf: string): Promise<{ run_id: string }> {
  return request("/api/runs", {
    method: "POST",
    body: JSON.stringify({ question, as_of: asOf }),
  });
}

export function cancelRun(runId: string): Promise<{ ok: boolean }> {
  return request(`/api/runs/${runId}/cancel`, { method: "POST" });
}

export function getRun(runId: string): Promise<RunDetail> {
  return request(`/api/runs/${runId}`);
}

export function listRuns(): Promise<{ runs: RunRecord[] }> {
  return request("/api/runs?limit=12");
}

export function getExamples(): Promise<{ examples: { question: string; as_of: string; tag: string }[] }> {
  return request("/api/meta/examples");
}

export function getGraph(): Promise<{ nodes: { id: string; kind: string; label: string }[] }> {
  return request("/api/meta/graph");
}

export function getModels(): Promise<{
  models: string[];
  offline: boolean;
  demo_live_enabled: boolean;
  budget: { max_cost_cny: number; max_tokens: number; max_llm_calls: number };
}> {
  return request("/api/meta/models");
}

/**
 * 手写 SSE 解析，而不是用 EventSource：
 * 1) EventSource 不能带自定义请求头，无法发 Last-Event-ID；
 * 2) 具名事件需要为每种 event 单独注册监听，新增事件类型就容易漏。
 * 自己读流还能在刷新后精确续传，不丢任何一条事件。
 */
/**
 * 解析 SSE 缓冲区：按空行切帧，拼接多行 data:，跳过心跳与半包。
 * 返回已解析的事件与未完成的残余 buffer（留给下一次读取继续拼）。
 * 独立导出是为了可单测——SSE 续传正确性是整个前端的命脉。
 */
export function parseSseBuffer(buffer: string): { events: StreamEvent[]; rest: string } {
  const chunks = buffer.split("\n\n");
  const rest = chunks.pop() ?? "";
  const events: StreamEvent[] = [];
  for (const chunk of chunks) {
    const dataLines = chunk
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim());
    if (dataLines.length === 0) continue;
    try {
      const payload = JSON.parse(dataLines.join("\n")) as StreamEvent;
      if (payload.type) events.push(payload);
    } catch {
      // 忽略心跳与半包
    }
  }
  return { events, rest };
}

export async function streamRun(
  runId: string,
  options: { lastSeq?: number; signal?: AbortSignal; onEvent: (event: StreamEvent) => void },
): Promise<void> {
  const response = await fetch(`${BASE}/api/runs/${runId}/events`, {
    headers: { "Last-Event-ID": String(options.lastSeq ?? 0) },
    signal: options.signal,
  });
  if (!response.ok || !response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseBuffer(buffer);
    buffer = rest;
    for (const event of events) options.onEvent(event);
  }
}
