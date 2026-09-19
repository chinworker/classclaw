import { EventEmitter } from "node:events";
import type { IncomingMessage, ServerResponse } from "node:http";
import { Readable } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import { chatReasoningHandler } from "./chatReasoning.js";

const classId = "11111111-1111-4111-8111-111111111111";
const key = `agent:class-agent:openresponses-user:classclaw-web-chat:${classId}:teacher-1:22222222-2222-4222-8222-222222222222`;

async function setup(body: unknown = { key }, method = "POST", slow = false) {
  const req = Readable.from([JSON.stringify(body)]) as IncomingMessage;
  req.method = method;
  const chunks: string[] = [];
  const res = Object.assign(new EventEmitter(), {
    statusCode: 0, setHeader: vi.fn(), flushHeaders: vi.fn(),
    write: vi.fn((text: string) => { chunks.push(text); return !slow; }),
    end: vi.fn((text?: string) => { if (text) chunks.push(text); res.emit("close"); }),
  });
  const unsubscribe = vi.fn();
  let emit!: (event: { sessionKey?: string; runId: string; stream: string; data: Record<string, unknown> }) => void;
  const subscribe = vi.fn((listener: typeof emit) => { emit = listener; return unsubscribe; });
  await chatReasoningHandler({ "class-agent": classId }, subscribe)(req, res as unknown as ServerResponse);
  return { res, chunks, subscribe, unsubscribe, emit,
    events: () => chunks.filter((chunk) => chunk.startsWith("data:")).map((chunk) => JSON.parse(chunk.slice(6))) };
}

describe("private reasoning subscription", () => {
  it("streams only this session's thinking text and lifecycle end, never tool arguments or answers", async () => {
    const h = await setup();
    expect(h.res.statusCode).toBe(200);
    const thinking = { sessionKey: key, runId: "run-1", stream: "thinking", data: { text: "first", delta: "first", secret: "hidden" } };
    h.emit({ ...thinking, sessionKey: key.replace("teacher-1", "teacher-2") });
    h.emit({ ...thinking, sessionKey: key.replace(classId, "another-class") });
    h.emit({ ...thinking, sessionKey: undefined });
    h.emit({ ...thinking, stream: "tool", data: { args: "private tool args" } });
    h.emit({ ...thinking, stream: "assistant" });
    h.emit(thinking);
    h.emit({ ...thinking, data: { text: "first second", delta: " second" } });
    h.emit({ ...thinking, data: { text: "new block", delta: "new block" } });
    h.emit({ ...thinking, stream: "lifecycle", data: { phase: "end", private: "metadata" } });
    expect(h.events()).toEqual([
      { type: "ready" }, { type: "thinking", run_id: "run-1", text: "first" },
      { type: "thinking", run_id: "run-1", text: " second" }, { type: "thinking", run_id: "run-1", text: "\n\nnew block" },
      { type: "done", run_id: "run-1" },
    ]);
    h.res.emit("close");
    expect(h.unsubscribe).toHaveBeenCalledTimes(1);
    h.emit(thinking);
    expect(h.events()).toHaveLength(5);
  });

  it.each([
    null, [], { key, method: "sessions.patch" }, { key, reasoningLevel: "on" },
    { key: "agent:class-agent:main" }, { key: key.replace("class-agent", "unbound") },
    { key: key.replace(classId, "33333333-3333-4333-8333-333333333333") },
  ])("rejects invalid or out-of-scope subscriptions before listening: %j", async (body) => {
    const h = await setup(body);
    expect(h.res.statusCode).toBe(400);
    expect(h.subscribe).not.toHaveBeenCalled();
  });

  it("rejects large bodies and non-POST requests", async () => {
    expect((await setup({ key, padding: "x".repeat(4096) })).res.statusCode).toBe(413);
    expect((await setup({ key }, "GET")).res.statusCode).toBe(405);
  });

  it("bounds slow consumers and closes every subscription on timeout", async () => {
    const slow = await setup({ key }, "POST", true);
    expect(slow.unsubscribe).toHaveBeenCalledTimes(1);
    expect(slow.res.end).toHaveBeenCalled();
    vi.useFakeTimers();
    try {
      const h = await setup();
      vi.advanceTimersByTime(180_000);
      expect(h.unsubscribe).toHaveBeenCalledTimes(1);
      expect(h.res.end).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });
});
