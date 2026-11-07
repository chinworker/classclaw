// 教室设备与实时监控：终端配对、显示屏与音量、摄像头登记、观看会话。

import { el, clear, toast, fmtDateTime, copyText } from "../util.js";
import { api } from "../api.js";
import { state } from "../state.js";
import { appConfig } from "../config.js";
import { classroomPlayer } from "../classroomPlayer.js";
import { pageHeader, field, errorPanel, skeleton, emptyState, statusBadge, metricCard, confirmDanger } from "../components.js";

const REFRESH_MS = 5000;
const INSTALLER = "/downloads/classroom-mate/latest/ClassroomMate-Setup-win-x64.exe";
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
  const view = { classId, mounted: true, refreshTimer: null, leaseTimer: null, lease: null, watching: true, nextAttempt: 0, live: {} };
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
  mount.append(pageHeader("实时监控",
    "查看教室画面和声音。Windows 本机设备在 Classroom Mate 中选择。", refreshButton));

  const summaryHost = el("div");
  const pairingHost = el("div");
  const terminalHost = el("div");
  const volumeHost = el("div");
  const cameraHost = el("div");
  const monitorHost = el("div");
  const advanced = el("details", { class: "card", open: ctx?.query?.settings === "1" },
    el("summary", {}, "终端与监控设置"),
    el("div", { class: "card" }, el("h3", {}, "Windows 终端软件"),
      el("a", { href: INSTALLER, download: true }, "下载最新版 Classroom Mate（Windows x64）")),
    pairingHost, terminalHost, volumeHost, cameraHost);
  mount.append(monitorHost, summaryHost, advanced);

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
  const pairingKey = (device) => JSON.stringify([device?.device_id, device?.pairing_status, device?.pairing_expires_at]);
  let pairingState = status.device;
  let pairingCode = null;
  let issuing = false;
  let statusRequest = 0;

  function drawPairing(data) {
    const device = data.device;
    clear(pairingHost);
    const card = el("div", { class: "card" }, el("h3", {}, "终端配对"));

    async function issue(name) {
      if (issuing) return;
      issuing = true;
      try {
        const result = await api(`/classes/${classId}/classroom/pairing`, { method: "POST", body: { name } });
        if (!live()) return;
        statusRequest += 1;
        pairingState = pairingCode = result;
        drawPairing({ device: result });
        toast("配对码已生成，只显示一次", "success");
      } catch (error) {
        if (live()) toast(error.message, "error");
      } finally { issuing = false; }
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
        "本班还没有教室终端。生成一次性配对码，在教室电脑的 Classroom Mate 中输入。"),
      field("终端名称", nameInput), issueButton);
    } else if (device.pairing_status === "unpaired") {
      const waiting = device.pairing_expires_at && !(Date.parse(device.pairing_expires_at) <= Date.parse(data.server_time));
      const code = waiting ? pairingCode?.pairing_code : null;
      if (code) card.append(
        el("p", {}, "在 Classroom Mate 的「设置 → 服务器与配对」输入这 4 位数字。默认服务器为 claw.dickhead.top。"),
        el("p", { class: "pairing-code" }, code),
        el("p", { class: "muted" }, `只显示一次 · 截止 ${fmtDateTime(device.pairing_expires_at)}`));
      card.append(el("p", { "aria-live": "polite" }, waiting
        ? "等待教室终端配对，完成后网页会自动更新。"
        : "配对码已失效，可以重新生成。"),
        el("div", { class: "row-gap" },
          code
            ? el("button", { class: "primary", type: "button",
                onclick: () => copyText(code).then(() => toast("配对码已复制", "success"), () => toast("复制失败，请手动抄写", "error")) }, "复制配对码")
            : el("button", { class: "primary", type: "button", onclick: () => void issue(device.name) }, "重新生成配对码"),
          waiting ? el("button", { class: "secondary", type: "button", onclick: () => act("pairing", "DELETE", "配对码已作废") }, "作废配对码") : null));
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
          title: `解绑教室终端「${device.name}」`,
          lines: ["会删除本班终端记录并撤销观看会话；摄像头登记保留但不再关联该终端。", "点名记录会保留。", "班级 Agent、记忆与其他渠道不受影响。"],
          confirmLabel: "解绑终端",
        });
        if (confirmed) await act("device", "DELETE", "终端已解绑");
      });
      card.append(el("p", {}, `已配对：${device.name}`),
        el("div", { class: "row-gap" }, revokeButton, unbindButton));
    }
    pairingHost.append(card);
  }

  /* ---------- 终端能力、输出设备与音量 ---------- */
  function drawTerminal(data) {
    const device = data.device;
    clear(terminalHost);
    clear(volumeHost);
    view.live.volumeReadout = null;
    view.live.terminalActions = [];
    if (!device || device.pairing_status !== "paired") return;
    const capabilities = device.capabilities || {};
    const inventory = device.inventory || {};
    const displays = [...new Set([...(capabilities.displays || []),
      ...(inventory.displays || []).map((row) => row.name || row.identifier)])].filter(Boolean);
    const speakers = (inventory.speakers || []).map((row) => row.name || row.identifier).filter(Boolean);
    function terminalAction(label, css, action, capable = true) {
      const button = el("button", { class: css, type: "button" }, label);
      let pending = false;
      const refresh = () => { button.disabled = pending || !capable || !pairingState?.online; };
      view.live.terminalActions.push(refresh);
      refresh();
      button.addEventListener("click", async () => {
        pending = true;
        refresh();
        try { await action(); }
        catch (error) { if (live()) toast(error.message, "error"); }
        finally { pending = false; if (live()) refresh(); }
      });
      return button;
    }

    const displaySelect = el("select", { "aria-label": "目标显示屏" },
      el("option", { value: "" }, "选择显示屏"),
      ...displays.map((name) => el("option", { value: name, selected: name === device.display_name }, name)));
    const speakerSelect = el("select", { "aria-label": "扬声器" },
      el("option", { value: "" }, "选择扬声器"),
      ...speakers.map((name) => el("option", { value: name, selected: name === device.audio_output_name }, name)));
    const applyOutput = terminalAction("下发输出设备设置", "primary", async () => {
      const body = {};
      if (displaySelect.value) body.display_name = displaySelect.value;
      if (speakerSelect.value) body.audio_output_name = speakerSelect.value;
      if (!Object.keys(body).length) { toast("请先选择显示屏或扬声器", "error"); return; }
      const result = await api(`/classes/${classId}/classroom/device/output`, { method: "POST", body });
      if (live()) toast(result.note || "设置已下发", "success");
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
      inventory.local_capture
        ? el("p", { class: "muted" }, `本机设置 · 显示屏：${device.display_name || "未设置"} · 扬声器：${device.audio_output_name || "未设置"}`)
        : el("div", {}, el("p", { class: "muted" }, "旧版终端可在这里设置，更新 Classroom Mate 后可直接在 Windows 选择。"),
            el("div", { class: "form-grid" }, field("目标显示屏", displaySelect), field("扬声器", speakerSelect)), applyOutput)));

    const slider = el("input", { type: "range", min: 0, max: limits.volume_ceiling || 100, step: 1,
      value: device.volume_level == null ? 50 : device.volume_level, "aria-label": "教室系统音量" });
    const readout = el("b", {}, device.volume_level == null ? "未知" : `${device.volume_level}%`);
    view.live.volumeReadout = readout;
    slider.addEventListener("input", () => { readout.textContent = `${slider.value}%`; });
    const muteBox = el("input", { type: "checkbox", checked: !!device.muted, "aria-label": "静音" });
    const applyVolume = terminalAction("下发音量", "primary", async () => {
      const result = await api(`/classes/${classId}/classroom/volume`,
        { method: "POST", body: { volume: Number(slider.value), mute: !!muteBox.checked } });
      if (live()) toast(result.note || "音量指令已下发", "success");
    }, !!capabilities.volume_control);

    function testButton(kind, label) {
      return terminalAction(label, "secondary", async () => {
        const result = await api(`/classes/${classId}/classroom/device/test`, { method: "POST", body: { kind } });
        if (live()) toast(`${result.text}；结果以终端回执为准`, "success");
      });
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
    const inventory = data.device?.inventory || {};
    const local = inventory.local_capture;
    const selectedCamera = local && (inventory.cameras || []).find((row) => row.identifier === local.camera);
    const selectedMic = local && (inventory.microphones || []).find((row) => row.identifier === local.microphone);
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
    pathSelect.value = "windows_capture";
    kindSelect.value = "windows_device";
    const identifierInput = el("input", { type: "text", maxlength: 200, placeholder: "终端上报的设备标识", "aria-label": "设备标识" });
    const microphoneSelect = el("select", { "aria-label": "监控麦克风" },
      el("option", { value: "" }, "不选择麦克风"),
      ...(data.device?.inventory?.microphones || []).map((row) => el("option", { value: row.identifier }, row.name || row.identifier)));
    const protocolSelect = el("select", { "aria-label": "协议" }, el("option", { value: "" }, "无"),
      ...["rtsp", "rtmps", "rtmp", "http", "https", "whip"].map((value) => el("option", { value }, value)));
    const locationInput = el("input", { type: "text", maxlength: 500, placeholder: "rtsp://10.20.30.40:554/stream1", "aria-label": "流地址" });
    const credentialInput = el("input", { type: "text", maxlength: 100, placeholder: "例如 lab_one", "aria-label": "凭据引用" });
    const audioBox = el("input", { type: "checkbox", "aria-label": "该来源带音轨" });
    const legacyAudio = el("input", { type: "checkbox", "aria-label": "使用所选麦克风" });
    const submit = el("button", { class: "primary", type: "button" }, camera ? "更换摄像头" : "连接摄像头");
    const reported = el("p", { class: "muted" },
      `终端选择 · 摄像头：${selectedCamera?.name || "未选择或不可用"} · 麦克风：${selectedMic?.name || "未选择或不可用"}。保存终端设置后可刷新本页。`);
    const network = el("div", {}, el("p", { class: "muted" }, "网络流凭据由服务器配置。流地址请仅填写协议、主机和路径。"),
      el("div", { class: "form-grid" }, field("协议", protocolSelect), field("流地址", locationInput),
        field("凭据引用", credentialInput, "CLASSCLAW_CLASSROOM_CAMERA_<引用名>")),
      el("label", { class: "checkbox-row" }, audioBox, "该来源带音轨"));
    const legacy = el("div", { class: "form-grid" }, field("设备标识", identifierInput, "从上方终端上报的摄像头清单中复制"),
      field("监控麦克风", microphoneSelect), el("label", { class: "checkbox-row" }, legacyAudio, "使用所选麦克风"));
    const isLocal = () => kindSelect.value === "windows_device";
    let submitting = false;
    const syncCameraOnline = () => {
      submit.disabled = submitting || (isLocal() && !!local && (!selectedCamera || !pairingState?.online || (!!local.microphone && !selectedMic)));
    };
    view.live.syncCameraOnline = syncCameraOnline;
    const syncSource = () => {
      const builtIn = isLocal();
      network.hidden = builtIn;
      legacy.hidden = !builtIn || !!local;
      reported.hidden = !builtIn || !local;
      syncCameraOnline();
      submit.textContent = builtIn && local ? (camera ? "确认更换监控来源" : "登记终端选择的来源") : (camera ? "更换摄像头" : "连接摄像头");
    };
    pathSelect.addEventListener("change", () => {
      kindSelect.value = pathSelect.value === "windows_capture" ? "windows_device" : "network_stream";
      syncSource();
    });
    kindSelect.addEventListener("change", syncSource);

    submit.addEventListener("click", async () => {
      const builtIn = isLocal();
      const microphone = builtIn ? (local ? selectedMic?.identifier : microphoneSelect.value) || null : null;
      const body = {
        access_path: pathSelect.value, source_kind: kindSelect.value,
        device_identifier: builtIn ? (local ? selectedCamera?.identifier : identifierInput.value) || null : null,
        protocol: builtIn ? null : protocolSelect.value || null,
        microphone_identifier: microphone,
        location: builtIn ? "" : locationInput.value,
        credential_ref: builtIn ? "" : credentialInput.value,
        audio_capable: builtIn ? (local ? !!microphone : !!legacyAudio.checked) : !!audioBox.checked,
      };
      if (camera) body.expected_revision = camera.config_revision;
      submitting = true;
      syncCameraOnline();
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
        submitting = false;
        if (live()) syncCameraOnline();
      }
    });

    card.append(el("h4", {}, camera ? "更换摄像头" : "连接摄像头"),
      el("div", { class: "form-grid" }, field("接入路径", pathSelect), local ? null : field("来源类型", kindSelect)),
      local ? reported : legacy, network,
      el("div", { class: "row-gap" }, submit, camera ? disconnectButton(camera) : null));
    syncSource();
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
  const playerHost = el("div", { class: "monitor-player" });
  const monitorMessage = el("p", { class: "muted", "aria-live": "polite" });
  const viewers = el("span", {}, "0");
  const watch = el("button", { class: "secondary", type: "button" }, "重新连接");
  const sound = el("button", { class: "secondary", type: "button", hidden: true }, "开启现场声音");
  const observationHost = el("div");
  const observe = el("button", { class: "secondary", type: "button" }, "查看现场概况（单帧）");
  const leases = `/classes/${classId}/classroom/media-sessions`;

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
  monitorHost.append(el("div", { class: "card" }, el("h3", {}, "实时监控"), playerHost, monitorMessage,
    el("p", { class: "muted" }, "正在观看：", viewers, " 人"),
    el("div", { class: "row-gap" }, watch, sound, observe), observationHost));

  function sourceKey(data) {
    const camera = data.camera?.camera;
    if (camera && camera.source_kind !== "windows_device") return `${camera.camera_id}:${camera.config_revision}`;
    const selected = data.device?.inventory?.local_capture;
    return selected?.camera != null
      ? selected.camera ? JSON.stringify([data.device.device_id, selected.camera, selected.microphone]) : ""
      : camera ? `${camera.camera_id}:${camera.config_revision}` : "";
  }

  function updateMonitor(data) {
    status = data;
    const camera = data.camera?.camera;
    viewers.textContent = String(data.streaming?.viewers ?? 0);
    sound.hidden = !view.lease?.audio_allowed;
    observe.hidden = !camera;
    observe.disabled = !data.media_ready;
    watch.textContent = view.lease ? "停止观看" : view.watching ? "重新连接" : "继续观看";
    const key = sourceKey(data);
    if (view.lease && view.watching && view.source !== key && !view.busy) {
      void connect(true);
      return;
    }
    if (view.lease || view.busy) return;
    if (!view.watching) { monitorMessage.textContent = "已停止观看"; return; }
    if (data.media_provider === "none") { monitorMessage.textContent = "服务器尚未启用媒体转发，Windows 本机预览仍可使用。"; return; }
    if (data.media_ready === false) { monitorMessage.textContent = "正在等待服务器媒体服务就绪…"; return; }
    if (!key) { monitorMessage.textContent = "请在 Classroom Mate 中选择摄像头，保存后这里会自动播放。"; return; }
    if (camera?.access_path !== "server_direct" && !data.device?.online) {
      monitorMessage.textContent = "教室终端离线，恢复后自动播放。";
      return;
    }
    if (Date.now() >= view.nextAttempt) void connect();
  }

  function showButtons() {
    watch.disabled = sound.disabled = !!view.busy;
    watch.textContent = view.lease ? "停止观看" : view.watching ? "重新连接" : "继续观看";
    sound.hidden = !view.lease?.audio_allowed;
    sound.textContent = view.soundPlaying ? "静音" : "播放现场声音";
  }

  async function dropLease() {
    const lease = view.lease;
    if (!lease) return;
    // Keep the current lease until the server confirms release so a failed request can be retried.
    await api(`${leases}/${lease.session_id}`, { method: "DELETE" });
    if (!live() || view.lease !== lease) return;
    view.lease = null;
    view.player?.dispose();
    view.player = null;
    window.clearTimeout(view.leaseTimer);
    clear(playerHost);
  }

  async function connect(replace = false) {
    if (view.busy || (view.lease && !replace)) return;
    view.busy = true;
    showButtons();
    const source = sourceKey(status);
    try {
      if (replace) await dropLease();
      if (!live()) return;
      monitorMessage.textContent = "正在连接教室画面…";
      const lease = await api(leases, { method: "POST", body: { audio: true, follow_terminal: true } });
      if (!live() || source !== sourceKey(status) || !lease.available) {
        await api(`${leases}/${lease.session_id}`, { method: "DELETE" });
        if (live() && !lease.available) throw new Error(lease.message);
        return;
      }
      view.lease = lease;
      view.soundPlaying = false;
      view.source = source;
      view.player = classroomPlayer(classId, lease, { onError(error, track) {
        if (!live() || view.lease?.session_id !== lease.session_id) return;
        monitorMessage.textContent = error.message;
        if (track === "video") void reconnectAfterFailure(lease.session_id);
      } });
      clear(playerHost).append(view.player.el);
      monitorMessage.textContent = lease.audio_unavailable_reason || (lease.audio_allowed ? "现场声音已接入 · 播放器默认静音" : "仅视频 · 当前来源无可用声音");
      scheduleRenew(lease);
    } catch (error) {
      if (live()) {
        view.nextAttempt = Date.now() + 10000;
        monitorMessage.textContent = error.message;
      }
    } finally {
      view.busy = false;
      if (live()) showButtons();
    }
  }

  function scheduleRenew(lease) {
    window.clearTimeout(view.leaseTimer);
    const ttl = new Date(lease.expires_at).getTime() - Date.now();
    view.leaseTimer = window.setTimeout(() => { if (live()) void renew(lease.session_id); }, Math.max(1000, ttl - 30000));
  }

  async function renew(sessionId) {
    if (!live() || view.lease?.session_id !== sessionId || view.busy) return;
    try {
      const updated = await api(`${leases}/${sessionId}/renew`, { method: "POST", body: {} });
      if (!live() || view.lease?.session_id !== sessionId) return;
      Object.assign(view.lease, updated);
      scheduleRenew(view.lease);
    } catch {
      await reconnectAfterFailure(sessionId);
    }
  }

  async function reconnectAfterFailure(sessionId) {
    if (!live() || view.lease?.session_id !== sessionId || view.busy) return;
    view.busy = true;
    showButtons();
    try {
      await dropLease();
      view.nextAttempt = Date.now() + 5000;
    } catch (error) {
      if (live()) {
        monitorMessage.textContent = error.message;
        view.leaseTimer = window.setTimeout(() => void reconnectAfterFailure(sessionId), 5000);
      }
    } finally {
      view.busy = false;
      if (live()) showButtons();
    }
  }

  watch.addEventListener("click", async () => {
    if (view.busy) return;
    if (!view.lease) {
      view.watching = true;
      view.nextAttempt = 0;
      updateMonitor(status);
      return;
    }
    view.watching = false;
    view.busy = true;
    showButtons();
    try {
      await dropLease();
      if (live()) monitorMessage.textContent = "已停止观看";
    } catch (error) {
      if (live()) monitorMessage.textContent = error.message;
    } finally {
      view.busy = false;
      if (live()) showButtons();
    }
  });
  sound.addEventListener("click", () => {
    if (!view.lease?.audio_allowed || !view.player) return;
    view.soundPlaying = !view.soundPlaying;
    view.player.setMuted(!view.soundPlaying);
    showButtons();
  });

  /* ---------- 刷新 ---------- */
  function applyStatus(data, refreshForms) {
    const device = data.device;
    const expired = device?.pairing_expires_at && Date.parse(device.pairing_expires_at) <= Date.parse(data.server_time);
    const next = expired ? { ...device, pairing_expires_at: null } : device;
    const pairingChanged = pairingKey(pairingState) !== pairingKey(next);
    const terminalChanged = pairingState?.device_id !== next?.device_id || pairingState?.pairing_status !== next?.pairing_status;
    if (pairingCode && pairingKey(pairingCode) !== pairingKey(next)) pairingCode = null;
    if (pairingState?.pairing_status === "unpaired" && next?.pairing_status === "paired") {
      toast("教室终端已配对", "success");
    }
    const onlineChanged = pairingState?.online !== next?.online;
    pairingState = next;
    drawSummary(data);
    if (refreshForms || pairingChanged) drawPairing({ ...data, device: next });
    if (refreshForms || terminalChanged) {
      drawTerminal(data);
      drawCamera(data);
    } else if (onlineChanged) {
      view.live.terminalActions.forEach((refresh) => refresh());
      view.live.syncCameraOnline?.();
    }
    if (view.live.volumeReadout && device?.volume_level != null) {
      view.live.volumeReadout.textContent = `${device.volume_level}%`;
    }
    updateMonitor(data);
  }

  async function readStatus(refreshForms) {
    const request = ++statusRequest;
    const data = await api(`/classes/${classId}/classroom/status`);
    if (live() && request === statusRequest) applyStatus(data, refreshForms);
  }

  async function reload() {
    try {
      await readStatus(true);
    } catch (error) {
      if (live()) clear(summaryHost).append(errorPanel(error, { onRetry: reload }));
    }
  }

  refreshButton.addEventListener("click", () => void reload());

  // 常规轮询保留表单输入。配对或连接状态变化时同步可用操作。
  async function tick() {
    try {
      await readStatus(false);
    } catch {
      // 轮询失败不打断正在填写的表单；概况区保留上一次结果。
    } finally {
      if (live()) view.refreshTimer = window.setTimeout(() => { if (live()) void tick(); }, REFRESH_MS);
    }
  }

  applyStatus(status, true);
  view.refreshTimer = window.setTimeout(() => { if (live()) void tick(); }, REFRESH_MS);
}
