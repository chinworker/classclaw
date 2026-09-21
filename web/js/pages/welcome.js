// 无班级班主任的欢迎页：创建班级 + 恢复未完成 onboarding。

import { el, clear, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { navigate } from "../router.js";
import { emptyState, errorPanel, skeleton, statusBadge } from "../components.js";

export async function render(mount) {
  clear(mount);
  mount.append(skeleton(3));
  let sessions = [];
  let loadError = null;
  try {
    sessions = await api("/class-onboarding/sessions");
  } catch (error) {
    loadError = error;
  }
  clear(mount);

  const drafts = (sessions || []).filter((s) => ["draft", "awaiting_confirmation"].includes(s.status));

  mount.append(el("div", { class: "card" },
    el("div", { class: "welcome-hero" },
      el("h2", {}, "欢迎使用 ClassClaw"),
      el("p", { class: "muted" }, "每个班主任账号只能有一个班级。"),
      el("p", { class: "muted" }, "按步骤填写名单、课表和座位。微信可稍后绑定。"),
      el("div", { style: { marginTop: "18px" } },
        el("button", { class: "primary", type: "button", onclick: () => navigate("/onboarding") }, "创建班级")))));

  if (loadError) {
    mount.append(errorPanel(loadError));
    return;
  }
  if (drafts.length) {
    mount.append(el("div", { class: "card" },
      el("h3", {}, "未完成的班级创建引导"),
      el("p", { class: "muted" }, "以下草稿可以从中断处继续填写。"),
      ...drafts.map((s) => el("div", { class: "row-gap", style: { justifyContent: "space-between", padding: "10px 0", borderBottom: "1px solid var(--border)" } },
        el("div", {},
          el("b", {}, s.draft_json?.class_info?.name || "未命名班级"),
          el("div", { class: "muted", style: { fontSize: "12px" } }, `更新于 ${fmtDateTime(s.updated_at)} · 版本 ${s.revision}`)),
        el("div", { class: "row-gap" },
          statusBadge(s.status),
          el("button", { class: "secondary", type: "button", onclick: () => navigate(`/onboarding/${s.id}`) }, "继续填写")))),
      el("p", { class: "muted", style: { fontSize: "12px", marginTop: "10px" } }, "不想继续旧草稿时，可以重新开始创建，最终以最新填写的内容为准。")));
  } else if (!drafts.length && !sessions?.length) {
    mount.append(el("div", { class: "card" }, emptyState("尚无班级", "点击上方“创建班级”开始第一个班级的创建向导。")));
  }
}
