import tencentWeixinPlugin from "@tencent-weixin/openclaw-weixin/dist/index.js";
import { weixinPlugin } from "@tencent-weixin/openclaw-weixin/dist/src/channel.js";
import { saveWeixinAccount, registerWeixinAccountId, loadConfigRouteTag } from "@tencent-weixin/openclaw-weixin/dist/src/auth/accounts.js";
import { normalizeAccountId } from "openclaw/plugin-sdk/account-id";
import { loadConfig } from "openclaw/plugin-sdk/config-runtime";
import { createWebLogin, LoginError } from "./web-login.js";
import { createWechatTransport } from "./web-transport.js";
import { webLoginHandler } from "./web-route.js";

import { createAccountLogout } from "./account-cleanup.js";
import { resolveStateDir } from "@tencent-weixin/openclaw-weixin/dist/src/storage/state-dir.js";
import { clearContextTokensForAccount } from "@tencent-weixin/openclaw-weixin/dist/src/messaging/inbound.js";

weixinPlugin.gateway.logoutAccount = createAccountLogout({ stateDir: resolveStateDir, clearMemory: clearContextTokensForAccount });

const webLoginMethods = ["web.login.start", "web.login.wait"];

// OpenClaw 2026.7 discovers the QR-login provider from this declaration.
// Tencent Weixin 2.4.6 implements loginWithQrStart/loginWithQrWait but omits
// gatewayMethods, so the host otherwise reports "web login provider is not
// available" even though the channel itself loaded successfully.
weixinPlugin.gatewayMethods = Array.from(
  new Set([...(weixinPlugin.gatewayMethods ?? []), ...webLoginMethods]),
);

export default {
  ...tencentWeixinPlugin,
  register(api) {
    tencentWeixinPlugin.register(api);
    const engine = createWebLogin({
      ...createWechatTransport({ routeTag: loadConfigRouteTag }),
      saveAccount({ classId, accountId, token, baseUrl, userId }) {
        const id = normalizeAccountId(accountId);
        const config = loadConfig();
        const classes = config.plugins?.entries?.classclaw?.config?.agentClasses || {};
        if (!Object.values(classes).includes(classId) || (config.bindings || []).some((row) =>
          row.match?.channel === "openclaw-weixin" && row.match.accountId === id && classes[row.agentId] !== classId)) {
          throw new LoginError("WECHAT_ACCOUNT_CONFLICT", "该微信账号已用于其他绑定，不能覆盖其消息路由。", 409);
        }
        try {
          saveWeixinAccount(id, { token, baseUrl, ...(typeof userId === "string" ? { userId } : {}) });
          registerWeixinAccountId(id);
        } catch { throw new LoginError("WECHAT_CREDENTIAL_SAVE_FAILED", "微信凭据保存失败，尚未完成绑定。", 503); }
        // ClassClaw patches the route and channelConfigUpdatedAt together to
        // reload the channel. A bindings-only patch does not start this account.
        // Do not race that write with another config rewrite or delete accounts.
        return id;
      },
    });
    api.registerHttpRoute({ path: "/api/v1/classclaw/wechat-login", auth: "gateway", match: "exact",
      gatewayRuntimeScopeSurface: "trusted-operator", handler: webLoginHandler(engine, loadConfig) });
  },
};
