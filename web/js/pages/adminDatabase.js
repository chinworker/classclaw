// 管理员 · 数据库调试（只读）：表清单、行数、分页浏览；密码哈希与会话令牌由后端脱敏。

import { el, clear, toast, copyText } from "../util.js";
import { adminView } from "../adminView.js";
import { appConfig } from "../config.js";
import { pageHeader, errorPanel, skeleton, jsonDetails, pagination } from "../components.js";

const PAGE_SIZE = Number(appConfig.web.database_page_size || 50);

let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }

export async function render(mount, ctx = {}) {
  dispose(); const view = adminView(); activeView = view;
  clear(mount);
  const api = async (path, options = {}) => {
    const result = await view.api(path, options);
    if (options.method && options.method !== "GET") ctx.onChanged?.();
    return result;
  };

  const listHost = el("div", { class: "card" });
  const tableHost = el("div", { class: "card" });
  mount.append(
    pageHeader("数据库调试", "只读视图：不提供 SQL 输入、单元格编辑或删除。password_hash 与 token_hash 已由后端脱敏。"),
    listHost, tableHost);

  let current = { table: null, offset: 0 };

  async function loadOverview() {
    clear(listHost);
    listHost.append(skeleton(4));
    let data;
    try {
      data = await api("/admin/database/overview");
    } catch (error) { if (!view.active) return;
      clear(listHost);
      listHost.append(errorPanel(error, { onRetry: loadOverview }));
      return;
    }
    clear(listHost);
    listHost.append(el("h3", {}, `数据表（${data.tables.length}）`));
    listHost.append(el("div", { class: "chips" }, data.tables.map((t) =>
      el("button", { class: "chip", type: "button", onclick: () => loadTable(t.name, 0) }, `${t.database === "usage" ? "用量库" : "业务库"} · ${t.name} · ${t.row_count ?? "不可用"}`))));
  }

  async function loadTable(name, offset) {
    current = { table: name, offset };
    clear(tableHost);
    tableHost.append(skeleton(5));
    let data;
    try {
      data = await api(`/admin/database/tables/${encodeURIComponent(name)}?offset=${offset}&limit=${PAGE_SIZE}`);
      if (current.table !== name || current.offset !== offset) return;
    } catch (error) { if (!view.active) return;
      clear(tableHost);
      tableHost.append(errorPanel(error, { onRetry: () => loadTable(name, offset) }));
      return;
    }
    clear(tableHost);
    tableHost.append(el("h3", {}, `${data.database === "usage" ? "用量库" : "业务库"} · ${data.table} · 共 ${data.total} 行`));
    if (!data.items.length) { tableHost.append(el("p", { class: "muted" }, "该表暂无数据")); return; }
    const wrap = el("div", { class: "table-wrap" });
    const table = el("table", { class: "data-table" });
    table.append(el("thead", {}, el("tr", {},
      el("th", {}, "#"),
      data.columns.slice(0, 6).map((c) => el("th", {}, c)),
      el("th", {}, "完整行"))));
    const tbody = el("tbody");
    data.items.forEach((row, index) => {
      const tr = el("tr");
      tr.append(el("td", {}, String(offset + index + 1)));
      for (const col of data.columns.slice(0, 6)) {
        const value = row[col];
        const text = value === null || value === undefined ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
        const isId = col === "id" || col.endsWith("_id");
        tr.append(el("td", {}, isId && value
          ? el("button", { class: "tag tag-id", type: "button", title: "点击复制", onclick: () => copyText(String(value)) }, `${String(value).slice(0, 8)}…`)
          : el("span", { style: { fontSize: "12px" } }, text.length > 40 ? `${text.slice(0, 40)}…` : text)));
      }
      tr.append(el("td", {}, jsonDetails(row, "展开")));
      tbody.append(tr);
    });
    table.append(tbody);
    wrap.append(table);
    tableHost.append(wrap, pagination({
      total: data.total, page: Math.floor(offset / PAGE_SIZE) + 1, pageSize: PAGE_SIZE,
      onPage: (page) => loadTable(name, (page - 1) * PAGE_SIZE),
    }));
  }

  await loadOverview();
}
