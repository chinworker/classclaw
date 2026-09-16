# OpenClaw Weixin compatibility wrapper

Version `2.4.6-classclaw.2` delegates message transport, channel registration and
CLI login to `@tencent-weixin/openclaw-weixin@2.4.6`. It preserves the web-login
provider discovery declarations and adds a **ClassClaw-only browser login**.

The pinned upstream CLI flow deletes its session when a wait call times out,
prints replacement QR codes to stdout, and reads phone verification codes from
stdin. ClassClaw's web flow must not delegate to that implementation.

- `web-login.js`: bounded, per-class in-memory login state. Short poll timeouts
  keep the attempt alive; provider-expired QR codes are returned immediately
  after refresh. Parallel waits share one provider call. Regeneration aborts
  the old attempt and identity-checks every late result before saving anything.
- `web-transport.js`: pinned iLink QR protocol, HTTPS Weixin host restriction,
  no redirects, whole-body deadlines, 1 MiB response limit, no URL/body logs.
  Other accounts' saved tokens are not submitted when generating a QR.
- `web-route.js`: `POST /api/v1/classclaw/wechat-login`, registered with Gateway
  authentication and exact path matching. Only currently mapped ClassClaw
  classes can use the fixed `start`, `wait`, `verify` operations. No arbitrary
  RPC, URL, filesystem path, or client-selected account destination is allowed.

Start accepts class ID, force, timeout and TTL; wait additionally requires the
returned login UUID; verification also requires the current challenge UUID and
1–12 ASCII digits. The UI preserves leading zeros, clears input on submission,
and never stores codes in browser storage. Raw QR data and codes are never
written to ClassClaw's DB or logs. Successful credentials are saved using the
upstream account store, then ClassClaw commits its guarded class route. A save
failure cannot report success, and other accounts are never auto-deleted.

The backend writes the route and upstream `channels.openclaw-weixin.channelConfigUpdatedAt`
reload marker in one `config.patch`. A route-only change does not start a newly
saved account; it otherwise waits for the Gateway health monitor. Keeping both
changes in one patch also avoids receiving messages before their class route
exists and preserves other accounts' settings.

Default login TTL is 300 seconds (bounded 30–1800), at most 100 retained class
attempts, at most 3 QR refreshes and 5 verification submissions per attempt.
Gateway restart loses pending attempts; regenerate the QR. Short wait requests
do not extend TTL. A response exposes only state, opaque IDs, QR content and a
normalized account ID after success—never account credentials.

Install it over the upstream plugin with:

```bash
npm install --omit=peer --ignore-scripts
npm test
openclaw plugins install --force --link .
openclaw gateway restart
```

If the plugin is already linked to this directory, no reinstall is necessary;
restart Gateway to load the source. Then restart ClassClaw and hard-refresh the
browser. Wait for active chats before restarting. No TypeScript build or new
dependency is needed. Loading in Gateway uses the host's OpenClaw SDK (the
optional peer is deliberately omitted from this package's local install).

Tests stub Weixin HTTP and account persistence; they do not start a real login,
save credentials, change OpenClaw configuration, or require network access.
Do not remove the wrapper merely because upstream adds discovery declarations:
verify short-poll lifetime, QR refresh, stale-attempt safety and browser code
entry support before migrating the ClassClaw private endpoint.


Deletion cleanup requires version `2.4.6-classclaw.4`: `cancel` returns the
normalized `accountIds` committed by that class's login attempts, including a
successful scan whose ClassClaw route has not yet been saved. The backend
journals and checks those accounts before logout. Raw and normalized account
aliases share the same ownership checks. Upgrade/restart the compatibility
plugin before the backend; older cancel responses stop deletion with a
retryable plugin-update error.
