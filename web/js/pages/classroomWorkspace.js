import { clear, el } from "../util.js";
import * as monitor from "./classroom.js?v=20261107";
import * as broadcast from "./broadcast.js?v=20261107";

let activePage;

export function dispose() {
  activePage?.dispose();
  activePage = null;
}

export function render(mount, ctx, helpers) {
  dispose();
  const showBroadcast = ctx.query.tab === "broadcast";
  const panel = el("div");
  const link = (href, text, current) => el("a", {
    href, class: current ? "primary" : "secondary", "aria-current": current ? "page" : null,
  }, text);
  clear(mount).append(
    el("nav", { class: "row-gap", "aria-label": "监控与播报" },
      link("#/classroom", "实时监控", !showBroadcast),
      link("#/classroom?tab=broadcast", "点名播报", showBroadcast)),
    panel,
  );
  activePage = showBroadcast ? broadcast : monitor;
  return activePage.render(panel, ctx, helpers);
}
