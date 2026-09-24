// 教室设备与实时监控：终端配对、显示屏与音量、摄像头登记、观看会话。

import { el, clear, toast, fmtDateTime, copyText } from "../util.js";
import { api } from "../api.js";
import { state } from "../state.js";
import { appConfig } from "../config.js";
import { classroomPlayer } from "../classroomPlayer.js";
import { pageHeader, field, errorPanel, skeleton, emptyState, statusBadge, metricCard, confirmDanger } from "../components.js";

const REFRESH_MS = 5000;
const PATH_LABELS = { windows_capture: "Windows 本机采集", server_direct: "服务器直读", windows_relay: "Windows 出站中转" };
let activeView = null;

export function dispose() {
  const view = activeView;
  activeView = null;
  if (!view) return;
  view.mounted = false;
  view.player?.dispose();
  window.clearTimeout(view.refreshTimer);
  window.clearTimeout(view.leaseTimer);
  // 离页释放本人的观看会话；最后一名观看者离开后服务端会停流。
  if (view.lease) void api(`/classes/${view.classId}/classroom/media-sessions/${view.lease.session_id}`, { method: "DELETE" }).catch(() => {});
}

function deviceList(title, rows, emptyHint) {
  const items = (rows || []).map((row) => row.name || row.label || row.identifier).filter(Boolean);
  return el("div", {}, el("b", {}, title),
    items.length
      ? el("ul", { class: "device-list" }, (rows || []).map((row) => el("li", {},
          row.name || row.label || row.identifier || "未命名设备",
          row.identifier ? el("small", { class: "muted" }, ` · ${row.identifier}`) : null)))
      : el("p", { class: "muted" }, emptyHint));
}

