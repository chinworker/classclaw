import { execFile } from "node:child_process";
import { readFile, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, isAbsolute, relative, resolve } from "node:path";
import { promisify } from "node:util";

import { Type } from "typebox";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import type { OpenClawPluginDefinition } from "openclaw/plugin-sdk/plugin-entry";
import { sharedTurnGuard, toolFailure, toolResult as result, validateReadParams } from "./toolRuntime.js";
import { sessionThinkingHandler } from "./sessionThinking.js";
import { chatReasoningHandler } from "./chatReasoning.js";
import { memoryPromptHook } from "./agentMemory.js";

type JsonObject = Record<string, unknown>;
type ClassClawConfig = { baseUrl: string; apiToken?: string; timeoutMs: number; allowedUploadRoots: string[]; agentClasses: Record<string, string> };

const operationTypes = [
  "memory.upsert", "memory.forget",
  "student.create", "student.update", "student.update.batch", "seating.update",
  "attendance.set", "homework.create", "homework.status.batch", "student_event.create",
  "student_event.batch", "exam.create", "score.batch", "lesson_override.create", "arrangement.create",
  "duty.schedule.confirm", "duty.assignment.score",
  "classroom.broadcast.send", "classroom.volume.set",
] as const;

const readResources = [
  "agent_memory",
  "classes", "class_summary", "student_search", "student_detail", "daily_timetable", "morning_briefing",
  "student_analysis", "class_analysis", "attention_students", "write_proposal", "interaction_analysis", "reminder_delivery",
  "classroom_status", "classroom_camera", "classroom_broadcast", "classroom_observation",
] as const;

type ToolContext = { sessionKey?: string; agentId?: string };
type ReminderCronJob = { name: string; declarationKey: string; at: string; agentId?: string; sessionKey: string; message: string };
type ReminderCronScheduler = (job: ReminderCronJob) => Promise<unknown>;

const execFileAsync = promisify(execFile);

export function resolveReminderCronAt(remindAt: string, currentMs = Date.now()): string {
  const parsedAt = Date.parse(remindAt);
  return Number.isFinite(parsedAt) && parsedAt <= currentMs ? new Date(currentMs + 5_000).toISOString() : remindAt;
}

async function scheduleReminderCron(job: ReminderCronJob): Promise<unknown> {
  const scheduleAt = resolveReminderCronAt(job.at);
  const args = [
    "cron", "add", "--name", job.name, "--declaration-key", job.declarationKey,
    "--at", scheduleAt, "--tz", process.env.CLASSCLAW_TIMEZONE || "Asia/Shanghai",
    "--delete-after-run", "--session", "isolated",
    "--session-key", job.sessionKey, "--message", job.message, "--announce", "--json",
  ];
  if (job.agentId) args.push("--agent", job.agentId);
  const { stdout } = await execFileAsync(process.env.OPENCLAW_BIN || "openclaw", args, { timeout: 30_000, maxBuffer: 256 * 1024 });
  return JSON.parse(stdout);
}

export function resolveConfig(raw: Record<string, unknown> | undefined): ClassClawConfig {
  const baseUrl = typeof raw?.baseUrl === "string" ? raw.baseUrl.replace(/\/+$/, "") : "http://127.0.0.1:8000";
  const configuredToken = typeof raw?.apiToken === "string" && raw.apiToken.length > 0 ? raw.apiToken : undefined;
  const apiToken = configuredToken ?? process.env.CLASSCLAW_API_TOKEN;
  const timeoutMs = typeof raw?.timeoutMs === "number" && raw.timeoutMs > 0 ? Math.min(raw.timeoutMs, 120_000) : 120_000;
  const configuredRoots = Array.isArray(raw?.allowedUploadRoots) ? raw.allowedUploadRoots.filter((item): item is string => typeof item === "string") : [];
  const allowedUploadRoots = (configuredRoots.length > 0 ? configuredRoots : [process.cwd(), tmpdir()]).map((item) => resolve(item));
  const agentClasses = raw?.agentClasses && typeof raw.agentClasses === "object" && !Array.isArray(raw.agentClasses)
    ? Object.fromEntries(Object.entries(raw.agentClasses).filter((entry): entry is [string, string] => typeof entry[1] === "string")) : {};
  return { baseUrl, apiToken, timeoutMs, allowedUploadRoots, agentClasses };
}

