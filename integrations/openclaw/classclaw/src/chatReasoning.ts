import type { IncomingMessage, ServerResponse } from "node:http";
import { validateWebSessionKey } from "./sessionThinking.js";

type AgentEvent = { sessionKey?: string; runId: string; stream: string; data: Record<string, unknown> };
type Subscribe = (listener: (event: AgentEvent) => void) => () => void;

// Read-only, ephemeral subscription. Gateway authentication is applied by the
// route registration; browsers never connect here or receive Gateway credentials.
export function chatReasoningHandler(agentClasses: Record<string, string>, subscribe: Subscribe) {
  return async (req: IncomingMessage, res: ServerResponse) => {
    const reject = (status: number) => {
      res.statusCode = status;
      res.setHeader("Cache-Control", "no-store");
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ ok: false, error: { code: "CHAT_REASONING_UNAVAILABLE" } }));
    };
    if (req.method !== "POST") { res.setHeader("Allow", "POST"); reject(405); return true; }
    let key: string;
    try {
      const chunks: Buffer[] = [];
      let size = 0;
      for await (const raw of req) {
        const chunk = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
        size += chunk.byteLength;
        if (size > 4096) { reject(413); return true; }
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).some((name) => name !== "key")) throw new Error();
      key = validateWebSessionKey(body.key, agentClasses);
    } catch { reject(400); return true; }

    let closed = false;
    let total = 0;
    let priorRun = "";
    let priorText = "";
    let unsubscribe = () => {};
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let lifetime: ReturnType<typeof setTimeout> | undefined;
    const cleanup = () => {
      if (closed) return;
      closed = true;
      unsubscribe();
      clearInterval(heartbeat);
      clearTimeout(lifetime);
      res.off("close", cleanup);
    };
    const send = (event: object) => {
      if (closed) return;
      // Bound slow-reader memory; never let a preview block the agent run.
      if (!res.write(`data: ${JSON.stringify(event)}\n\n`)) { cleanup(); res.end(); }
    };
    try {
      unsubscribe = subscribe((event) => {
        if (closed || event.sessionKey !== key || typeof event.runId !== "string") return;
        if (event.stream === "thinking") {
          const { text, delta } = event.data;
          if (typeof text !== "string" || typeof delta !== "string" || !delta) return;
          total += delta.length;
          if (total > 1_048_576 || text.length > 1_048_576 || delta.length > 262_144) { cleanup(); res.end(); return; }
          // A new assistant reasoning block (e.g. after a tool) resets text.
          const separator = priorRun === event.runId && priorText && (delta === text || !text.startsWith(priorText)) ? "\n\n" : "";
          priorRun = event.runId;
          priorText = text;
          send({ type: "thinking", run_id: event.runId, text: separator + delta });
        } else if (event.stream === "lifecycle" && ["end", "error"].includes(String(event.data.phase))) {
          send({ type: "done", run_id: event.runId });
        }
      });
    } catch { reject(503); return true; }
    res.on("close", cleanup);
    res.statusCode = 200;
    res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders();
    send({ type: "ready" });
    if (!closed) {
      heartbeat = setInterval(() => { if (!res.write(": keepalive\n\n")) { cleanup(); res.end(); } }, 10_000);
      lifetime = setTimeout(() => { cleanup(); res.end(); }, 180_000);
      heartbeat.unref();
      lifetime.unref();
    }
    return true;
  };
}
