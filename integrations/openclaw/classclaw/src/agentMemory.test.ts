import { describe, expect, it, vi } from "vitest";
import { memoryPromptHook } from "./agentMemory.js";

describe("class memory prompt context", () => {
  it("projects an effective rule by ID without repeating facts or leaking other class data", async () => {
    const rule = { memory_id: "rule-1", kind: "schedule", name: "早读", aliases: [], start_time: "07:30",
      end_time: "08:00", weekdays: [1], valid_from: null, description: "重复展示", revision: 7 };
    const get = vi.fn(async () => ({ class_id: "class-1", date: "2026-10-26", timezone: "Asia/Shanghai",
      items: [rule], effective: [rule] }));
    const hook = memoryPromptHook({ "bound-agent": "class-1" }, get);
    expect(await hook({}, { agentId: "other-agent" })).toBeUndefined();
    expect(get).not.toHaveBeenCalled();
    const result = await hook({}, { agentId: "bound-agent" });
    const [, json] = result!.prependContext.split("\n");
    expect(JSON.parse(json)).toEqual({
      date: "2026-10-26", timezone: "Asia/Shanghai",
      items: [{ memory_id: "rule-1", kind: "schedule", name: "早读", start_time: "07:30", end_time: "08:00", weekdays: [1] }],
      effective_ids: ["rule-1"],
    });
  });

  it("reports missing memory rather than treating a foreign snapshot as this class's data", async () => {
    const hook = memoryPromptHook({ "bound-agent": "class-1" }, async () => ({
      class_id: "class-2", items: [{ name: "外班事项" }], effective: [],
    }));
    const result = await hook({}, { agentId: "bound-agent" });
    expect(result?.prependContext).toContain("记忆读取失败");
    expect(result?.prependContext).not.toContain("外班事项");
  });
});