async function assertAllowedFile(filePath: string, roots: string[]): Promise<string> {
  const target = await realpath(resolve(filePath));
  const allowed = await Promise.all(roots.map(async (root) => {
    try { return await realpath(root); } catch { return resolve(root); }
  }));
  if (!allowed.some((root) => {
    const pathFromRoot = relative(root, target);
    return pathFromRoot === "" || (!pathFromRoot.startsWith("..") && !isAbsolute(pathFromRoot));
  })) throw new Error(`File is outside allowedUploadRoots: ${target}`);
  return target;
}

export function createClient(config: ClassClawConfig) {
  async function request(path: string, init: RequestInit = {}): Promise<unknown> {
    const headers = new Headers(init.headers);
    if (config.apiToken) headers.set("Authorization", `Bearer ${config.apiToken}`);
    if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
    const response = await fetch(`${config.baseUrl}${path}`, { ...init, headers, signal: AbortSignal.timeout(config.timeoutMs) });
    let payload: JsonObject;
    try { payload = await response.json() as JsonObject; }
    catch { throw new Error(`ClassClaw returned HTTP ${response.status} without JSON`); }
    if (!response.ok || payload.success === false) {
      const error = payload.error as JsonObject | undefined;
      throw Object.assign(new Error(`${String(error?.code ?? `HTTP_${response.status}`)}: ${String(error?.message ?? "ClassClaw request failed")}`), {
        code: String(error?.code ?? `HTTP_${response.status}`), details: error?.details,
      });
    }
    return payload.data;
  }
  return {
    get: (path: string) => request(path),
    post: (path: string, body: unknown) => request(path, { method: "POST", body: JSON.stringify(body) }),
    patch: (path: string, body: unknown) => request(path, { method: "PATCH", body: JSON.stringify(body) }),
    async upload(filePath: string, fields: Record<string, string | undefined>) {
      const target = await assertAllowedFile(filePath, config.allowedUploadRoots);
      const bytes = await readFile(target);
      const form = new FormData();
      form.append("file", new Blob([bytes]), basename(target));
      for (const [key, value] of Object.entries(fields)) if (value) form.append(key, value);
      return request("/api/v1/attachments", { method: "POST", body: form });
    },
  };
}

