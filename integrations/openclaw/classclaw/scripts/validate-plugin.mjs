import { access, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";

const root = resolve(import.meta.dirname, "..");
const manifestPath = resolve(root, "openclaw.plugin.json");
const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
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

const registered = tools.map((item) => item.name);
const toolMetadata = Object.fromEntries(tools.filter((item) => item.optional).map(({ name }) => [name, { optional: true }]));
if (new Set(registered).size !== registered.length) throw new Error("Duplicate tool registration");
if (!tools.some((item) => item.name === "classclaw_commit_write" && item.optional)) {
  throw new Error("classclaw_commit_write must be registered as an optional tool");
}
if (!tools.some((item) => item.name === "classclaw_commit_writes" && item.optional)) {
  throw new Error("classclaw_commit_writes must be registered as an optional tool");
}
if (JSON.stringify(hooks.sort()) !== JSON.stringify(["after_tool_call", "before_prompt_build", "before_tool_call"])) throw new Error("Class scope, memory and turn guard hooks are required");
if (routes.length !== 2 || routes.some((route) => route.auth !== "gateway")
  || JSON.stringify(routes.map((route) => route.path).sort()) !== JSON.stringify([
    "/api/v1/classclaw/web-chat-reasoning", "/api/v1/classclaw/web-session-thinking",
  ])) {
  throw new Error("Only the authenticated session-thinking and read-only reasoning routes are allowed");
}
if (!manifest.contracts?.gatewayMethodDispatch?.includes("authenticated-request")) throw new Error("Authenticated dispatch contract is required");
for (const skill of manifest.skills ?? []) await access(resolve(root, skill, "SKILL.md"));

if (process.argv.includes("--write-tools")) {
  manifest.contracts.tools = registered;
  manifest.toolMetadata = toolMetadata;
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
} else if (!isDeepStrictEqual(manifest.contracts.tools, registered) || !isDeepStrictEqual(manifest.toolMetadata, toolMetadata)) {
  throw new Error("Manifest tools differ from actual registration; run npm run plugin:sync-tools and commit the manifest.");
}

console.log(`ClassClaw mixed plugin is valid: ${tools.length} tools, ${hooks.length} hooks, ${(manifest.skills ?? []).length} skill.`);
