import tencentWeixinPlugin from "@tencent-weixin/openclaw-weixin/dist/index.js";
import { weixinPlugin } from "@tencent-weixin/openclaw-weixin/dist/src/channel.js";

const webLoginMethods = ["web.login.start", "web.login.wait"];

// OpenClaw 2026.7 discovers the QR-login provider from this declaration.
// Tencent Weixin 2.4.6 implements loginWithQrStart/loginWithQrWait but omits
// gatewayMethods, so the host otherwise reports "web login provider is not
// available" even though the channel itself loaded successfully.
weixinPlugin.gatewayMethods = Array.from(
  new Set([...(weixinPlugin.gatewayMethods ?? []), ...webLoginMethods]),
);

export default tencentWeixinPlugin;
