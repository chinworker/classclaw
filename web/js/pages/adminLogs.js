import { pageHeader } from "../components.js";
import { logViewer } from "../logViewer.js";
import { replaceRouteQuery } from "../router.js";
let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }
export async function render(mount, ctx = {}) {
  dispose();
  const query = ctx.query || {};
  const viewer = logViewer({ source: query.source, level: query.level, requestId: query.request_id, query: query.q, range: query.range,
    onFilters: (filters) => replaceRouteQuery({ ...query, tab: "logs", ...filters }),
  });
  activeView = viewer;
  mount.append(pageHeader("日志 / Debug", "筛选最近日志、关联请求链路，或开启实时刷新。"), viewer.el);
  await viewer.load();
}
