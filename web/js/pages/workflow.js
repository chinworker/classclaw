// 工作流：查看班级创建引导（onboarding）会话与写入预览（proposal）。
// 边界：普通网页结构化写入直接调用领域接口，不产生 proposal；
// 只有 onboarding 最终创建与 OpenClaw/微信聊天写入使用 proposal。
// 后端没有 proposal 列表接口，这里提供 onboarding 会话列表 + 按 ID 查询 proposal。

import { el, clear, toast, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { navigate } from "../router.js";
import { state } from "../state.js";
import {
  pageHeader, dataTable, statusBadge, proposalReview, jsonDetails,
  errorPanel, skeleton, emptyState, confirmDanger, field,
} from "../components.js";

export async function render(mount, ctx, helpers) {
  clear(mount);
  mount.append(pageHeader("待确认记录", "查看班级创建进度。"));

  /* ---------- onboarding 会话 ---------- */
  const sessionsHost = el("div", { class: "card" },
    el("h3", {}, "班级创建记录"),
    skeleton(3));
  mount.append(sessionsHost);

  async function loadSessions() {
    clear(sessionsHost);
    sessionsHost.append(el("h3", {}, "班级创建记录"));
    let sessions;
    try {
      sessions = await api("/class-onboarding/sessions");
    } catch (error) {
      sessionsHost.append(errorPanel(error, { onRetry: loadSessions }));
      return;
    }
    if (!sessions?.length) {
      sessionsHost.append(emptyState("没有创建引导会话", "班主任可在“创建班级”中开始新的班级创建向导。"));
      return;
    }
    sessionsHost.append(dataTable({
      columns: [
        { key: "name", label: "班级", render: (s) => el("b", {}, s.draft_json?.class_info?.name || "未命名班级") },
        { key: "status", label: "状态", render: (s) => statusBadge(s.status) },
        { key: "current_step", label: "当前步骤" },
        { key: "revision", label: "版本" },
        { key: "updated_at", label: "更新时间", render: (s) => fmtDateTime(s.updated_at) },
        {
          key: "actions", label: "操作", render: (s) => {
            if (["draft", "awaiting_confirmation"].includes(s.status)) {
              return el("button", { class: "text-button", type: "button", onclick: () => navigate(`/onboarding/${s.id}`) }, "继续填写");
            }
            return el("span", { class: "muted" }, "—");
          },
        },
      ],
      rows: sessions,
      empty: { title: "没有创建引导会话" },
    }));
  }

  /* ---------- 按 ID 查询写入预览 ---------- */
  const idInput = el("input", { type: "text", placeholder: "粘贴 proposal ID（来自聊天智能体或 onboarding 预览）" });
  const resultHost = el("div");
  const queryBtn = el("button", { class: "primary", type: "button" }, "查询写入预览");

  async function loadProposal() {
    const id = idInput.value.trim();
    if (!id) { toast("请输入 proposal ID", "error"); return; }
    queryBtn.disabled = true;
    clear(resultHost);
    resultHost.append(skeleton(3));
    try {
      const proposal = await api(`/write-proposals/${encodeURIComponent(id)}`);
      renderProposal(proposal);
    } catch (error) {
      clear(resultHost);
      resultHost.append(errorPanel(error, { onRetry: loadProposal }));
    } finally {
      queryBtn.disabled = false;
    }
  }

  function renderProposal(proposal) {
    clear(resultHost);
    const head = el("div", { class: "row-gap", style: { alignItems: "center", flexWrap: "wrap" } },
      statusBadge(proposal.status),
      el("span", { class: "tag tag-id" }, `operation: ${proposal.operation_type}`),
      proposal.expires_at ? el("span", { class: "muted" }, `有效期至 ${fmtDateTime(proposal.expires_at)}`) : null,
      proposal.requested_by ? el("span", { class: "muted" }, `发起人：${proposal.requested_by}`) : null);
    const actions = el("div", { class: "row-gap", style: { marginTop: "12px" } });
    if (proposal.status === "pending_review") {
      if (proposal.operation_type === "class.onboarding.commit" && proposal.onboarding_session_id) {
        actions.append(el("button", {
          class: "primary", type: "button",
          onclick: () => navigate(`/onboarding/${proposal.onboarding_session_id}`),
        }, "前往创建向导完成复核"));
      } else {
        actions.append(el("span", { class: "muted" },
          "聊天写入预览需在聊天中向智能体明确确认后执行；网页不代替聊天复核。"));
      }
      actions.append(el("button", {
        class: "danger", type: "button",
        onclick: async () => {
          const ok = await confirmDanger({
            title: "取消该写入预览",
            lines: [
              `预览 ${proposal.id.slice(0, 8)}…（${proposal.operation_type}）将被标记为已取消。`,
              "取消后该预览不能再确认执行；业务数据不会被修改。",
            ],
            confirmLabel: "取消预览",
          });
          if (!ok) return;
          try {
            const updated = await api(`/write-proposals/${proposal.id}/cancel`, {
              method: "POST",
              body: { cancelled_by: state.user?.username || null },
            });
            toast("写入预览已取消", "success");
            renderProposal(updated);
          } catch (error) { toast(error.message, "error"); }
        },
      }, "取消该预览"));
    }
    resultHost.append(el("div", { class: "card" },
      head,
      proposalReview(proposal),
      proposal.error_message ? el("p", { class: "field-error" }, `执行错误：${proposal.error_message}`) : null,
      jsonDetails(proposal.normalized_payload_json, "查看归一化载荷摘要"),
      actions));
  }

  queryBtn.addEventListener("click", loadProposal);
  idInput.addEventListener("keydown", (e) => { if (e.key === "Enter") loadProposal(); });

  if (state.user?.role === "admin") {
    mount.append(el("div", { class: "card" },
      el("h3", {}, "写入预览查询"),
      el("p", { class: "muted" }, "管理员可按记录编号检查待确认内容。"),
      el("div", { class: "row-gap" }, field("记录编号", idInput), queryBtn),
      resultHost));
    mount.append(el("div", { class: "card" },
      el("h3", {}, "审计日志"),
      el("p", { class: "muted" }, "审计仅记录操作人、动作、实体与时间，不包含完整前后快照。"),
      el("button", { class: "secondary", type: "button", onclick: () => navigate("/admin/audit") }, "打开审计日志")));
  }

  if (state.user?.role === "admin" && ctx.query?.proposal) {
    idInput.value = ctx.query.proposal;
    loadProposal();
  }
  await loadSessions();
}
