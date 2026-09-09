import { createHash } from "node:crypto";

type Row = Record<string, unknown>;

// A record's status is NOT the tool's execution status (e.g. cancelled proposals).
// Keep full validated previews, identifiers, revisions and clarification evidence;
// omit duplicate write payloads from the model context, never from the database.
export function compactToolData(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(compactToolData);
  if (!value || typeof value !== "object") return value;
  let row = value as Row;
  if (Array.isArray(row.proposals) && row.proposals.length && row.analysis && typeof row.analysis === "object") {
    const analysis = row.analysis as Row;
    const structured = analysis.structured_json as Row | undefined;
    if (Array.isArray(structured?.operations)) {
      row = { ...row, analysis: { ...analysis, structured_json: { ...structured, operations: structured.operations.map((op) => {
        if (!op || typeof op !== "object") return op;
        return Object.fromEntries(Object.entries(op).filter(([key]) => key !== "payload"));
      }) } } };
    }
  }
  const proposal = typeof row.operation_type === "string" && row.preview_json && row.id && row.revision;
  return Object.fromEntries(Object.entries(row)
    .filter(([key]) => !proposal || !["payload_json", "normalized_payload_json", "source_message_id", "idempotency_key"].includes(key))
    .map(([key, item]) => [key, compactToolData(item)]));
}

export function toolResult(data: unknown) {
  const compact = compactToolData(data);
  return { content: [{ type: "text" as const, text: JSON.stringify(compact) }], details: { success: true, data: compact } };
}

const requiredReadFields: Record<string, string[]> = {
  class_summary: ["class_id"], student_search: ["class_id"], student_detail: ["student_id"],
  daily_timetable: ["class_id"], morning_briefing: ["class_id"], student_analysis: ["student_id"],
  class_analysis: ["class_id"], attention_students: ["class_id"], write_proposal: ["proposal_id"],
  interaction_analysis: ["analysis_id"], reminder_delivery: ["reminder_id"],
};

export function validateReadParams(params: Row) {
  const missing = (requiredReadFields[String(params.resource)] ?? [])
    .filter((key) => typeof params[key] !== "string" || !(params[key] as string).trim());
  if (missing.length) throw new Error(`TOOL_ARGUMENT_REQUIRED: ${params.resource} requires ${missing.join(", ")}. Use returned IDs; ask for missing information instead of retrying.`);
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value as Row).sort(([a], [b]) => a.localeCompare(b))
    .filter(([key]) => !["idempotency_key", "requested_by", "confirmed_by", "confirmation_note"].includes(key))
    .map(([key, item]) => [key, canonical(item)]));
}

type Turn = { touched: number; calls: number; seen: Set<string>; writesBlocked: boolean };
const writes = new Set(["classclaw_propose_write", "classclaw_commit_write", "classclaw_commit_writes"]);

export function createTurnGuard() {
  const turns = new Map<string, Turn>();
  function before(key: string | undefined, name: string, params: Row): string | undefined {
    if (!key) return; // Older hosts without a run ID must not mix separate user turns.
    const now = Date.now();
    for (const [id, turn] of turns) if (now - turn.touched > 600_000) turns.delete(id);
    if (!turns.has(key)) {
      if (turns.size >= 200) turns.delete(turns.keys().next().value!);
      turns.set(key, { touched: now, calls: 0, seen: new Set(), writesBlocked: false });
    }
    const turn = turns.get(key)!;
    turn.touched = now;
    if (turn.calls >= 24) return "本轮工具调用已达安全上限。停止调用，说明已完成的部分和缺失信息，等待用户下一条消息。";
    if (writes.has(name) && turn.writesBlocked) return "本轮分析需要澄清或写入已失败。禁止绕过分析、重建或重复提交；说明原因并等待用户补充或重新复核。";
    const hash = createHash("sha256").update(JSON.stringify(canonical(params))).digest("hex");
    const fingerprint = `${name}:${hash}`;
    if (turn.seen.has(fingerprint)) return "本轮已调用过完全相同的工具参数。使用已有结果；失败时说明原因，不再重复调用。";
    turn.calls += 1;
    turn.seen.add(fingerprint);
  }
  function after(key: string | undefined, name: string, result: unknown, error?: string) {
    const turn = key ? turns.get(key) : undefined;
    if (!turn) return;
    const details = (result as { details?: { data?: Row } } | undefined)?.details;
    const data = details?.data;
    if (name === "classclaw_analyze_interaction") {
      const analysis = data?.analysis as Row | undefined;
      if (error || !analysis || analysis.status !== "awaiting_review") turn.writesBlocked = true;
    }
    if ((name === "classclaw_commit_write" || name === "classclaw_commit_writes") && error) turn.writesBlocked = true;
    // A successful mutation may invalidate earlier reads in the same turn.
    if (!error && ["classclaw_commit_write", "classclaw_commit_writes", "classclaw_cancel_write"].includes(name)) {
      turn.seen = new Set([...turn.seen].filter((item) => !item.startsWith("classclaw_read:")));
    }
  }
  return { before, after };
}
