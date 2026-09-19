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

export function toolFailure(error: unknown, operation: "analysis" | "commit" = "analysis") {
  const row = error as { name?: string; code?: string; message?: string; details?: { analysis_id?: string } } | undefined;
  const failure = {
    code: row?.name === "TimeoutError" ? "CLASSCLAW_TOOL_TIMEOUT" : row?.code || "CLASSCLAW_TOOL_FAILED",
    message: row?.message || `ClassClaw ${operation} failed`,
    ...(typeof row?.details?.analysis_id === "string" ? { analysis_id: row.details.analysis_id } : {}),
    retryable: false,
    instruction: operation === "commit"
      ? "本次提交未得到成功确认。说明原因并结束本轮；超时或断连需核对结果，勿假定未写入。不要自行重新分析、替换预览或重试提交。新预览仍须重新复核确认。"
      : "本轮分析未完成。说明原因并结束本轮，等待用户下一条消息；不要重试、轮询或绕过分析写入。",
  };
  return { isError: true, content: [{ type: "text" as const, text: JSON.stringify(failure) }], details: { success: false, error: failure } };
}

const requiredReadFields: Record<string, string[]> = {
  agent_memory: ["class_id"],
  class_summary: ["class_id"], student_search: ["class_id"],
  daily_timetable: ["class_id"], morning_briefing: ["class_id"],
  class_analysis: ["class_id"], attention_students: ["class_id"], write_proposal: ["proposal_id"],
  interaction_analysis: ["analysis_id"], reminder_delivery: ["reminder_id"],
};

export function validateReadParams(params: Row) {
  const missing = (requiredReadFields[String(params.resource)] ?? [])
    .filter((key) => typeof params[key] !== "string" || !(params[key] as string).trim());
  if (missing.length) throw new Error(`TOOL_ARGUMENT_REQUIRED: ${params.resource} requires ${missing.join(", ")}. Use returned IDs; ask for missing information instead of retrying.`);
  if (["student_detail", "student_analysis"].includes(String(params.resource))) {
    const hasId = typeof params.student_id === "string" && (params.student_id as string).trim();
    const hasNo = typeof params.student_no === "string" && (params.student_no as string).trim();
    if (!hasId && !hasNo) {
      throw new Error(`TOOL_ARGUMENT_REQUIRED: ${params.resource} requires student_no (班内学号) or student_id.`);
    }
    const hasClass = typeof params.class_id === "string" && (params.class_id as string).trim();
    if (hasNo && !hasId && !hasClass) {
      // 学号只在班级内唯一：没有班级范围的学号搜索会跨班误匹配。
      throw new Error(`TOOL_ARGUMENT_REQUIRED: ${params.resource} with student_no also requires class_id.`);
    }
  }
  if (["classes", "student_search"].includes(String(params.resource))) {
    for (const field of ["page", "page_size"]) {
      const value = params[field];
      if (value !== undefined && (!Number.isInteger(value) || Number(value) < 1 || (field === "page_size" && Number(value) > 100))) {
        throw new Error(`TOOL_ARGUMENT_INVALID: ${field} must be a positive integer${field === "page_size" ? " up to 100" : ""}`);
      }
    }
  }
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value as Row).sort(([a], [b]) => a.localeCompare(b))
    .filter(([key]) => !["idempotency_key", "requested_by", "confirmed_by", "confirmation_note"].includes(key))
    .map(([key, item]) => [key, canonical(item)]));
}

type Turn = { touched: number; calls: number; seen: Set<string>; writesBlocked: boolean; analysisStarted: boolean; analysisStopped: boolean };
const writes = new Set(["classclaw_propose_write", "classclaw_commit_write", "classclaw_commit_writes"]);

export function createTurnGuard(turns = new Map<string, Turn>()) {
  function before(key: string | undefined, name: string, params: Row): string | undefined {
    if (!key) return; // Older hosts without a run ID must not mix separate user turns.
    const now = Date.now();
    for (const [id, turn] of turns) if (now - turn.touched > 600_000) turns.delete(id);
    if (!turns.has(key)) {
      if (turns.size >= 200) turns.delete(turns.keys().next().value!);
      turns.set(key, { touched: now, calls: 0, seen: new Set(), writesBlocked: false, analysisStarted: false, analysisStopped: false });
    }
    const turn = turns.get(key)!;
    turn.touched = now;
    if (turn.calls++ >= 24) return "本轮工具调用已达安全上限。停止调用，说明已完成的部分和缺失信息，等待用户下一条消息。";
    if ((writes.has(name) || name === "classclaw_analyze_interaction") && turn.writesBlocked) return "本轮分析需要澄清或写入已失败。禁止绕过分析、重建或重复提交；说明原因并结束本轮，等待用户补充或重新复核。";
    if (turn.analysisStopped && name !== "classclaw_cancel_write") return "本轮分析已停止。结束本轮并说明原因；不要重试、轮询或绕过分析，等待用户下一条消息。";
    if (name === "classclaw_analyze_interaction") {
      if (turn.analysisStarted) return "本轮已发起分析。不要修改原文或消息编号重试；使用已有结果并结束本轮。";
      turn.analysisStarted = true;
    }
    const hash = createHash("sha256").update(JSON.stringify(canonical(params))).digest("hex");
    const fingerprint = `${name}:${hash}`;
    if (turn.seen.has(fingerprint)) return "本轮已调用过完全相同的工具参数。使用已有结果；失败时说明原因，不再重复调用。";
    turn.seen.add(fingerprint);
  }
  function after(key: string | undefined, name: string, result: unknown, error?: string) {
    const turn = key ? turns.get(key) : undefined;
    if (!turn) return;
    const response = result as { isError?: boolean; details?: { success?: boolean; data?: Row } } | undefined;
    const details = response?.details;
    const failed = Boolean(error || response?.isError || details?.success === false);
    const data = details?.data;
    if (name === "classclaw_analyze_interaction") {
      const analysis = data?.analysis as Row | undefined;
      if (failed || !analysis || analysis.status !== "awaiting_review") {
        turn.writesBlocked = true;
        turn.analysisStopped = true;
      }
    }
    if ((name === "classclaw_commit_write" || name === "classclaw_commit_writes") && failed) turn.writesBlocked = true;
    // A successful mutation may invalidate earlier reads in the same turn.
    if (!failed && ["classclaw_commit_write", "classclaw_commit_writes", "classclaw_cancel_write"].includes(name)) {
      turn.seen = new Set([...turn.seen].filter((item) => !item.startsWith("classclaw_read:")));
    }
  }
  return { before, after };
}

const turnStateKey = Symbol.for("classclaw.tool-turn-state.v1");

export function sharedTurnGuard() {
  // Nested extractor runs can register a new plugin instance between before
  // and after hooks. Keep only bounded run metadata/hashes in process memory,
  // so either instance sees the same guard without retaining message text.
  const processState = globalThis as typeof globalThis & { [turnStateKey]?: Map<string, Turn> };
  return createTurnGuard(processState[turnStateKey] ??= new Map());
}
