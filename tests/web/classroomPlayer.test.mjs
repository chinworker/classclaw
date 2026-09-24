import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, tick, response } from "./dom.mjs";
import { classroomPlayer } from "../../web/js/classroomPlayer.js";
import { saveSession } from "../../web/js/state.js";

let peers, calls, players;
beforeEach(() => {
  installDom(); saveSession("test-token"); peers = []; calls = []; players = [];
  globalThis.RTCPeerConnection = class {
    constructor() { peers.push(this); this.iceGatheringState = "complete"; }
    addTransceiver(track, options) { this.track = track; assert.equal(options.direction, "recvonly"); }
    async createOffer() { return { sdp: "sdp" }; }
    async setLocalDescription(value) { this.localDescription = value; }
    async setRemoteDescription(value) { this.remote = value; }
    close() { this.closed = true; }
  };
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return response(path.endsWith("/offer") ? { peer_id: path.includes("audio") ? "audio-peer" : "video-peer", sdp: "answer" } : { closed: true });
  };
});
afterEach(() => { players.forEach((player) => player.dispose()); delete globalThis.RTCPeerConnection; });
function player(audio) {
  const value = classroomPlayer("class-a", { audio_allowed: audio,
    offers: { video: "/api/v1/video/offer", audio: "/api/v1/audio/offer" } });
  players.push(value); return value;
}
test("video-only lease never negotiates an audio peer", async () => {
  const view = player(false); await tick();
  assert.deepEqual(peers.map((pc) => pc.track), ["video"]);
  assert.equal(calls.length, 1);
  view.dispose(); await tick();
  assert.ok(peers[0].closed);
  assert.ok(calls.some((row) => row.path.endsWith("/media/close")));
});
test("authorized sound uses a separate receive-only peer", async () => {
  const view = player(true); await tick();
  assert.deepEqual(peers.map((pc) => pc.track), ["video", "audio"]);
  view.dispose(); await tick();
  assert.equal(calls.filter((row) => row.path.endsWith("/media/close")).length, 2);
});
test("late signaling response is closed after leaving the page", async () => {
  let resolve;
  globalThis.fetch = (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("/offer")) return new Promise((done) => { resolve = done; });
    return Promise.resolve(response({ closed: true }));
  };
  const view = player(false); await tick(); view.dispose();
  resolve(response({ peer_id: "late-peer", sdp: "answer" })); await tick();
  assert.ok(peers[0].closed);
  assert.equal(peers[0].remote, undefined);
  assert.ok(calls.some((row) => row.path.endsWith("/media/close") && JSON.parse(row.options.body).peer_id === "late-peer"));
});
