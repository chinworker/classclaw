import { afterEach, describe, expect, it, vi } from "vitest";

import entry, { createClient, resolveConfig, resolveReminderCronAt, scheduleCommittedReminders } from "./index.js";
import { toolResult } from "./toolRuntime.js";

type TestTool = { parameters: { properties: Record<string, unknown> }; execute: (id: string, params: object) => Promise<unknown> };
function registeredTools() {
  const tools: Record<string, TestTool> = {};
  entry.register?.({
    pluginConfig: { baseUrl: "http://example.test" }, registerHttpRoute() {}, on() {},
    registerTool(tool: (TestTool & { name: string }) | ((context: object) => TestTool & { name: string })) {
      const resolved = typeof tool === "function" ? tool({ agentId: "class-agent", sessionKey: "session-1" }) : tool;
      tools[resolved.name] = resolved;
    },
  } as never);
  return tools;
}

function registeredHooks() {
  const hooks: Record<string, (event: Record<string, unknown>, context: Record<string, unknown>) => unknown> = {};
  entry.register?.({
    pluginConfig: { baseUrl: "http://example.test", agentClasses: { "agent-a": "class-a", "agent-b": "class-b" } },
    registerHttpRoute() {}, registerTool() {},
    on(name: string, hook: typeof hooks[string]) { hooks[name] = hook; },
  } as never);
  return hooks;
}

afterEach(() => vi.restoreAllMocks());

