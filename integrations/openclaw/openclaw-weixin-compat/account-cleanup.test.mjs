import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createAccountLogout } from "./account-cleanup.js";

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "classclaw-account-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const cleared = [];
  const logout = createAccountLogout({ stateDir: () => root, clearMemory: (id) => cleared.push(id) });
  return { root, cleared, logout };
}

function seed(root, accountId) {
  const files = [
    path.join(root, "openclaw-weixin/accounts", `${accountId}.json`),
    path.join(root, "openclaw-weixin/accounts", `${accountId}.sync.json`),
    path.join(root, "openclaw-weixin/accounts", `${accountId}.context-tokens.json`),
    path.join(root, "credentials", `openclaw-weixin-${accountId}-allowFrom.json`),
  ];
  for (const file of files) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "{}");
  }
  const index = path.join(root, "openclaw-weixin/accounts.json");
  const registered = fs.existsSync(index) ? JSON.parse(fs.readFileSync(index, "utf8")) : [];
  fs.writeFileSync(index, JSON.stringify([...registered, accountId], null, 2));
  return files;
}

const cfg = (bindings) => ({ bindings });

test("logout clears credentials, sync state and memory for bot and raw ids only", async (t) => {
  const f = fixture(t);
  const files = seed(f.root, "abc-im-bot");
  const rawSync = path.join(f.root, "openclaw-weixin/accounts/abc@im.bot.sync.json");
  const keep = path.join(f.root, "openclaw-weixin/accounts/keep-im-bot.json");
  fs.writeFileSync(rawSync, "{}");
  fs.writeFileSync(keep, "{}");
  fs.writeFileSync(path.join(f.root, "openclaw-weixin/accounts.json"), JSON.stringify(["abc-im-bot", "abc@im.bot", "keep-im-bot"]));

  const result = await f.logout({
    accountId: "abc-im-bot",
    cfg: cfg([{ agentId: "agent-a", match: { channel: "openclaw-weixin", accountId: "abc-im-bot" } }]),
  });

  assert.deepEqual(result, { cleared: true, loggedOut: true });
  assert.equal(files.some((file) => fs.existsSync(file)), false);
  assert.equal(fs.existsSync(rawSync), false);
  assert.equal(fs.existsSync(keep), true);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.root, "openclaw-weixin/accounts.json"), "utf8")), ["keep-im-bot"]);
  assert.deepEqual([...f.cleared].sort(), ["abc-im-bot", "abc@im.bot"]);
});

test("logout refuses an account shared by another agent", async (t) => {
  const f = fixture(t);
  seed(f.root, "shared-im-bot");
  await assert.rejects(f.logout({
    accountId: "shared-im-bot",
    cfg: cfg([
      { agentId: "agent-a", match: { channel: "openclaw-weixin", accountId: "shared-im-bot" } },
      { agentId: "agent-b", match: { channel: "openclaw-weixin", accountId: "shared-im-bot" } },
    ]),
  }), /shared by other agents/);
});

test("logout refuses a legacy shared credentials file", async (t) => {
  const f = fixture(t);
  seed(f.root, "legacy-im-bot");
  const legacy = path.join(f.root, "credentials/openclaw-weixin/credentials.json");
  fs.mkdirSync(path.dirname(legacy), { recursive: true });
  fs.writeFileSync(legacy, "{}");
  await assert.rejects(f.logout({ accountId: "legacy-im-bot", cfg: cfg([]) }), /manual ownership review/);
});

test("logout validates the account id and tolerates a missing index", async (t) => {
  const f = fixture(t);
  await assert.rejects(f.logout({ accountId: "../escape", cfg: cfg([]) }), /Invalid account ID/);

  const accountFile = path.join(f.root, "openclaw-weixin/accounts", "lonely-im-wechat.json");
  fs.mkdirSync(path.dirname(accountFile), { recursive: true });
  fs.writeFileSync(accountFile, "{}");
  const result = await f.logout({ accountId: "lonely-im-wechat", cfg: cfg([]) });
  assert.deepEqual(result, { cleared: true, loggedOut: true });
  assert.equal(fs.existsSync(accountFile), false);
});

test("raw account aliases cannot bypass shared-account protection", async (t) => {
  const f = fixture(t);
  const files = seed(f.root, "abc-im-bot");
  await assert.rejects(f.logout({ accountId: "abc-im-bot", cfg: cfg([
    { agentId: "a", match: { channel: "openclaw-weixin", accountId: "abc-im-bot" } },
    { agentId: "b", match: { channel: "openclaw-weixin", accountId: "abc@im.bot" } },
  ]) }), /shared by other agents/);
  assert.ok(files.every((file) => fs.existsSync(file)));
});
