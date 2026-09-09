import { describe, expect, it } from "vitest";
import { compactToolData, createTurnGuard, toolResult, validateReadParams } from "./toolRuntime.js";

describe("tool execution contract and bounded context", () => {
  it.each(["cancelled", "failed", "disabled", "blocked"])("does not misclassify a successful read of a %s record", (status) => {
    const result = toolResult({ id: "record-id", status });
    expect(result.details).toEqual({ success: true, data: { id: "record-id", status } });
    expect(result.details).not.toHaveProperty("status");
    expect(JSON.parse(result.content[0].text).status).toBe(status);
  });

  it("preserves complete previews and evidence without repeating raw/normalized payloads", () => {
    const preview = { warnings: ["必须复核"], students: [{ id: "student-id", name: "测试", student_no: "001" }] };
    const proposal = { id: "proposal-id", revision: 2, status: "pending", operation_type: "student.create", preview_json: preview,
      payload_json: { large: "x".repeat(5000) }, normalized_payload_json: { large: "x".repeat(5000) }, expires_at: "2026-09-10" };
    const original = { analysis: { status: "awaiting_review", structured_json: { rejected_reasons: ["缺少日期"],
      operations: [{ operation_type: "student.create", confidence: 0.99, payload: proposal.payload_json }] } }, proposals: [proposal] };
    const output = toolResult(original);
    expect(output.content[0].text.length).toBeLessThan(JSON.stringify(original).length / 5);
    const data = output.details.data as typeof original;
    expect(data.proposals[0].preview_json).toEqual(preview);
    expect(data.proposals[0]).toMatchObject({ id: "proposal-id", revision: 2, expires_at: "2026-09-10" });
    expect(data.analysis.structured_json.rejected_reasons).toEqual(["缺少日期"]);
    expect(original.proposals[0].payload_json.large.length).toBe(5000);
    expect(compactToolData({ payload_json: { arbitrary: "not a proposal" } })).toEqual({ payload_json: { arbitrary: "not a proposal" } });
  });

  it.each(["write_proposal", "student_detail", "interaction_analysis", "reminder_delivery"])("rejects missing IDs for %s before HTTP", (resource) => {
    expect(() => validateReadParams({ resource })).toThrow("TOOL_ARGUMENT_REQUIRED");
    expect(() => validateReadParams({ resource: "write_proposal", proposal_id: "  " })).toThrow("TOOL_ARGUMENT_REQUIRED");
    expect(() => validateReadParams({ resource: "write_proposal", proposal_id: "real-id" })).not.toThrow();
  });

  it("does not repeat requests by changing object order or idempotency keys; runs remain independent", () => {
    const guard = createTurnGuard();
    expect(guard.before("owner/session/run-a", "classclaw_read", { resource: "student_search", class_id: "class-a" })).toBeUndefined();
    expect(guard.before("owner/session/run-a", "classclaw_read", { class_id: "class-a", resource: "student_search" })).toContain("相同");
    expect(guard.before("owner/session/run-b", "classclaw_read", { resource: "student_search", class_id: "class-a" })).toBeUndefined();
    expect(guard.before("run-write", "classclaw_propose_write", { payload: { class_id: "class-a" }, idempotency_key: "a" })).toBeUndefined();
    expect(guard.before("run-write", "classclaw_propose_write", { payload: { class_id: "class-a" }, idempotency_key: "b" })).toContain("相同");
  });

  it.each(["needs_clarification", "no_action", "failed"])("does not bypass %s analysis with a direct write", (status) => {
    const guard = createTurnGuard();
    guard.before("run", "classclaw_analyze_interaction", { text: "原始消息" });
    guard.after("run", "classclaw_analyze_interaction", toolResult({ analysis: { status }, proposals: [] }));
    expect(guard.before("run", "classclaw_propose_write", { payload: { class_id: "class-a" } })).toContain("禁止绕过");
    expect(guard.before("next-run", "classclaw_propose_write", { payload: { class_id: "class-a" } })).toBeUndefined();
  });

  it("stops conflicting writes, allows cancellation, and permits fresh reads after a successful write", () => {
    const guard = createTurnGuard();
    guard.before("run", "classclaw_read", { resource: "class_summary", class_id: "a" });
    guard.after("run", "classclaw_commit_write", undefined, "STUDENT_NO_CONFLICT");
    expect(guard.before("run", "classclaw_propose_write", { payload: { new: "replacement" } })).toContain("写入已失败");
    expect(guard.before("run", "classclaw_cancel_write", { proposal_id: "p" })).toBeUndefined();
    guard.after("run", "classclaw_cancel_write", toolResult({ status: "cancelled" }));
    expect(guard.before("run", "classclaw_read", { resource: "class_summary", class_id: "a" })).toBeUndefined();
  });

  it("caps distinct calls and does not share state when a host has no run IDs", () => {
    const guard = createTurnGuard();
    for (let i = 0; i < 24; i++) expect(guard.before("run", "classclaw_read", { q: String(i) })).toBeUndefined();
    expect(guard.before("run", "classclaw_read", { q: "extra" })).toContain("上限");
    expect(guard.before(undefined, "classclaw_read", {})).toBeUndefined();
    expect(guard.before(undefined, "classclaw_read", {})).toBeUndefined();
  });
});
