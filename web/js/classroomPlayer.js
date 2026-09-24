// One receive-only peer per authorized track. A video-only lease never requests audio.
import { api } from "./api.js";
import { el } from "./util.js";

export function classroomPlayer(classId, lease, { onError = () => {} } = {}) {
  let disposed = false;
  const peers = new Set();
  const remoteIds = new Set();
  const timers = new Set();
  const status = el("p", { class: "muted" }, "正在等待教室采集端…");
  const video = el("video", { autoplay: true, playsinline: true, controls: true, muted: true });
  const audio = el("audio", { autoplay: true, controls: true });
  const host = el("div", { class: "classroom-player" }, video, lease.audio_allowed ? audio : null, status);

  const closeRemote = (id) => api(`/classes/${classId}/classroom/media/close`, { method: "POST", body: { peer_id: id } }).catch(() => {});

  function delay(ms) {
    return new Promise((resolve) => {
      const entry = { id: null, resolve };
      entry.id = setTimeout(() => { timers.delete(entry); resolve(); }, ms);
      timers.add(entry);
    });
  }

  async function connect(track, offerPath) {
    for (let attempt = 0; attempt < 10 && !disposed; attempt += 1) {
      let pc;
      try {
        pc = new RTCPeerConnection({ iceServers: lease.ice_servers || [] });
        peers.add(pc);
        pc.addTransceiver(track, { direction: "recvonly" });
        pc.ontrack = (event) => {
          if (disposed) return;
          const element = track === "video" ? video : audio;
          element.srcObject = new MediaStream([event.track]);
          element.play().catch(() => { status.textContent = "已收到媒体，请点击播放器开始播放声音。"; });
        };
        pc.onconnectionstatechange = () => {
          if (disposed) return;
          if (pc.connectionState === "connected") status.textContent = "实时连接已建立";
          if (["failed", "disconnected"].includes(pc.connectionState)) {
            status.textContent = "媒体连接已中断，请停止后重新观看";
          }
        };
        await pc.setLocalDescription(await pc.createOffer());
        const deadline = Date.now() + 8000;
        while (!disposed && pc.iceGatheringState !== "complete" && Date.now() < deadline) await delay(100);
        if (disposed) break;
        if (pc.iceGatheringState !== "complete") throw new Error("网络候选收集超时，请检查网络或 TURN 配置");
        const result = await api(offerPath.replace(/^\/api\/v1/, ""), { method: "POST", body: { sdp: pc.localDescription.sdp } });
        if (disposed) { await closeRemote(result.peer_id); break; }
        remoteIds.add(result.peer_id);
        await pc.setRemoteDescription({ type: "answer", sdp: result.sdp });
        return;
      } catch (error) {
        if (pc) { pc.close(); peers.delete(pc); }
        if (disposed) return;
        if (attempt === 9 || ["CLASS_ACCESS_DENIED", "MEDIA_TRACK_DENIED", "MEDIA_SESSION_EXPIRED"].includes(error.code)) {
          status.textContent = error.message;
          onError(error);
          return;
        }
        await delay(1500);
      }
    }
  }

  if (typeof RTCPeerConnection === "undefined") status.textContent = "当前浏览器不支持 WebRTC，请使用支持的浏览器。";
  else for (const [track, offerPath] of Object.entries(lease.offers || {})) {
    if (track === "audio" && !lease.audio_allowed) continue;
    if (["video", "audio"].includes(track)) void connect(track, offerPath);
  }

  return {
    el: host,
    dispose() {
      disposed = true;
      for (const entry of timers) { clearTimeout(entry.id); entry.resolve(); }
      timers.clear();
      for (const pc of peers) pc.close();
      peers.clear();
      video.srcObject = null;
      audio.srcObject = null;
      for (const id of remoteIds) void closeRemote(id);
      remoteIds.clear();
    },
  };
}
