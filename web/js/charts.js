// Small, accessible SVG charts. Values and labels are always inserted as text.
import { el } from "./util.js";
import { emptyState } from "./components.js";

function svg(tag, attributes = {}, text = null) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== null) node.textContent = String(text);
  return node;
}
const num = (value) => Math.max(0, Number(value) || 0);
const label = (value) => num(value).toLocaleString("zh-CN", { maximumFractionDigits: 0 });

export function trendChart(series, { xKey = "date", yKeys = ["requests", "total_tokens"], labels = ["请求", "Token"], compact = false } = {}) {
  if (!series?.length) return emptyState("暂无趋势数据");
  const root = el("figure", { class: `usage-trend${compact ? " compact" : ""}` });
  const chart = svg("svg", { viewBox: "0 0 760 260", role: "img", "aria-label": `${labels.join("与")}每日趋势` });
  const caption = el("figcaption", { class: "chart-tooltip", "aria-live": "polite" }, "悬停或聚焦数据点查看当日数值");
  const left = 80; const right = 680; const top = 28; const bottom = 218;
  const maxima = yKeys.map((key) => Math.max(1, ...series.map((row) => num(row[key]))));
  const x = (index) => series.length === 1 ? (left + right) / 2 : left + index * (right - left) / (series.length - 1);
  const y = (value, index) => bottom - num(value) / maxima[index] * (bottom - top);
  for (let index = 0; index <= 4; index++) {
    const ratio = index / 4; const position = bottom - ratio * (bottom - top);
    chart.append(svg("line", { x1: left, x2: right, y1: position, y2: position, class: "chart-gridline" }),
      svg("text", { x: left - 10, y: position + 4, "text-anchor": "end", class: "chart-axis" }, label(maxima[0] * ratio)));
    if (yKeys.length > 1) chart.append(svg("text", { x: right + 10, y: position + 4, class: "chart-axis" }, label(maxima[1] * ratio)));
  }
  for (const index of [...new Set([0, Math.floor((series.length - 1) / 2), series.length - 1])]) {
    chart.append(svg("text", { x: x(index), y: 245, "text-anchor": "middle", class: "chart-axis" }, String(series[index][xKey]).slice(5)));
  }
  yKeys.forEach((key, line) => {
    chart.append(svg("text", { x: line ? right : left, y: 16, "text-anchor": line ? "end" : "start", class: `chart-series-label series-${line}` }, labels[line] || key));
    chart.append(svg("polyline", { points: series.map((row, index) => `${x(index)},${y(row[key], line)}`).join(" "), class: `chart-series series-${line}`, fill: "none" }));
    series.forEach((row, index) => {
      const description = `${row[xKey]} · ${yKeys.map((field, part) => `${labels[part] || field} ${label(row[field])}`).join(" · ")}`;
      const dot = svg("circle", { cx: x(index), cy: y(row[key], line), r: 4, class: `chart-point series-${line}`, tabindex: "0", "aria-label": description });
      dot.append(svg("title", {}, description));
      for (const event of ["pointerenter", "focus"]) dot.addEventListener(event, () => { caption.textContent = description; });
      chart.append(dot);
    });
  });
  root.append(chart, caption); return root;
}

export function barList(items, { labelKey = "name", valueKey = "tokens" } = {}) {
  if (!items?.length) return emptyState("暂无分布数据");
  const sorted = [...items].sort((a, b) => num(b[valueKey]) - num(a[valueKey]));
  const total = sorted.reduce((sum, row) => sum + num(row[valueKey]), 0);
  const root = el("div", { class: "usage-bars" });
  for (const row of sorted) {
    const percent = total ? num(row[valueKey]) / total * 100 : 0;
    const chart = svg("svg", { viewBox: "0 0 400 12", role: "img", "aria-label": `${row[labelKey]} 占比 ${percent.toFixed(1)}%` });
    chart.append(svg("rect", { width: 400, height: 12, rx: 6, class: "chart-bar-bg" }),
      svg("rect", { width: percent * 4, height: 12, rx: 6, class: "chart-bar-fill" }));
    root.append(el("div", { class: "usage-bar" }, el("div", { class: "row-gap" }, el("b", {}, row[labelKey]), el("span", { class: "spacer" }), el("span", {}, `${percent.toFixed(1)}%`)), chart,
      el("small", { class: "muted" }, `${label(row.requests)} 次请求 · ${label(row.tokens)} Token`)));
  }
  return root;
}
