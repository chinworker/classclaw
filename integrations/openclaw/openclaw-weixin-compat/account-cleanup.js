import fs from "node:fs";
import path from "node:path";

function safe(root, target) {
  const base = path.resolve(root), value = path.resolve(target);
  if (!value.startsWith(base + path.sep)) throw new Error("Unsafe account path");
  // Only the configured root's descendants are checked: platform temp/state
  // roots themselves may traverse symlinks (for example /var on macOS).
  for (let part = value; part !== base && part !== path.dirname(part); part = path.dirname(part)) {
    try { if (fs.lstatSync(part).isSymbolicLink()) throw new Error("Unsafe account symlink"); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  return value;
}

// Called by channels.logout after the Gateway has stopped the account monitor.
// No provider best-effort unlink: failures must reach the durable deletion job.
export function createAccountLogout({ stateDir, clearMemory }) {
  return async ({ accountId, cfg }) => {
    if (!/^[a-z0-9][a-z0-9_-]{0,199}$/.test(accountId)) throw new Error("Invalid account ID");
    const canonical = (value) => typeof value === "string" ? value.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "-") : "";
    const routes = (cfg.bindings || []).filter((r) => r.match?.channel === "openclaw-weixin" && canonical(r.match.accountId) === accountId);
    if (new Set(routes.map((r) => r.agentId)).size > 1) throw new Error("Account is shared by other agents");
    const root = stateDir();
    const legacy = safe(root, path.join(root, "credentials/openclaw-weixin/credentials.json"));
    if (fs.existsSync(legacy)) throw new Error("Legacy shared credentials require manual ownership review");
    const raw = accountId.endsWith("-im-bot") ? accountId.slice(0, -7) + "@im.bot"
      : accountId.endsWith("-im-wechat") ? accountId.slice(0, -10) + "@im.wechat" : null;
    const ids = [accountId, ...(raw ? [raw] : [])];
    const index = safe(root, path.join(root, "openclaw-weixin/accounts.json"));
    const files = ids.flatMap((id) => [
      ...[".json", ".sync.json", ".context-tokens.json"].map((suffix) => path.join(root, "openclaw-weixin/accounts", id + suffix)),
      path.join(root, "credentials", `openclaw-weixin-${id}-allowFrom.json`),
    ]).map((file) => safe(root, file));
    const registered = fs.existsSync(index) ? JSON.parse(fs.readFileSync(index, "utf8")) : [];
    if (!Array.isArray(registered) || registered.some((id) => typeof id !== "string")) throw new Error("Invalid account index");
    for (const id of ids) clearMemory(id);
    for (const file of files) {
      try { fs.unlinkSync(file); } catch (error) { if (error.code !== "ENOENT") throw error; }
    }
    const remaining = registered.filter((id) => !ids.includes(id));
    if (remaining.length !== registered.length) fs.writeFileSync(index, JSON.stringify(remaining, null, 2), { mode: 0o600 });
    if (files.some((file) => fs.existsSync(file))) throw new Error("Account cleanup incomplete");
    if (fs.existsSync(index) && JSON.parse(fs.readFileSync(index, "utf8")).some((id) => ids.includes(id))) throw new Error("Account still registered");
    return { cleared: true, loggedOut: true };
  };
}
