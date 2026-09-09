import { Readable } from "node:stream";
import type { IncomingMessage, ServerResponse } from "node:http";
import { describe, expect, it, vi } from "vitest";
import { sessionThinkingHandler, validateThinkingRequest } from "./sessionThinking.js";

const classId = "97d646a7-153d-4370-aa17-bc49d3ba5593";
const conversation = "21d646a7-153d-4370-aa17-bc49d3ba5593";
const key = `agent:class-agent:openresponses-user:classclaw-web-chat:${classId}:teacher-1:${conversation}`;
const agentClasses = { "class-agent": classId };

describe("private session thinking endpoint", () => {
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

  it("dispatches only sessions.patch and never returns private session metadata", async () => {
    const dispatch = vi.fn().mockResolvedValue({ ok: true, payload: { path: "private-path", entry: { secret: "secret" } } });
    const handler = sessionThinkingHandler(agentClasses, dispatch);
    const req = Readable.from([JSON.stringify({ key, thinkingLevel: "high" })]) as IncomingMessage;
    req.method = "POST";
    let body = "";
    const res = { statusCode: 0, setHeader: vi.fn(), end: (value: string) => { body = value; } };
    await handler(req, res as unknown as ServerResponse);
    expect(dispatch).toHaveBeenCalledWith("sessions.patch", { key, thinkingLevel: "high" }, { timeoutMs: 10_000 });
    expect(res.statusCode).toBe(200);
    expect(JSON.parse(body)).toEqual({ ok: true, payload: { thinkingLevel: "high" } });
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
