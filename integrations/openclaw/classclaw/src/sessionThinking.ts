import type { IncomingMessage, ServerResponse } from "node:http";
import { dispatchGatewayMethod } from "openclaw/plugin-sdk/gateway-method-runtime";

const levels = new Set(["off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"]);
const uuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const webKey = new RegExp(`^agent:([a-z0-9_-]{1,100}):openresponses-user:classclaw-web-chat:(${uuid}):([a-z0-9_-]{1,100}):(${uuid})$`);

export function validateThinkingRequest(body: unknown, agentClasses: Record<string, string>) {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Expected an object");
  const row = body as Record<string, unknown>;
  if (Object.keys(row).some((key) => !["key", "thinkingLevel"].includes(key))) throw new Error("Only key and thinkingLevel are allowed");
  const match = typeof row.key === "string" ? webKey.exec(row.key) : null;
  if (!match || agentClasses[match[1]] !== match[2]) throw new Error("Only bound ClassClaw web-chat sessions may be changed");
  if (typeof row.thinkingLevel !== "string" || !levels.has(row.thinkingLevel)) throw new Error("Unsupported thinking level");
  return { key: row.key as string, thinkingLevel: row.thinkingLevel };
}

export function thinkingFailure(error: { code?: string; message?: string } | undefined) {
  const unsupported = /^thinkingLevel "[^"]+" is not supported for ([a-zA-Z0-9._:/-]+) \(use ([^)]+)\)$/.exec(error?.message || "");
  if (unsupported) {
    // Gateway formats display labels here. Binary providers label the low ID
    // as "on"; returning only known IDs would otherwise lose the enabled option.
    const labels = unsupported[2].split("|");
    const supported = [...new Set(labels.map((label) => label === "on" ? "low" : label).filter((level) => levels.has(level)))];
    return {
      code: "CHAT_THINKING_UNSUPPORTED", message: "The selected thinking level is not supported by this model",
      model: unsupported[1], supported_levels: supported,
      ...(labels.includes("on") ? { supported_level_labels: { low: "on" } } : {}),
    };
  }
  return { code: "CHAT_THINKING_REJECTED", message: "Gateway rejected the session thinking update" };
}

export function sessionThinkingHandler(agentClasses: Record<string, string>, dispatch = dispatchGatewayMethod) {
  return async (req: IncomingMessage, res: ServerResponse) => {
    const send = (status: number, body: unknown) => {
      res.statusCode = status;
      res.setHeader("Cache-Control", "no-store");
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify(body));
    };
    if (req.method !== "POST") {
      res.setHeader("Allow", "POST");
      send(405, { ok: false, error: { message: "POST required" } });
      return true;
    }
    let params;
    try {
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const raw of req) {
        const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
        size += chunk.byteLength;
        if (size > 4096) {
          send(413, { ok: false, error: { message: "Request too large" } });
          return true;
        }
        chunks.push(chunk);
      }
      params = validateThinkingRequest(JSON.parse(Buffer.concat(chunks).toString("utf8")), agentClasses);
    } catch {
      send(400, { ok: false, error: { code: "CHAT_THINKING_INVALID_REQUEST", message: "Invalid ClassClaw web-session thinking request" } });
      return true;
    }
    try {
      // Fixed method and fixed two-field projection. No caller-supplied RPC,
      // arbitrary session key, model, deletion or agent configuration mutation.
      const result = await dispatch("sessions.patch", params, { timeoutMs: 10_000 });
      if (!result.ok) {
        send(400, { ok: false, error: thinkingFailure(result.error) });
      } else {
        send(200, { ok: true, payload: { thinkingLevel: params.thinkingLevel } });
      }
    } catch {
      send(503, { ok: false, error: { code: "CHAT_THINKING_UNAVAILABLE", message: "Session thinking service unavailable" } });
    }
    return true;
  };
}