function query(params: Record<string, unknown>, keys: string[]): string {
  const search = new URLSearchParams();
  for (const key of keys) {
    const value = params[key];
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  return search.size ? `?${search.toString()}` : "";
}

export async function scheduleCommittedReminders(_api: any, context: ToolContext, data: unknown, scheduler: ReminderCronScheduler = scheduleReminderCron): Promise<{ scheduled: number; warnings: string[] }> {
  const proposals = (Array.isArray(data) ? data : [data]).filter((item): item is JsonObject => Boolean(item && typeof item === "object"));
  const sessionKey = context.sessionKey;
  if (!sessionKey) return { scheduled: 0, warnings: ["当前会话没有可用的主动消息路由，提醒已保存但未创建 Agent 定时任务"] };
  let scheduled = 0;
  const warnings: string[] = [];
  for (const proposal of proposals) {
    const resultJson = proposal.result_json as JsonObject | undefined;
    const arrangement = resultJson?.arrangement as JsonObject | undefined;
    const reminders = Array.isArray(resultJson?.reminders) ? resultJson.reminders : [];
    if (!arrangement || reminders.length === 0) continue;
    for (const rawReminder of reminders) {
      if (!rawReminder || typeof rawReminder !== "object") continue;
      const reminder = rawReminder as JsonObject;
      const reminderId = typeof reminder.id === "string" ? reminder.id : undefined;
      const remindAt = typeof reminder.remind_at === "string" ? reminder.remind_at : undefined;
      if (!reminderId || !remindAt) continue;
      const declarationKey = `classclaw-reminder-${reminderId}`;
      const message = [
        "这是 ClassClaw 系统触发的主动提醒任务。",
        `先调用 classclaw_read，resource=reminder_delivery，reminder_id=${reminderId}，核验提醒仍为 active=true 且 due=true。`,
        "若无效、已完成、已取消或已发送，只回复 NO_REPLY。",
        "若有效，调用 classclaw_mark_reminder_sent 标记成功，然后只向用户发送一句简短提醒；包含事项名称和时间，不提工具、数据库或任务编号，可有一句温和小幽默。",
      ].join("\n");
      try {
        await scheduler({
          name: `ClassClaw 提醒 ${reminderId.slice(0, 8)}`,
          declarationKey,
          agentId: context.agentId,
          sessionKey,
          at: remindAt,
          message,
        });
        scheduled += 1;
      } catch (error) {
        warnings.push(`提醒 ${reminderId} 的 Agent 定时任务创建失败：${String(error).slice(0, 200)}`);
      }
    }
  }
  return { scheduled, warnings };
}

const plugin: OpenClawPluginDefinition = definePluginEntry({
  id: "classclaw",
  name: "ClassClaw 班级管理",
  description: "Parse, preview, confirm in chat, and write class-management information through ClassClaw.",
  register(api) {
    const config = resolveConfig(api.pluginConfig);
    const client = createClient(config);
    api.on("before_prompt_build", memoryPromptHook(config.agentClasses, createClient({ ...config, timeoutMs: 3_000 }).get));
    api.registerHttpRoute({
      path: "/api/v1/classclaw/web-session-thinking", auth: "gateway", match: "exact",
      gatewayRuntimeScopeSurface: "trusted-operator", handler: sessionThinkingHandler(config.agentClasses),
    });
    api.registerHttpRoute({
      path: "/api/v1/classclaw/web-chat-reasoning", auth: "gateway", match: "exact",
      gatewayRuntimeScopeSurface: "trusted-operator",
      handler: chatReasoningHandler(config.agentClasses, (listener) => api.runtime.events.onAgentEvent(listener)),
    });
    const guard = sharedTurnGuard();
    const turnKey = (context: { agentId?: string; sessionKey?: string; runId?: string }, runId?: string) =>
      context.runId || runId ? JSON.stringify([context.agentId, context.sessionKey, context.runId || runId]) : undefined;

    api.on("after_tool_call", (event, context) => {
      if (event.toolName.startsWith("classclaw_")) guard.after(turnKey(context, event.runId), event.toolName, event.result, event.error);
    });

    api.on("before_tool_call", async (event, context) => {
      if (!event.toolName.startsWith("classclaw_")) return;
      const classId = context.agentId ? config.agentClasses[context.agentId] : undefined;
      const params = { ...event.params } as Record<string, unknown>;
      if (classId && event.toolName === "classclaw_upload_file") params.class_id = classId;
      if (classId && event.toolName === "classclaw_analyze_interaction") params.class_id = classId;
      if (classId && event.toolName === "classclaw_read") {
        if (params.resource === "classes") return { block: true, blockReason: "班级专属智能体不能列出其他班级" };
        if (params.class_id && params.class_id !== classId) return { block: true, blockReason: "班级专属智能体不能跨班读取" };
        params.class_id = classId;
        params.bound_class_id = classId;
      }
      if (classId && event.toolName === "classclaw_propose_write") {
        const payload = { ...((params.payload as JsonObject | undefined) ?? {}) };
        if (payload.class_id && payload.class_id !== classId) return { block: true, blockReason: "班级专属智能体不能跨班创建写入预览" };
        payload.class_id = classId;
        params.payload = payload;
      }
      if (classId && ["classclaw_commit_write", "classclaw_commit_writes", "classclaw_mark_reminder_sent"].includes(event.toolName)) params.bound_class_id = classId;
      const blocked = guard.before(turnKey(context, event.runId), event.toolName, params);
      if (blocked) return { block: true, blockReason: blocked };
      return { params };
    }, { priority: 200 });

    api.registerTool({
      name: "classclaw_health",
      label: "ClassClaw 健康检查",
      description: "Check that the ClassClaw backend is reachable before starting a workflow.",
      parameters: Type.Object({}),
      async execute() { return result(await client.get("/health")); },
    });

    api.registerTool({
      name: "classclaw_analyze_interaction",
      label: "用 OpenClaw 清洗 ClassClaw 输入",
      description: "Analyze the original write request once, including reusable schedules, preferences and facts worth remembering even without an explicit remember command. Includes confirmed class memory and the full roster. Returns validated previews or clarification; memory is saved only after confirmation. On failure, explain and end the turn; do not retry or bypass analysis.",
      parameters: Type.Object({
        channel: Type.String(), external_message_id: Type.Optional(Type.String()), sender_id: Type.Optional(Type.String()),
        message_type: Type.Optional(Type.String()), text: Type.Optional(Type.String()),
        attachment_ids: Type.Optional(Type.Array(Type.String(), { maxItems: 8 })),
        class_id: Type.Optional(Type.String()), onboarding_session_id: Type.Optional(Type.String()),
        requested_by: Type.Optional(Type.String()), idempotency_key: Type.Optional(Type.String()),
      }),
      async execute(_id, params) {
        try { return result(await client.post("/api/v1/interaction-analyses", params)); }
        catch (error) { return toolFailure(error); }
      },
    });

    api.registerTool({
      name: "classclaw_upload_file",
      label: "暂存 ClassClaw 原始附件",
      description: "Stage a user attachment in ClassClaw for the current analysis. This does not change class business data.",
      parameters: Type.Object({ path: Type.String(), description: Type.Optional(Type.String()), class_id: Type.Optional(Type.String()) }),
      async execute(_id, params) {
        const values = params as { path: string; description?: string; class_id?: string };
        return result(await client.upload(values.path, { description: values.description, class_id: values.class_id }));
      },
    });

    api.registerTool({
      name: "classclaw_read",
      label: "读取 ClassClaw 数据",
      description: "Read a whitelisted ClassClaw resource. Lists use page (from 1) and page_size (1–100); inspect total before declaring a roster complete. student_detail/student_analysis accept student_no (班内学号) instead of student_id; student_no requires class_id because numbers are only unique within a class. Use the returned analysis_id to read interaction_analysis, never a message or session ID. Never use this tool for writes.",
      parameters: Type.Object({
        resource: Type.Union(readResources.map((value) => Type.Literal(value))), class_id: Type.Optional(Type.String()),
        student_id: Type.Optional(Type.String()), student_no: Type.Optional(Type.String()), proposal_id: Type.Optional(Type.String()), session_id: Type.Optional(Type.String()),
        analysis_id: Type.Optional(Type.String()),
        reminder_id: Type.Optional(Type.String()),
        broadcast_id: Type.Optional(Type.String()),
        bound_class_id: Type.Optional(Type.String()),
        q: Type.Optional(Type.String()), exact_name: Type.Optional(Type.Boolean()), date: Type.Optional(Type.String()),
        page: Type.Optional(Type.Integer({ minimum: 1 })), page_size: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
        start_date: Type.Optional(Type.String()), end_date: Type.Optional(Type.String()),
      }),
      async execute(_id, params) {
        const values = params as Record<string, unknown> & { resource: typeof readResources[number] };
        validateReadParams(values);
        if (["student_detail", "student_analysis"].includes(values.resource) && !values.student_id && values.student_no) {
          // Deterministic class-scoped 学号 lookup; the LLM never needs roster UUIDs.
          const found = await client.get(`/api/v1/students${query({ class_id: values.class_id, student_no: values.student_no, page_size: 2 }, ["class_id", "student_no", "page_size"])}`) as { items?: Array<{ id?: unknown }> };
          const items = Array.isArray(found.items) ? found.items : [];
          if (items.length !== 1 || typeof items[0]?.id !== "string") {
            throw new Error(`STUDENT_NOT_FOUND: 班级内找不到学号 ${String(values.student_no)} 对应的唯一学生`);
          }
          values.student_id = items[0].id;
        }
        let path: string;
        switch (values.resource) {
          case "agent_memory": path = `/api/v1/classes/${encodeURIComponent(String(values.class_id ?? ""))}/agent-memories${query(values, ["date", "q"])}`; break;
          case "classes": path = `/api/v1/classes${query(values, ["page", "page_size"])}`; break;
          case "class_summary": path = `/api/v1/classes/${encodeURIComponent(String(values.class_id ?? ""))}/summary`; break;
          case "student_search": path = `/api/v1/students${query(values, ["class_id", "q", "exact_name", "student_no", "page", "page_size"])}`; break;
          case "student_detail": path = `/api/v1/students/${encodeURIComponent(String(values.student_id ?? ""))}`; break;
          case "daily_timetable": path = `/api/v1/classes/${encodeURIComponent(String(values.class_id ?? ""))}/timetable/daily${query({ lesson_date: values.date }, ["lesson_date"])}`; break;
          case "morning_briefing": path = `/api/v1/briefings/morning${query(values, ["class_id", "date"])}`; break;
          case "student_analysis": path = `/api/v1/analytics/students/${encodeURIComponent(String(values.student_id ?? ""))}/comprehensive${query(values, ["start_date", "end_date"])}`; break;
          case "class_analysis": path = `/api/v1/analytics/classes/${encodeURIComponent(String(values.class_id ?? ""))}/comprehensive${query(values, ["start_date", "end_date"])}`; break;
          case "attention_students": path = `/api/v1/analytics/classes/${encodeURIComponent(String(values.class_id ?? ""))}/attention-students${query(values, ["start_date", "end_date"])}`; break;
          case "write_proposal": path = `/api/v1/write-proposals/${encodeURIComponent(String(values.proposal_id ?? ""))}`; break;
          case "interaction_analysis": path = `/api/v1/interaction-analyses/${encodeURIComponent(String(values.analysis_id ?? ""))}`; break;
          case "reminder_delivery": path = `/api/v1/reminders/${encodeURIComponent(String(values.reminder_id ?? ""))}`; break;
          case "classroom_status": path = `/api/v1/classes/${encodeURIComponent(String(values.class_id ?? ""))}/classroom/status`; break;
          case "classroom_camera": path = `/api/v1/classes/${encodeURIComponent(String(values.class_id ?? ""))}/classroom/camera`; break;
          case "classroom_observation": {
            if (values.bound_class_id && values.bound_class_id !== values.class_id) {
              throw new Error("CLASS_SCOPE_VIOLATION: classroom observation must use the bound class");
            }
            const observed = await client.post(`/api/v1/classes/${encodeURIComponent(String(values.class_id ?? ""))}/classroom/observation`, {});
            if ((observed as JsonObject).class_id !== values.class_id) throw new Error("CLASS_SCOPE_VIOLATION: classroom observation belongs to another class");
            return result(observed);
          }
          case "classroom_broadcast": path = `/api/v1/classes/${encodeURIComponent(String(values.class_id ?? ""))}/classroom/broadcasts/${encodeURIComponent(String(values.broadcast_id ?? ""))}`; break;
          default: throw new Error(`Unsupported ClassClaw read resource: ${String(values.resource)}`);
        }
        const data = await client.get(path);
        const scopedClassId = typeof values.bound_class_id === "string" ? values.bound_class_id : undefined;
        if (scopedClassId && ["student_detail", "student_analysis"].includes(values.resource)) {
          const studentId = String(values.student_id ?? "");
          // student_detail already returned the same payload; only analysis needs the extra lookup.
          const detail = values.resource === "student_detail" ? data as JsonObject
            : await client.get(`/api/v1/students/${encodeURIComponent(studentId)}`) as JsonObject;
          const student = (detail.student ?? detail) as JsonObject;
          if (student.class_id !== scopedClassId) throw new Error("CLASS_SCOPE_VIOLATION: student does not belong to this agent's class");
        }
        if (scopedClassId && values.resource === "interaction_analysis" && ((data as JsonObject).analysis as JsonObject | undefined)?.class_id !== scopedClassId) {
          throw new Error("CLASS_SCOPE_VIOLATION: analysis does not belong to this agent's class");
        }
        if (scopedClassId && values.resource === "reminder_delivery") {
          const arrangement = (data as JsonObject).arrangement as JsonObject | undefined;
          if (arrangement?.class_id !== scopedClassId) throw new Error("CLASS_SCOPE_VIOLATION: reminder does not belong to this agent's class");
        }
        if (scopedClassId && ["classroom_status", "classroom_broadcast"].includes(values.resource)
          && (data as JsonObject).class_id !== scopedClassId) {
          throw new Error("CLASS_SCOPE_VIOLATION: classroom record does not belong to this agent's class");
        }
        if (scopedClassId && values.resource === "classroom_camera") {
          const camera = (data as JsonObject).camera as JsonObject | null | undefined;
          if (camera && camera.class_id !== scopedClassId) throw new Error("CLASS_SCOPE_VIOLATION: camera does not belong to this agent's class");
        }
        return result(data);
      },
    });

    api.registerTool({
      name: "classclaw_propose_write",
      label: "生成 ClassClaw 写入预览",
      description: "Preview an already deterministic structured write without changing business data. Natural-language analysis already returns validated previews; display those directly and do not recreate them here.",
      parameters: Type.Object({
        operation_type: Type.Union(operationTypes.map((value) => Type.Literal(value))),
        payload: Type.Record(Type.String(), Type.Unknown()),
        requested_by: Type.Optional(Type.String()), idempotency_key: Type.Optional(Type.String()),
      }),
      async execute(_id, params) { return result(await client.post("/api/v1/write-proposals", params)); },
    });

    api.registerTool((context: ToolContext) => ({
        name: "classclaw_commit_write",
        label: "确认并执行 ClassClaw 写入",
        description: "Execute one previously previewed write after explicit chat confirmation, without another approval card. On expiry, conflict or failure, explain and end the turn; do not re-analyze, replace the preview or retry.",
        parameters: Type.Object({
          proposal_id: Type.String(), revision: Type.Integer({ minimum: 1 }), confirmed_by: Type.String(),
          confirmation_note: Type.Optional(Type.String()), bound_class_id: Type.Optional(Type.String()),
        }),
        async execute(_id, params) {
          const values = params as { proposal_id: string; revision: number; confirmed_by: string; confirmation_note?: string };
          const scoped = values as typeof values & { bound_class_id?: string };
          let data: unknown;
          try {
            data = await client.post(`/api/v1/write-proposals/${encodeURIComponent(values.proposal_id)}/confirm`, {
              revision: values.revision, confirmed_by: values.confirmed_by, confirmation_note: values.confirmation_note, bound_class_id: scoped.bound_class_id,
            });
          } catch (error) { return toolFailure(error, "commit"); }
          const reminderSchedule = await scheduleCommittedReminders(api, context, data);
          return result({ data, reminder_schedule: reminderSchedule });
        },
      }), { optional: true, name: "classclaw_commit_write" });

    api.registerTool((context: ToolContext) => ({
        name: "classclaw_commit_writes",
        label: "批量确认并执行 ClassClaw 写入",
        description: "Atomically execute the current preview group after explicit chat confirmation, without another approval card. If any item fails, none are written. On failure, explain and end the turn; do not re-analyze, replace previews or retry.",
        parameters: Type.Object({
          items: Type.Array(Type.Object({
            proposal_id: Type.String(),
            revision: Type.Integer({ minimum: 1 }),
          }), { minItems: 1, maxItems: 50 }),
          confirmed_by: Type.String(),
          confirmation_note: Type.Optional(Type.String()),
          bound_class_id: Type.Optional(Type.String()),
        }),
        async execute(_id, params) {
          const values = params as {
            items: Array<{ proposal_id: string; revision: number }>;
            confirmed_by: string;
            confirmation_note?: string;
            bound_class_id?: string;
          };
          let data: unknown;
          try { data = await client.post("/api/v1/write-proposals/confirm-batch", values); }
          catch (error) { return toolFailure(error, "commit"); }
          const reminderSchedule = await scheduleCommittedReminders(api, context, data);
          return result({ data, reminder_schedule: reminderSchedule });
        },
      }), { optional: true, name: "classclaw_commit_writes" });

    api.registerTool({
      name: "classclaw_mark_reminder_sent",
      label: "标记 ClassClaw 主动提醒已发送",
      description: "Used only by a scheduled ClassClaw reminder turn after verifying the reminder is active and due. This does not require user confirmation.",
      parameters: Type.Object({ reminder_id: Type.String(), bound_class_id: Type.Optional(Type.String()) }),
      async execute(_id, params) {
        const values = params as { reminder_id: string; bound_class_id?: string };
        return result(await client.post(`/api/v1/reminders/${encodeURIComponent(values.reminder_id)}/sent`, { bound_class_id: values.bound_class_id }));
      },
    });

    api.registerTool({
      name: "classclaw_cancel_write",
      label: "取消 ClassClaw 写入预览",
      description: "Cancel a pending write preview without changing business data.",
      parameters: Type.Object({ proposal_id: Type.String(), cancelled_by: Type.Optional(Type.String()) }),
      async execute(_id, params) {
        const values = params as { proposal_id: string; cancelled_by?: string };
        return result(await client.post(`/api/v1/write-proposals/${encodeURIComponent(values.proposal_id)}/cancel`, { cancelled_by: values.cancelled_by }));
      },
    });

  },
});

export default plugin;
