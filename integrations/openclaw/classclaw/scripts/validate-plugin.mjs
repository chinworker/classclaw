import { access, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(import.meta.dirname, "..");
const manifest = JSON.parse(await readFile(resolve(root, "openclaw.plugin.json"), "utf8"));
const entry = (await import(pathToFileURL(resolve(root, "dist/index.js")).href)).default;
const tools = [];
const hooks = [];
const routes = [];

if (!entry || typeof entry.register !== "function") throw new Error("Plugin entry does not export a register function");
entry.register({
  pluginConfig: { baseUrl: "http://127.0.0.1:8000" },
  registerTool(tool, options) {
    const resolved = typeof tool === "function" ? tool({ agentId: "class-agent", sessionKey: "session-1" }) : tool;
    const rows = Array.isArray(resolved) ? resolved : [resolved];
    for (const row of rows) tools.push({ name: row.name, optional: options?.optional === true });
  },
  on(name) { hooks.push(name); },
  registerHttpRoute(route) { routes.push(route); },
});

const declared = [...(manifest.contracts?.tools ?? [])].sort();
const registered = tools.map((item) => item.name).sort();
if (JSON.stringify(declared) !== JSON.stringify(registered)) {
  throw new Error(`Tool contract mismatch: declared=${declared.join(",")} registered=${registered.join(",")}`);
}
if (!tools.some((item) => item.name === "classclaw_commit_write" && item.optional)) {
  throw new Error("classclaw_commit_write must be registered as an optional tool");
}
if (!tools.some((item) => item.name === "classclaw_commit_writes" && item.optional)) {
  throw new Error("classclaw_commit_writes must be registered as an optional tool");
}
if (JSON.stringify(hooks.sort()) !== JSON.stringify(["after_tool_call", "before_tool_call"])) throw new Error("Class scope and turn guard hooks are required");
if (routes.length !== 1 || routes[0].auth !== "gateway" || routes[0].path !== "/api/v1/classclaw/web-session-thinking") {
  throw new Error("Only the authenticated, fixed web-session thinking route is allowed");
}
if (!manifest.contracts?.gatewayMethodDispatch?.includes("authenticated-request")) throw new Error("Authenticated dispatch contract is required");
for (const skill of manifest.skills ?? []) await access(resolve(root, skill, "SKILL.md"));

console.log(`ClassClaw mixed plugin is valid: ${tools.length} tools, ${hooks.length} class-scope hook, ${(manifest.skills ?? []).length} skill.`);
