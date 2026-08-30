# OpenClaw Weixin compatibility wrapper

This package delegates all behavior to the official
`@tencent-weixin/openclaw-weixin@2.4.6` plugin. It only declares
`web.login.start` and `web.login.wait` in the channel's `gatewayMethods`, as
required by OpenClaw 2026.7 provider discovery.

Install it over the upstream plugin with:

```bash
npm install
openclaw plugins install --force --link .
openclaw gateway restart
```

Remove this wrapper after Tencent publishes a release that declares the same
gateway methods itself.
