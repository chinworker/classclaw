import { Readable } from "node:stream";
import type { IncomingMessage, ServerResponse } from "node:http";
import { describe, expect, it, vi } from "vitest";
import { sessionThinkingHandler, thinkingFailure, validateThinkingRequest } from "./sessionThinking.js";

const classId = "97d646a7-153d-4370-aa17-bc49d3ba5593";
const conversation = "21d646a7-153d-4370-aa17-bc49d3ba5593";
const key = `agent:class-agent:openresponses-user:classclaw-web-chat:${classId}:teacher-1:${conversation}`;
const agentClasses = { "class-agent": classId };

describe("private session thinking endpoint", () => {
  it("reports supported levels without returning arbitrary Gateway diagnostics", () => {
    expect(thinkingFailure({ message: 'thinkingLevel "xhigh" is not supported for kimi/kimi-for-coding (use off|low|medium|high)' }))
      .toEqual({ code: "CHAT_THINKING_UNSUPPORTED", message: "The selected thinking level is not supported by this model",
        model: "kimi/kimi-for-coding", supported_levels: ["off", "low", "medium", "high"] });
    expect(JSON.stringify(thinkingFailure({ message: "private path or token" }))).not.toContain("private path");
  });

  it("preserves the enabled option when Kimi returns display labels off|on", () => {
    expect(thinkingFailure({ message: 'thinkingLevel "minimal" is not supported for kimi/kimi-for-coding (use off|on)' }))
      .toMatchObject({ code: "CHAT_THINKING_UNSUPPORTED", supported_levels: ["off", "low"], supported_level_labels: { low: "on" } });
    expect(validateThinkingRequest({ key, thinkingLevel: "low" }, agentClasses)).toEqual({ key, thinkingLevel: "low" });
  });
  it("accepts only a bound class's web session and the two allowed fields", () => {
    expect(validateThinkingRequest({ key, thinkingLevel: "high" }, agentClasses)).toEqual({ key, thinkingLevel: "high" });
    expect(validateThinkingRequest({ key, thinkingLevel: "off" }, agentClasses)).toEqual({ key, thinkingLevel: "off" });
  });

  it.each([
    { key: "agent:class-agent:main", thinkingLevel: "high" },
    { key: key.replace(classId, conversation), thinkingLevel: "high" },
    { key: key.replace("class-agent", "other-agent"), thinkingLevel: "high" },
    { key: key.replace("teacher-1", "teacher:injected"), thinkingLevel: "high" },
    { key, thinkingLevel: "unknown" },
    { key, thinkingLevel: "high", method: "sessions.delete" },
    { key, thinkingLevel: "high", model: "other/model" },
  ])("rejects arbitrary sessions, cross-class targets and extra mutation fields", (body) => {
    expect(() => validateThinkingRequest(body, agentClasses)).toThrow();
  });

  it.each(["low", "high"])("dispatches only sessions.patch for %s and never returns private session metadata", async (thinkingLevel) => {
    const dispatch = vi.fn().mockResolvedValue({ ok: true, payload: { path: "private-path", entry: { secret: "secret" } } });
    const handler = sessionThinkingHandler(agentClasses, dispatch);
    const req = Readable.from([JSON.stringify({ key, thinkingLevel })]) as IncomingMessage;
    req.method = "POST";
    let body = "";
    const res = { statusCode: 0, setHeader: vi.fn(), end: (value: string) => { body = value; } };
    await handler(req, res as unknown as ServerResponse);
    expect(dispatch).toHaveBeenCalledWith("sessions.patch", { key, thinkingLevel }, { timeoutMs: 10_000 });
    expect(res.statusCode).toBe(200);
    expect(JSON.parse(body)).toEqual({ ok: true, payload: { thinkingLevel } });
  });

  it.each(["GET", "DELETE"])("rejects %s without dispatch", async (method) => {
    const dispatch = vi.fn();
    const req = Readable.from([]) as IncomingMessage;
    req.method = method;
    const res = { statusCode: 0, setHeader: vi.fn(), end: vi.fn() };
    await sessionThinkingHandler(agentClasses, dispatch)(req, res as unknown as ServerResponse);
    expect(res.statusCode).toBe(405);
    expect(dispatch).not.toHaveBeenCalled();
  });
});
