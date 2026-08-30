import { describe, expect, it, vi } from "vitest";

import entry, { createClient, resolveConfig, resolveReminderCronAt, scheduleCommittedReminders } from "./index.js";

describe("classclaw plugin", () => {
  it("uses safe configuration defaults", () => {
    const config = resolveConfig(undefined);
    expect(config.baseUrl).toBe("http://127.0.0.1:8000");
    expect(config.timeoutMs).toBe(30_000);
    expect(config.allowedUploadRoots.length).toBeGreaterThan(0);
  });

  it("runs an already-due reminder promptly instead of creating an invalid past cron", () => {
    expect(resolveReminderCronAt("2026-08-30T09:00:00+08:00", Date.parse("2026-08-30T10:00:00+08:00")))
      .toBe("2026-08-30T02:00:05.000Z");
  });

  it("registers preview and chat-confirmed commit tools", () => {
    const tools: string[] = [];
    const hooks: string[] = [];
    entry.register?.({
      pluginConfig: { baseUrl: "http://127.0.0.1:8000" },
      registerTool(tool: { name: string } | ((context: object) => { name: string })) {
        const resolved = typeof tool === "function" ? tool({ agentId: "class-agent", sessionKey: "session-1" }) : tool;
        tools.push(resolved.name);
      },
      on(name: string) { hooks.push(name); },
    } as never);
    expect(tools).toContain("classclaw_propose_write");
    expect(tools).toContain("classclaw_analyze_interaction");
    expect(tools).toContain("classclaw_commit_write");
    expect(tools).toContain("classclaw_commit_writes");
    expect(tools).toContain("classclaw_mark_reminder_sent");
    expect(tools).not.toContain("classclaw_direct_write");
    expect(tools).not.toContain("classclaw_onboarding_start");
    expect(tools).not.toContain("classclaw_onboarding_update");
    expect(hooks).toEqual(["before_tool_call"]);
  });

  it("schedules committed reminders as proactive agent turns", async () => {
    const scheduler = vi.fn().mockResolvedValue({ id: "cron-1" });
    const outcome = await scheduleCommittedReminders(
      {},
      { agentId: "class-agent", sessionKey: "session-1" },
      {
        result_json: {
          arrangement: { id: "arrangement-1", title: "家长会" },
          reminders: [{ id: "reminder-1", remind_at: "2026-08-31T13:00:00+08:00" }],
        },
      },
      scheduler,
    );
    expect(outcome).toEqual({ scheduled: 1, warnings: [] });
    expect(scheduler).toHaveBeenCalledWith(expect.objectContaining({
      declarationKey: "classclaw-reminder-reminder-1",
      sessionKey: "session-1",
      agentId: "class-agent",
      at: "2026-08-31T13:00:00+08:00",
    }));
  });

  it("sends the configured bearer token", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ success: true, data: { status: "ok" } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = createClient(resolveConfig({ baseUrl: "http://example.test", apiToken: "secret" }));
    await client.get("/health");
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer secret");
    fetchMock.mockRestore();
  });
});