export async function render(mount, ctx, helpers) {
  dispose();
  clear(mount);
  mount.append(skeleton(6));
  const classId = state.classId;
  const limits = appConfig.classroom || {};
  const view = { classId, mounted: true, refreshTimer: null, leaseTimer: null, lease: null, live: {} };
  activeView = view;
  const live = () => activeView === view && view.mounted && mount.isConnected && state.classId === classId;

  let status;
  try {
    status = await api(`/classes/${classId}/classroom/status`);
  } catch (error) {
    if (!live()) return;
    clear(mount);
    mount.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }

  if (!live()) return;
  clear(mount);
  const refreshButton = el("button", { class: "secondary", type: "button" }, "刷新");
  mount.append(pageHeader("教室设备与实时监控",
    "终端配对、显示屏与扬声器、音量、每班唯一摄像头与观看会话。摄像头的连接与更换只能在这里发起。", refreshButton));

  const summaryHost = el("div");
  const pairingHost = el("div");
  const terminalHost = el("div");
  const volumeHost = el("div");
  const cameraHost = el("div");
  const monitorHost = el("div");
  mount.append(summaryHost, pairingHost, terminalHost, volumeHost, cameraHost, monitorHost);

  /* ---------- 概况（轮询只更新这里，表单不会被重建） ---------- */
  function drawSummary(data) {
    const device = data.device;
    clear(summaryHost).append(el("div", { class: "card" },
      el("h3", {}, "教室概况"),
      el("div", { class: "metric-grid" },
        metricCard("终端", device ? (device.online ? "在线" : "离线") : "未配对",
          device ? `最后心跳 ${fmtDateTime(device.last_seen_at)}` : null, device?.online ? "ok" : "warn"),
        metricCard("摄像头", data.camera?.registered ? (data.camera.connected ? "已连通" : data.camera.camera.status) : "未登记"),
        metricCard("观看人数", data.streaming?.viewers ?? 0),
        metricCard("媒体转发", data.media_provider === "none" ? "未接入" : data.media_provider)),
      el("p", {}, data.note),
      el("p", { class: "muted" }, data.camera?.note || "")));
  }

  /* ---------- 配对 ---------- */
  function drawPairing(data) {
    const device = data.device;
    clear(pairingHost);
    const card = el("div", { class: "card" }, el("h3", {}, "终端配对"));

    function drawCode(result) {
      clear(pairingHost);
      pairingHost.append(el("div", { class: "card" },
        el("h3", {}, "终端配对"),
        el("p", {}, "在教室终端输入服务器地址和这个配对码。配对码只显示一次，兑换后立即失效。"),
        el("p", { class: "pairing-code" }, result.pairing_code),
        el("p", { class: "muted" }, `有效期 ${Math.round((result.pairing_ttl_seconds || limits.pairing_code_ttl_seconds || 600) / 60)} 分钟 · 截止 ${fmtDateTime(result.pairing_expires_at)}`),
        el("div", { class: "row-gap" },
          el("button", { class: "primary", type: "button",
            onclick: () => copyText(result.pairing_code).then(() => toast("配对码已复制", "success"), () => toast("复制失败，请手动抄写", "error")) }, "复制配对码"),
          el("button", { class: "secondary", type: "button", onclick: () => void reload() }, "我已完成配对"))));
    }

    async function issue(name) {
      try {
        const result = await api(`/classes/${classId}/classroom/pairing`, { method: "POST", body: { name } });
        if (live()) { drawCode(result); toast("配对码已生成，只显示一次", "success"); }
      } catch (error) {
        if (live()) toast(error.message, "error");
      }
    }

    async function act(path, method, message, body = {}) {
      try {
        await api(`/classes/${classId}/classroom/${path}`, { method, body });
        if (live()) { toast(message, "success"); await reload(); }
      } catch (error) {
        if (live()) toast(error.message, "error");
      }
    }

    if (!device) {
      const nameInput = el("input", { type: "text", value: "教室终端", maxlength: 100, "aria-label": "终端名称" });
      const issueButton = el("button", { class: "primary", type: "button" }, "生成配对码");
      issueButton.addEventListener("click", () => { issueButton.disabled = true; void issue(nameInput.value).finally(() => { if (live()) issueButton.disabled = false; }); });
      card.append(el("p", { class: "muted" },
        "本班还没有教室终端。生成一次性配对码，在教室电脑的 ClassClaw Classroom 中输入服务器地址和配对码；终端不持有全局共享 Token。"),
      field("终端名称", nameInput), issueButton);
    } else if (device.pairing_status === "unpaired") {
      card.append(el("p", {}, "已生成配对码，等待教室终端兑换。"),
        el("p", {}, el("b", {}, "有效期至："), fmtDateTime(device.pairing_expires_at)),
        el("div", { class: "row-gap" },
          el("button", { class: "primary", type: "button", onclick: () => void issue(device.name) }, "重新生成配对码"),
          el("button", { class: "secondary", type: "button", onclick: () => act("pairing", "DELETE", "配对码已作废") }, "作废配对码")));
    } else if (device.pairing_status === "revoked") {
      card.append(el("p", {}, "终端凭据已撤销，既有连接与观看会话已终止。"),
        el("button", { class: "primary", type: "button", onclick: () => void issue(device.name) }, "重新配对"));
    } else {
      const revokeButton = el("button", { class: "secondary", type: "button" }, "撤销凭据");
      revokeButton.addEventListener("click", async () => {
        const confirmed = await confirmDanger({
          title: "撤销终端凭据",
          lines: ["撤销后教室终端立即离线，正在观看的会话同时终止。", "点名广播与音量操作会被拒绝，直到重新配对。",
            "班级 Agent、记忆和微信等其他渠道不受影响。"],
          confirmLabel: "撤销凭据",
        });
        if (confirmed) await act("device/revoke", "POST", "凭据已撤销");
      });
      const unbindButton = el("button", { class: "danger", type: "button" }, "解绑终端");
      unbindButton.addEventListener("click", async () => {
        const confirmed = await confirmDanger({
          title: "解绑教室终端",
          lines: ["会删除本班终端记录并撤销观看会话；摄像头登记保留但不再关联该终端。", "点名记录会保留。", "班级 Agent、记忆与其他渠道不受影响。"],
          requireText: device.name, confirmLabel: "解绑终端",
        });
        if (confirmed) await act("device", "DELETE", "终端已解绑");
      });
      card.append(el("p", {}, `已配对：${device.name}`),
        el("p", { class: "muted" }, `最后心跳 ${fmtDateTime(device.last_seen_at)} · 配置版本 ${device.config_revision}`),
        el("div", { class: "row-gap" }, revokeButton, unbindButton));
    }
    pairingHost.append(card);
  }

  /* ---------- 终端能力、输出设备与音量 ---------- */
  function drawTerminal(data) {
    const device = data.device;
    clear(terminalHost);
    clear(volumeHost);
    if (!device || device.pairing_status !== "paired") return;
    const capabilities = device.capabilities || {};
    const inventory = device.inventory || {};
    const displays = [...new Set([...(capabilities.displays || []),
      ...(inventory.displays || []).map((row) => row.name || row.identifier)])].filter(Boolean);
    const speakers = (inventory.speakers || []).map((row) => row.name || row.identifier).filter(Boolean);
    const online = !!device.online;

    const displaySelect = el("select", { "aria-label": "目标显示屏" },
      el("option", { value: "" }, "选择显示屏"),
      ...displays.map((name) => el("option", { value: name, selected: name === device.display_name }, name)));
    const speakerSelect = el("select", { "aria-label": "扬声器" },
      el("option", { value: "" }, "选择扬声器"),
      ...speakers.map((name) => el("option", { value: name, selected: name === device.audio_output_name }, name)));
    const applyOutput = el("button", { class: "primary", type: "button", disabled: !online }, "下发输出设备设置");
    applyOutput.addEventListener("click", async () => {
      const body = {};
      if (displaySelect.value) body.display_name = displaySelect.value;
      if (speakerSelect.value) body.audio_output_name = speakerSelect.value;
      if (!Object.keys(body).length) { toast("请先选择显示屏或扬声器", "error"); return; }
      applyOutput.disabled = true;
      try {
        const result = await api(`/classes/${classId}/classroom/device/output`, { method: "POST", body });
        if (live()) toast(result.note || "设置已下发", "success");
      } catch (error) {
        if (live()) toast(error.message, "error");
      } finally {
        if (live()) applyOutput.disabled = !online;
      }
    });

    terminalHost.append(el("div", { class: "card" },
      el("h3", {}, "终端能力与设备检测"),
      el("div", { class: "metric-grid" },
        metricCard("程序版本", device.app_version || "未知"),
        metricCard("系统", device.os_version || "未知"),
        metricCard("屏幕显示", capabilities.display ? "可用" : "不可用"),
        metricCard("语音播报", capabilities.speak ? (capabilities.chinese_tts ? "中文可用" : "缺中文语音") : "不可用")),
      (data.device_warnings || []).length
        ? el("ul", { class: "warning-list" }, data.device_warnings.map((text) => el("li", {}, text))) : null,
      el("div", { class: "form-grid" },
        deviceList("摄像头", inventory.cameras, "终端没有上报可用摄像头"),
        deviceList("麦克风", inventory.microphones, "终端没有上报可用麦克风"),
        deviceList("扬声器", inventory.speakers, "终端没有上报可用扬声器")),
      el("p", { class: "muted" }, "清单由教室电脑的 Windows 接口检测上报；这里选择后下发给终端，权威配置在服务端。"),
      el("div", { class: "form-grid" }, field("目标显示屏", displaySelect), field("扬声器", speakerSelect)),
      applyOutput));

    const slider = el("input", { type: "range", min: 0, max: limits.volume_ceiling || 100, step: 1,
      value: device.volume_level == null ? 50 : device.volume_level, "aria-label": "教室系统音量" });
    const readout = el("b", {}, device.volume_level == null ? "未知" : `${device.volume_level}%`);
    view.live.volumeReadout = readout;
    slider.addEventListener("input", () => { readout.textContent = `${slider.value}%`; });
    const muteBox = el("input", { type: "checkbox", checked: !!device.muted, "aria-label": "静音" });
    const applyVolume = el("button", { class: "primary", type: "button", disabled: !online || !capabilities.volume_control }, "下发音量");
    applyVolume.addEventListener("click", async () => {
      applyVolume.disabled = true;
      try {
        const result = await api(`/classes/${classId}/classroom/volume`,
          { method: "POST", body: { volume: Number(slider.value), mute: !!muteBox.checked } });
        if (live()) toast(result.note || "音量指令已下发", "success");
      } catch (error) {
        if (live()) toast(error.message, "error");
      } finally {
        if (live()) applyVolume.disabled = !online || !capabilities.volume_control;
      }
    });

    function testButton(kind, label) {
      const button = el("button", { class: "secondary", type: "button", disabled: !online }, label);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await api(`/classes/${classId}/classroom/device/test`, { method: "POST", body: { kind } });
          if (live()) toast(`${result.text}；结果以终端回执为准`, "success");
        } catch (error) {
          if (live()) toast(error.message, "error");
        } finally {
          if (live()) button.disabled = !online;
        }
      });
      return button;
    }

    volumeHost.append(el("div", { class: "card" },
      el("h3", {}, "教室音量"),
      el("p", { class: "muted" }, `当前输出设备：${device.audio_output_name || "未设置"} · 终端回报音量：`, readout,
        device.muted ? "（已静音）" : ""),
      el("div", { class: "volume-row" }, slider, el("label", { class: "checkbox-row" }, muteBox, "静音")),
      el("div", { class: "row-gap" }, applyVolume, testButton("display", "显示测试"), testButton("speak", "播报测试")),
      el("p", { class: "muted" }, "调整的是 Windows 指定输出设备的系统主音量；设备实际值以终端回执为准，不将下发成功当作已生效。")));
  }

  /* ---------- 摄像头 ---------- */
  function drawCamera(data) {
    clear(cameraHost);
    const camera = data.camera?.camera;
    const card = el("div", { class: "card" }, el("h3", {}, "教室摄像头"));
    if (camera) {
      card.append(el("p", {}, `${camera.name} `, statusBadge(camera.status),
        el("small", { class: "muted" }, ` · 配置版本 ${camera.config_revision}`)),
      el("p", { class: "muted" }, data.camera.note),
      el("ul", { class: "device-list" },
        el("li", {}, `接入路径：${PATH_LABELS[camera.access_path] || camera.access_path}`),
        el("li", {}, camera.source_kind === "windows_device"
          ? `来源：Windows 设备 ${camera.device_label || camera.device_identifier || ""}`
          : `来源：${camera.protocol || ""} ${camera.location || ""}`),
        el("li", {}, `音轨：${camera.audio_capable ? "有" : "无（不能凭空获取现场声音）"}`),
        camera.credential_configured ? el("li", {}, "凭据：服务器侧已配置") : null,
        camera.last_error ? el("li", { class: "field-error" }, camera.last_error) : null));
    } else {
      card.append(el("p", { class: "muted" }, data.camera?.note || "本班尚未连接摄像头。每班最多一路，只能从这里发起。"));
    }

    const pathSelect = el("select", { "aria-label": "接入路径" },
      ...Object.entries(PATH_LABELS).map(([value, label]) => el("option", { value }, `${label}${value === "windows_capture" ? "（优先验证）" : ""}`)));
    const kindSelect = el("select", { "aria-label": "来源类型" },
      el("option", { value: "windows_device" }, "Windows 检测到的设备"),
      el("option", { value: "network_stream" }, "网络流（RTSP 等）"));
    const identifierInput = el("input", { type: "text", maxlength: 200, placeholder: "终端上报的设备标识", "aria-label": "设备标识" });
    const microphoneSelect = el("select", { "aria-label": "监控麦克风" },
      el("option", { value: "" }, "不选择麦克风"),
      ...(data.device?.inventory?.microphones || []).map((row) => el("option", { value: row.identifier }, row.name || row.identifier)));
    const protocolSelect = el("select", { "aria-label": "协议" }, el("option", { value: "" }, "无"),
      ...["rtsp", "rtmps", "rtmp", "http", "https", "whip"].map((value) => el("option", { value }, value)));
    const locationInput = el("input", { type: "text", maxlength: 500, placeholder: "rtsp://10.20.30.40:554/stream1", "aria-label": "流地址" });
    const credentialInput = el("input", { type: "text", maxlength: 100, placeholder: "例如 lab_one", "aria-label": "凭据引用" });
    const audioBox = el("input", { type: "checkbox", "aria-label": "该来源带音轨" });
    const submit = el("button", { class: "primary", type: "button" }, camera ? "更换摄像头" : "连接摄像头");
    pathSelect.addEventListener("change", () => {
      kindSelect.value = pathSelect.value === "server_direct" ? "network_stream" : "windows_device";
    });

    submit.addEventListener("click", async () => {
      const body = {
        access_path: pathSelect.value, source_kind: kindSelect.value,
        device_identifier: identifierInput.value || null, protocol: protocolSelect.value || null,
        microphone_identifier: microphoneSelect.value || null,
        location: locationInput.value || "", credential_ref: credentialInput.value || "",
        audio_capable: !!audioBox.checked,
      };
      if (camera) body.expected_revision = camera.config_revision;
      submit.disabled = true;
      try {
        const result = await api(`/classes/${classId}/classroom/camera`, { method: "POST", body });
        if (!live()) return;
        toast(result.replaced ? "摄像头已更换，旧观看会话已撤销" : "摄像头已登记", "success");
        if (result.replaced) toast(result.stop_requested ? "已请求停止旧采集，实际结果以终端回执为准" : "旧采集未收到停止指令，请复核终端状态", "info");
        await reload();
      } catch (error) {
        if (!live()) return;
        const current = error.details?.current;
        toast(current ? `请先确认替换对象：${current.name}（配置版本 ${current.config_revision}）` : error.message, "error");
      } finally {
        if (live()) submit.disabled = false;
      }
    });

    card.append(el("h4", {}, camera ? "更换摄像头" : "连接摄像头"),
      el("p", { class: "muted" }, "摄像头密码不要写进流地址；使用服务器侧凭据引用（环境变量 CLASSCLAW_CLASSROOM_CAMERA_<引用名>）。页面不显示也不保存明文。"),
      el("div", { class: "form-grid" },
        field("接入路径", pathSelect), field("来源类型", kindSelect),
        field("设备标识", identifierInput, "从上方终端上报的摄像头清单中复制"),
        field("监控麦克风", microphoneSelect), field("协议", protocolSelect), field("流地址", locationInput), field("凭据引用", credentialInput)),
      el("label", { class: "checkbox-row" }, audioBox, "该来源带音轨"),
      el("div", { class: "row-gap" }, submit, camera ? disconnectButton(camera) : null));
    cameraHost.append(card);

    function disconnectButton(target) {
      const button = el("button", { class: "danger", type: "button" }, "断开摄像头");
      button.addEventListener("click", async () => {
        const confirmed = await confirmDanger({
          title: `断开摄像头「${target.name}」`,
          lines: ["会先停止旧连接并撤销所有观看会话。", "断开后点名广播和音量功能仍然可用。"],
          confirmLabel: "断开摄像头",
        });
        if (!confirmed) return;
        try {
          await api(`/classes/${classId}/classroom/camera?expected_revision=${target.config_revision}`, { method: "DELETE" });
          if (live()) { toast("摄像头已断开", "success"); await reload(); }
        } catch (error) {
          if (live()) toast(error.message, "error");
        }
      });
      return button;
    }
  }

  /* ---------- 实时监控 ---------- */
  function drawMonitor(data) {
    clear(monitorHost);
    const camera = data.camera?.camera;
    const card = el("div", { class: "card" }, el("h3", {}, "实时监控"));
    if (!camera) {
      card.append(emptyState("尚未连接摄像头", "先登记本班摄像头，再开始观看。"));
      monitorHost.append(card);
      return;
    }
    const viewers = el("span", {}, String(data.streaming?.viewers ?? 0));
    view.live.viewers = viewers;
    const audioBox = el("input", { type: "checkbox", "aria-label": "同时接收现场声音", disabled: !camera.audio_capable });
    const start = el("button", { class: "primary", type: "button", disabled: !!view.lease || view.starting }, "开始观看");
    view.live.startButton = start;
    start.addEventListener("click", async () => {
      if (view.starting || view.lease) return;
      view.starting = true;
      start.disabled = true;
      try {
        const lease = await api(`/classes/${classId}/classroom/media-sessions`, { method: "POST", body: { audio: !!audioBox.checked } });
        if (!live()) {
          await api(`/classes/${classId}/classroom/media-sessions/${lease.session_id}`, { method: "DELETE" });
          return;
        }
        view.lease = lease;
        drawLease(lease);
      } catch (error) {
        if (live()) toast(error.message, "error");
      } finally {
        view.starting = false;
        if (live() && view.live.startButton) view.live.startButton.disabled = !!view.lease;
      }
    });
    const playerHost = el("div", { class: "monitor-player" });
    const observationHost = el("div");
    const observe = el("button", { class: "secondary", type: "button" }, "查看现场概况（单帧）");
    observe.addEventListener("click", async () => {
      observe.disabled = true;
      try {
        const result = await api(`/classes/${classId}/classroom/observation`, { method: "POST", body: {}, timeoutMs: 150000 });
        if (!live()) return;
        clear(observationHost).append(el("p", {}, `采样时间：${fmtDateTime(result.captured_at)}`),
          el("p", {}, result.summary.scene), el("p", {}, result.summary.visible_activity),
          el("p", { class: "muted" }, [...result.summary.limitations, result.note].join("；")));
      } catch (error) {
        if (live()) clear(observationHost).append(el("p", { class: "field-error" }, error.message));
      } finally { if (live()) observe.disabled = false; }
    });
    view.live.playerHost = playerHost;
    card.append(el("p", {}, "正在观看：", viewers, " 人 · 共用一路上行"),
      el("label", { class: "checkbox-row" }, audioBox, "同时接收现场声音"),
      el("p", { class: "muted" }, camera.audio_capable
        ? "只有明确开启后才采集或转发音轨；播放器静音不等于没有采集声音。"
        : "该摄像头没有音轨，勾选也不会收到现场声音。"),
      start, playerHost, observe, observationHost);
    monitorHost.append(card);
    if (view.lease) drawLease(view.lease);
  }

  function drawLease(lease) {
    const host = view.live.playerHost;
    if (!host) return;
    if (lease.available && !view.player) view.player = classroomPlayer(classId, lease);
    clear(host).append(
      lease.available
        ? view.player.el
        : el("p", { class: "field-error" }, `${lease.message}（${lease.reason}）`),
      el("p", { class: "muted" }, `会话 ${lease.session_id.slice(0, 8)} · 视频 ${lease.video_allowed ? "已授权" : "未授权"} · 声音 ${lease.audio_allowed ? "已授权" : "未授权"}`,
        lease.audio_unavailable_reason ? ` · ${lease.audio_unavailable_reason}` : ""),
      el("p", { class: "muted" }, `有效期至 ${fmtDateTime(lease.expires_at)}；离页或登出会自动释放`),
      el("div", { class: "row-gap" },
        el("button", { class: "secondary", type: "button", onclick: () => void renew(lease.session_id) }, "续期"),
        el("button", { class: "secondary", type: "button", onclick: () => void stopWatching(lease.session_id) }, "停止观看")));
    window.clearTimeout(view.leaseTimer);
    const ttl = new Date(lease.expires_at).getTime() - Date.now();
    if (ttl > 30000) view.leaseTimer = window.setTimeout(() => { if (live()) void renew(lease.session_id); }, ttl - 30000);
  }

  async function renew(sessionId) {
    if (!live() || view.lease?.session_id !== sessionId) return;
    try {
      const lease = await api(`/classes/${classId}/classroom/media-sessions/${sessionId}/renew`, { method: "POST", body: {} });
      if (!live() || view.lease?.session_id !== sessionId) return;
      view.lease = { ...view.lease, ...lease };
      drawLease(view.lease);
    } catch (error) {
      if (!live() || view.lease?.session_id !== sessionId) return;
      view.lease = null;
      view.player?.dispose();
      view.player = null;
      window.clearTimeout(view.leaseTimer);
      if (view.live.startButton) view.live.startButton.disabled = false;
      toast(error.message, "error");
    }
  }

  async function stopWatching(sessionId) {
    window.clearTimeout(view.leaseTimer);
    try {
      const data = await api(`/classes/${classId}/classroom/media-sessions/${sessionId}`, { method: "DELETE" });
      if (!live()) return;
      if (view.lease?.session_id === sessionId) view.lease = null;
      view.player?.dispose();
      view.player = null;
      toast(data.viewers ? "已停止本人的观看" : "已停止观看；最后一名观看者离开后将停止转发", "success");
      await reload();
    } catch (error) {
      if (live()) toast(error.message, "error");
    }
  }

  /* ---------- 刷新 ---------- */
  async function reload() {
    try {
      const data = await api(`/classes/${classId}/classroom/status`);
      if (!live()) return;
      drawSummary(data);
      drawPairing(data);
      drawTerminal(data);
      drawCamera(data);
      drawMonitor(data);
    } catch (error) {
      if (live()) clear(summaryHost).append(errorPanel(error, { onRetry: reload }));
    }
  }

  refreshButton.addEventListener("click", () => void reload());

  // 轮询只更新概况读数与终端回报音量，不重建表单，避免老师填到一半被清空。
  async function tick() {
    try {
      const data = await api(`/classes/${classId}/classroom/status`);
      if (!live()) return;
      drawSummary(data);
      const device = data.device;
      if (view.live.volumeReadout && device?.volume_level != null) {
        view.live.volumeReadout.textContent = `${device.volume_level}%`;
      }
      if (view.live.viewers) view.live.viewers.textContent = String(data.streaming?.viewers ?? 0);
    } catch {
      // 轮询失败不打断正在填写的表单；概况区保留上一次结果。
    } finally {
      if (live()) view.refreshTimer = window.setTimeout(() => { if (live()) void tick(); }, REFRESH_MS);
    }
  }

  drawSummary(status);
  drawPairing(status);
  drawTerminal(status);
  drawCamera(status);
  drawMonitor(status);
  view.refreshTimer = window.setTimeout(() => { if (live()) void tick(); }, REFRESH_MS);
}
