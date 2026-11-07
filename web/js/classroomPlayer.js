// One receive-only peer per authorized track. A video-only lease never requests audio.
import { api } from "./api.js";
import { el } from "./util.js";

const CLOSED_LEASE = new Set(["CLASS_ACCESS_DENIED", "MEDIA_TRACK_DENIED", "MEDIA_SESSION_EXPIRED",
  "MEDIA_SESSION_INVALID", "CAMERA_DISCONNECTED", "CAMERA_REVISION_CONFLICT"]);

export function classroomPlayer(classId, lease, { onError = () => {} } = {}) {
  let disposed = false;
  const peers = new Set();
  const timers = new Set();
  const status = el("p", { class: "muted", role: "status" }, "正在等待教室采集端…");
  const video = el("video", { autoplay: true, playsinline: true, controls: true, muted: true });
  const audio = el("audio", { autoplay: true, muted: true });
  video.muted = audio.muted = true;
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
    const isVideo = track === "video";
    const element = isVideo ? video : audio;
    let retry = 1000;
    while (!disposed) {
      let pc, remoteId, mediaTrack;
      let frameCallback, lastDecoded = 0;
      let autoplayBlocked = false;
      try {
        pc = new RTCPeerConnection({ iceServers: lease.ice_servers || [] });
        peers.add(pc);
        pc.addTransceiver(track, { direction: "recvonly" });
        pc.ontrack = (event) => {
          if (disposed) return;
          mediaTrack = event.track;
          element.srcObject = new MediaStream([event.track]);
          if (isVideo && typeof video.requestVideoFrameCallback === "function") {
            const decoded = () => {
              if (disposed || !peers.has(pc)) return;
              lastDecoded = Date.now();
              frameCallback = video.requestVideoFrameCallback(decoded);
            };
            frameCallback = video.requestVideoFrameCallback(decoded);
          }
          element.play().catch(() => {
            if (disposed || !peers.has(pc)) return;
            autoplayBlocked = true;
            if (isVideo) status.textContent = "请点击播放器上的播放按钮。";
          });
        };
        await pc.setLocalDescription(await pc.createOffer());
        const deadline = Date.now() + 8000;
        while (!disposed && pc.iceGatheringState !== "complete" && Date.now() < deadline) await delay(100);
        if (disposed) break;
        if (pc.iceGatheringState !== "complete") throw new Error("网络候选收集超时，请检查网络或 TURN 配置");
        const result = await api(offerPath.replace(/^\/api\/v1/, ""),
          { method: "POST", body: { sdp: pc.localDescription.sdp }, timeoutMs: 18000 });
        remoteId = result.peer_id;
        if (disposed) break;
        await pc.setRemoteDescription({ type: "answer", sdp: result.sdp });
        let lastProgress = Date.now();
        let time = element.currentTime || 0;
        let hadVideo = false;
        while (!disposed) {
          if (["closed", "failed"].includes(pc.connectionState) || mediaTrack?.readyState === "ended") {
            throw new Error("连接中断，正在重新连接…");
          }
          const decoded = frameCallback !== undefined ? lastDecoded > lastProgress : element.currentTime > time;
          if (isVideo && element.videoWidth && decoded) {
            time = element.currentTime;
            hadVideo = true;
            autoplayBlocked = false;
            retry = 1000;
            lastProgress = Date.now();
            const message = `实时画面 · ${element.videoWidth}×${element.videoHeight}`;
            if (status.textContent !== message) status.textContent = message;
          }
          if (!isVideo && pc.connectionState === "connected" && mediaTrack) {
            retry = 1000;
            lastProgress = Date.now();
          }
          if (autoplayBlocked || (hadVideo && element.paused)) lastProgress = Date.now();
          if (Date.now() - lastProgress >= 15000) {
            throw new Error(hadVideo ? "画面中断，正在重新连接…" : "还未收到画面，正在重新连接…");
          }
          await delay(1000);
        }
      } catch (error) {
        if (disposed) return;
        if (isVideo) status.textContent = error.message;
        if (CLOSED_LEASE.has(error.code)) {
          onError(error, track);
          return;
        }
      } finally {
        if (frameCallback !== undefined) video.cancelVideoFrameCallback?.(frameCallback);
        if (pc && peers.delete(pc)) pc.close();
        element.srcObject = null;
        if (remoteId) void closeRemote(remoteId);
      }
      if (disposed) return;
      await delay(retry);
      retry = Math.min(retry * 2, 8000);
    }
  }

  if (typeof RTCPeerConnection === "undefined") status.textContent = "当前浏览器不支持 WebRTC，请使用支持的浏览器。";
  else for (const [track, offerPath] of Object.entries(lease.offers || {})) {
    if (track === "audio" && !lease.audio_allowed) continue;
    if (["video", "audio"].includes(track)) void connect(track, offerPath);
  }

  return {
    el: host,
    setMuted(muted) {
      audio.muted = muted;
      if (!muted && audio.srcObject) audio.play().catch(() => { status.textContent = "浏览器尚未允许播放声音，请再点一次播放。"; });
    },
    dispose() {
      disposed = true;
      for (const entry of timers) { clearTimeout(entry.id); entry.resolve(); }
      timers.clear();
      for (const pc of peers) pc.close();
      peers.clear();
      video.srcObject = null;
      audio.srcObject = null;
      // Each running negotiation closes its own peer, including a late SDP reply.
    },
  };
}
