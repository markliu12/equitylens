import { describe, expect, it } from "vitest";
import { parseSseBuffer } from "./client";

describe("parseSseBuffer", () => {
  it("解析单个完整事件帧", () => {
    const buf = 'id: 1\nevent: RUN_STARTED\ndata: {"type":"RUN_STARTED","seq":1}\n\n';
    const { events, rest } = parseSseBuffer(buf);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("RUN_STARTED");
    expect(rest).toBe("");
  });

  it("一次读取里的多个帧按序解析", () => {
    const buf =
      'data: {"type":"NODE_STARTED","seq":1}\n\n' +
      'data: {"type":"NODE_FINISHED","seq":2}\n\n';
    const { events } = parseSseBuffer(buf);
    expect(events.map((e) => e.seq)).toEqual([1, 2]);
  });

  it("半包留在 rest 里，下一包拼接后不丢事件（续传核心语义）", () => {
    const first = 'data: {"type":"NODE_STARTED","seq":1}\n\ndata: {"type":"NODE_FIN';
    const r1 = parseSseBuffer(first);
    expect(r1.events.map((e) => e.seq)).toEqual([1]);
    expect(r1.rest).toContain("NODE_FIN");

    const r2 = parseSseBuffer(r1.rest + 'ISHED","seq":2}\n\n');
    expect(r2.events.map((e) => e.seq)).toEqual([2]);
    expect(r2.rest).toBe("");
  });

  it("心跳与非法 JSON 被跳过，不抛出", () => {
    const buf = ": ping\n\ndata: not-json\n\n" + 'data: {"type":"HEARTBEAT"}\n\n';
    const { events } = parseSseBuffer(buf);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("HEARTBEAT");
  });

  it("多行 data: 按 SSE 规范拼接", () => {
    const buf = 'data: {"type":"X",\ndata: "seq":3}\n\n';
    const { events } = parseSseBuffer(buf);
    expect(events[0].seq).toBe(3);
  });

  it("无 type 字段的负载不上抛", () => {
    const buf = 'data: {"seq":9}\n\n';
    expect(parseSseBuffer(buf).events).toHaveLength(0);
  });
});
