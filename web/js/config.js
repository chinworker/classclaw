// 浏览器只读取后端明确投影的非敏感启动配置；失败时保留与服务端一致的安全默认值。

export const appConfig = {
  config_hash: null,
  brand: {
    name: "ClassClaw",
    title: "ClassClaw · 班主任工作台",
    mark: "C",
    subtitle: "班主任工作台",
    admin_name: "ClassClaw Control",
    admin_subtitle: "ADMIN CONSOLE",
    login_description: "班级管理、座位课表、值日作业考勤与班级专属智能体。",
  },
  features: { file_analysis: true, event_ai: true, wechat_binding: true, reminders: true },
  runtime: { timezone: "Asia/Shanghai" },
  agent_chat: { default_thinking_level: "off" },
  storage: { max_attachment_bytes: 20 * 1024 * 1024 },
  semester_defaults: {},
  web: { ai_request_timeout_seconds: 150, usage_window_days: 30, database_page_size: 50 },
  wechat: {
    qr_binding_timeout_seconds: 300,
    qr_initial_poll_ms: 1500,
    qr_poll_ms: 2000,
    qr_retry_ms: 3500,
  },
  classroom: {
    broadcast_max_chars: 160,
    broadcast_max_segments: 20,
    display_seconds_default: 15,
    display_seconds_max: 120,
    speak_repeat_max: 3,
    volume_ceiling: 100,
    media_lease_seconds: 300,
    media_stop_grace_seconds: 30,
    media_provider: "none",
    pairing_code_ttl_seconds: 600,
    heartbeat_timeout_seconds: 90,
  },
};

let loading = null;

export async function loadAppConfig() {
  if (loading) return loading;
  loading = (async () => {
    try {
      const response = await fetch("/api/v1/app-config", {
        headers: { "X-ClassClaw-Surface": "web" },
        cache: "no-store",
      });
      const payload = await response.json();
      if (!response.ok || payload.success === false || !payload.data) throw new Error("配置接口不可用");
      const value = payload.data;
      appConfig.config_hash = value.config_hash || null;
      Object.assign(appConfig.brand, value.brand || {});
      Object.assign(appConfig.features, value.features || {});
      Object.assign(appConfig.runtime, value.runtime || {});
      Object.assign(appConfig.agent_chat, value.agent_chat || {});
      Object.assign(appConfig.storage, value.storage || {});
      Object.assign(appConfig.semester_defaults, value.semester_defaults || {});
      Object.assign(appConfig.web, value.web || {});
      Object.assign(appConfig.wechat, value.wechat || {});
      Object.assign(appConfig.classroom, value.classroom || {});
    } catch {
      // 后端不可用时登录页仍可显示；后续 API 请求会给出统一的网络错误。
    }
    document.title = appConfig.brand.title;
    return appConfig;
  })();
  return loading;
}

export function featureEnabled(name) {
  return appConfig.features[name] !== false;
}