describe("classclaw plugin", () => {
  it("stamps staged uploads with the bound class before any analysis", async () => {
    const hook = registeredHooks().before_tool_call;
    const result = await hook({ toolName: "classclaw_upload_file", params: { path: "/tmp/file.txt", class_id: "other" } },
      { agentId: "agent-a", sessionKey: "upload", runId: "upload-run" });
    expect(result).toMatchObject({ params: { class_id: "class-a" } });
  });

  it("uses safe configuration defaults", () => {
    const config = resolveConfig(undefined);
    expect(config.baseUrl).toBe("http://127.0.0.1:8000");
    expect(config.timeoutMs).toBe(120_000);
    expect(config.allowedUploadRoots.length).toBeGreaterThan(0);
  });

  it("fetches later roster pages and exposes pagination to the model", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(String(input));
      const page = Number(url.searchParams.get("page") || 1);
      return new Response(JSON.stringify({ success: true, data: { total: 42, page, page_size: 20,
        items: [{ student_no: String((page - 1) * 20 + 1) }] } }));
    });
    const tool = registeredTools().classclaw_read;
    expect(tool.parameters.properties).toHaveProperty("page");
    expect(tool.parameters.properties).toHaveProperty("page_size");
    const first = await tool.execute("1", { resource: "student_search", class_id: "class-a", page: 1, page_size: 20 });
    const second = await tool.execute("2", { resource: "student_search", class_id: "class-a", page: 2, page_size: 20 });
    expect(first).toMatchObject({ details: { data: { items: [{ student_no: "1" }] } } });
    expect(second).toMatchObject({ details: { data: { items: [{ student_no: "21" }] } } });
    expect(String(fetchMock.mock.calls[1][0])).toContain("page=2&page_size=20");
    await expect(tool.execute("3", { resource: "student_search", class_id: "class-a", page: 0 })).rejects.toThrow("TOOL_ARGUMENT_INVALID");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("resolves a class-scoped student number before reading student detail", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(String(input));
      calls.push(`${url.pathname}${url.search}`);
      if (url.pathname === "/api/v1/students") {
        return new Response(JSON.stringify({ success: true, data: { total: 1, page: 1, page_size: 2, items: [{ id: "student-1", student_no: "001" }] } }));
      }
      return new Response(JSON.stringify({ success: true, data: { student: { id: "student-1", student_no: "001" }, homework: [] } }));
    });
    const tool = registeredTools().classclaw_read;
    const result = await tool.execute("1", { resource: "student_detail", class_id: "class-a", student_no: "001" });
    expect(result).toMatchObject({ details: { data: { student: { student_no: "001" } } } });
    expect(calls[0]).toBe("/api/v1/students?class_id=class-a&student_no=001&page_size=2");
    expect(calls[1]).toBe("/api/v1/students/student-1");
  });

  it("rejects an unmatched student number without calling the detail endpoint", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      calls.push(new URL(String(input)).pathname);
      return new Response(JSON.stringify({ success: true, data: { total: 0, page: 1, page_size: 2, items: [] } }));
    });
    const tool = registeredTools().classclaw_read;
    await expect(tool.execute("1", { resource: "student_detail", class_id: "class-a", student_no: "999" })).rejects.toThrow("STUDENT_NOT_FOUND");
    expect(calls).toEqual(["/api/v1/students"]);
  });

  it("checks the nested student scope for student detail without a second fetch", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      calls.push(new URL(String(input)).pathname);
      return new Response(JSON.stringify({ success: true, data: { student: { id: "student-1", class_id: "class-a", student_no: "001" }, homework: [] } }));
    });
    const tool = registeredTools().classclaw_read;
    await expect(tool.execute("1", { resource: "student_detail", student_id: "student-1", bound_class_id: "class-a" }))
      .resolves.toMatchObject({ details: { data: { student: { class_id: "class-a" } } } });
    expect(calls).toEqual(["/api/v1/students/student-1"]);
    await expect(tool.execute("2", { resource: "student_detail", student_id: "student-1", bound_class_id: "class-b" }))
      .rejects.toThrow("CLASS_SCOPE_VIOLATION");
  });

  it("rejects a student number without a class scope before any HTTP call", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}"));
    const tool = registeredTools().classclaw_read;
    await expect(tool.execute("1", { resource: "student_detail", student_no: "001" })).rejects.toThrow("TOOL_ARGUMENT_REQUIRED");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reads the nested analysis scope and rejects foreign analyses", async () => {    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ success: true,
      data: { analysis: { id: "analysis-a", class_id: "class-a", status: "failed" }, proposals: [] },
    })));
    const tool = registeredTools().classclaw_read;
    await expect(tool.execute("1", { resource: "interaction_analysis", analysis_id: "analysis-a", bound_class_id: "class-a" }))
      .resolves.toMatchObject({ details: { data: { analysis: { status: "failed" } } } });
    await expect(tool.execute("2", { resource: "interaction_analysis", analysis_id: "analysis-a", bound_class_id: "class-b" }))
      .rejects.toThrow("CLASS_SCOPE_VIOLATION");
  });

  it("retains the actual analysis ID and ends the turn on an in-progress response", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ success: false,
      error: { code: "INTERACTION_ANALYSIS_IN_PROGRESS", message: "仍在分析", details: { analysis_id: "analysis-a", private: "hidden" } },
    }), { status: 409 }));
    const result = await registeredTools().classclaw_analyze_interaction.execute("1", { channel: "web", text: "补充性别" });
    expect(result).toMatchObject({ isError: true, details: { success: false,
      error: { code: "INTERACTION_ANALYSIS_IN_PROGRESS", analysis_id: "analysis-a", retryable: false },
    } });
    expect(JSON.stringify(result)).not.toContain("hidden");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["needs_clarification", "awaiting_review"])("keeps the turn guard across nested plugin registration: %s", async (status) => {
    const original = registeredHooks();
    const context = { agentId: "agent-a", sessionKey: "chat", runId: `nested-extraction-${status}` };
    const event = { toolName: "classclaw_analyze_interaction", params: { text: "原文" } };
    expect(await original.before_tool_call(event, context)).toEqual({ params: { text: "原文", class_id: "class-a" } });
    // A nested extractor loads another plugin registry while the HTTP call waits.
    const nested = registeredHooks();
    await nested.after_tool_call({ ...event, result: toolResult({ analysis: { status }, proposals: [] }) }, context);
    expect(await nested.before_tool_call({ ...event, params: { text: "改写重试", external_message_id: "new-id" } }, context))
      .toMatchObject({ block: true });
    if (status === "needs_clarification") {
      expect(await original.before_tool_call({ toolName: "classclaw_propose_write", params: { payload: {} } }, context))
        .toMatchObject({ block: true });
    }
    expect(await nested.before_tool_call(event, { ...context, runId: `${context.runId}-next` })).not.toHaveProperty("block");
    expect(await nested.before_tool_call(event, { ...context, agentId: "agent-b" }))
      .toEqual({ params: { text: "原文", class_id: "class-b" } });
  });

  it.each(["classclaw_commit_write", "classclaw_commit_writes"])("ends failed %s without re-analysis or replacement writes", async (name) => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ success: false,
      error: { code: "PENDING_CONFIRMATION_REQUIRED", message: "写入预览已过期，请重新生成" },
    }), { status: 409 }));
    const hooks = registeredHooks();
    const context = { agentId: "agent-a", sessionKey: "chat", runId: `expired-${name}` };
    const params = name === "classclaw_commit_write"
      ? { proposal_id: "expired", revision: 1, confirmed_by: "teacher" }
      : { items: [{ proposal_id: "expired", revision: 1 }], confirmed_by: "teacher" };
    const event = { toolName: name, params };
    expect(await hooks.before_tool_call(event, context)).not.toHaveProperty("block");
    const result = await registeredTools()[name].execute("call-1", params);
    expect(result).toMatchObject({ isError: true, details: { success: false,
      error: { code: "PENDING_CONFIRMATION_REQUIRED", retryable: false, instruction: expect.stringContaining("不要自行重新分析") },
    } });
    const reloaded = registeredHooks();
    await reloaded.after_tool_call({ ...event, result }, context);
    expect(await reloaded.before_tool_call({ toolName: "classclaw_analyze_interaction", params: { text: "重新分析" } }, context))
      .toMatchObject({ block: true });
    expect(await hooks.before_tool_call({ toolName: "classclaw_propose_write", params: { payload: {} } }, context))
      .toMatchObject({ block: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("runs an already-due reminder promptly instead of creating an invalid past cron", () => {
    expect(resolveReminderCronAt("2026-08-30T09:00:00+08:00", Date.parse("2026-08-30T10:00:00+08:00")))
      .toBe("2026-08-30T02:00:05.000Z");
  });

  it("reads classroom status and camera only for the bound class", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(String(input));
      calls.push(url.pathname);
      const classId = url.pathname.split("/")[4];
      return new Response(JSON.stringify({ success: true, data: { class_id: classId, camera: { class_id: classId } } }));
    });
    const tool = registeredTools().classclaw_read;
    await expect(tool.execute("1", { resource: "classroom_status", class_id: "class-a", bound_class_id: "class-a" }))
      .resolves.toMatchObject({ details: { data: { class_id: "class-a" } } });
    await expect(tool.execute("2", { resource: "classroom_camera", class_id: "class-a", bound_class_id: "class-a" }))
      .resolves.toMatchObject({ details: { success: true } });
    await expect(tool.execute("3", { resource: "classroom_broadcast", class_id: "class-a", broadcast_id: "b-1", bound_class_id: "class-a" }))
      .resolves.toMatchObject({ details: { success: true } });
    expect(calls).toEqual([
      "/api/v1/classes/class-a/classroom/status",
      "/api/v1/classes/class-a/classroom/camera",
      "/api/v1/classes/class-a/classroom/broadcasts/b-1",
    ]);
    await expect(tool.execute("4", { resource: "classroom_status", class_id: "class-b", bound_class_id: "class-a" }))
      .rejects.toThrow("CLASS_SCOPE_VIOLATION");
  });

  it("samples classroom overview with a scoped POST and refuses cross-class calls before HTTP", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ success: true,
      data: { class_id: "class-a", audio_used: false } })));
    const tool = registeredTools().classclaw_read;
    await expect(tool.execute("1", { resource: "classroom_observation", class_id: "class-b", bound_class_id: "class-a" }))
      .rejects.toThrow("CLASS_SCOPE_VIOLATION");
    expect(fetch).not.toHaveBeenCalled();
    await expect(tool.execute("2", { resource: "classroom_observation", class_id: "class-a", bound_class_id: "class-a" }))
      .resolves.toMatchObject({ details: { data: { class_id: "class-a", audio_used: false } } });
    expect(String(fetch.mock.calls[0][0])).toContain("/classes/class-a/classroom/observation");
    expect(fetch.mock.calls[0][1]?.method).toBe("POST");
  });

  it("forces the bound class onto classroom reads and broadcast proposals", async () => {
    const hook = registeredHooks().before_tool_call;
    const read = await hook({ toolName: "classclaw_read", params: { resource: "classroom_status", class_id: "other" } },
      { agentId: "agent-a", sessionKey: "classroom", runId: "classroom-run" });
    expect(read).toMatchObject({ block: true });

    const scoped = await hook({ toolName: "classclaw_read", params: { resource: "classroom_camera" } },
      { agentId: "agent-a", sessionKey: "classroom", runId: "classroom-run-2" });
    expect(scoped).toMatchObject({ params: { class_id: "class-a", bound_class_id: "class-a" } });

    const proposal = await hook({
      toolName: "classclaw_propose_write",
      params: { operation_type: "classroom.broadcast.send", payload: { mode: "three_part", student_nos: ["3"], predicate: "去扫地" } },
    }, { agentId: "agent-a", sessionKey: "classroom", runId: "classroom-run-3" });
    expect(proposal).toMatchObject({ params: { payload: { class_id: "class-a", predicate: "去扫地" } } });

    const crossClass = await hook({
      toolName: "classclaw_propose_write",
      params: { operation_type: "classroom.volume.set", payload: { class_id: "class-b", volume: 40 } },
    }, { agentId: "agent-a", sessionKey: "classroom", runId: "classroom-run-4" });
    expect(crossClass).toMatchObject({ block: true });
  });

  it("registers preview and chat-confirmed commit tools", () => {
    const tools: string[] = [];
    const hooks: string[] = [];
    entry.register?.({
      pluginConfig: { baseUrl: "http://127.0.0.1:8000" },
      registerHttpRoute(route: { auth: string; path: string }) {
        expect(route.auth).toBe("gateway");
        expect(["/api/v1/classclaw/web-session-thinking", "/api/v1/classclaw/web-chat-reasoning"]).toContain(route.path);
      },
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
    expect(hooks).toEqual(["before_prompt_build", "after_tool_call", "before_tool_call"]);
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
