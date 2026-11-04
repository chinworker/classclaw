import { el, clear, toast } from "./util.js";
import { compareStudents } from "./studentOrder.js";

const SEAT_DRAG_TYPE = "application/x-classclaw-seat";

export function seatMapEditor({ students = [], rows = 5, cols = 6, layout = null }) {
  students = [...students].sort(compareStudents);
  const byKey = new Map(students.map((s) => [s.id, s]));
  const initial = normalizeLayout(layout);
  const model = {
    rows: initial.length || rows,
    cols: initial[0]?.length || cols,
    matrix: initial.length ? initial : Array.from({ length: rows }, () => Array(cols).fill(null)),
  };
  let selectedPoolKey = null;   // 未排座池中选中的学生
  let selectedSeat = null;      // 选中的座位 {r,c}
  const poolBox = el("div", { class: "seat-pool" });
  const gridBox = el("div", { class: "seat-grid-box" });
  const sizeInputs = el("div", { class: "seat-toolbar" });

  function normalizeLayout(source) {
    return (source ?? []).map((row) => row.map((key) => byKey.has(key) ? key : null));
  }

  function validate() {
    const errors = [];
    if (model.matrix.length !== model.rows || model.matrix.some((row) => row.length !== model.cols)) {
      errors.push(`矩阵必须是严格的 ${model.rows} 行 × ${model.cols} 列`);
    }
    const seen = new Map();
    model.matrix.forEach((row, r) => row.forEach((key, c) => {
      if (!key) return;
      if (!byKey.has(key)) errors.push(`第 ${r + 1} 排第 ${c + 1} 座引用了不存在的学生`);
      if (seen.has(key)) errors.push(`学生 ${byKey.get(key)?.name || key} 出现多次`);
      seen.set(key, true);
    }));
    return errors;
  }

  function setSize(nextRows, nextCols) {
    nextRows = Math.min(30, Math.max(1, nextRows));
    nextCols = Math.min(30, Math.max(1, nextCols));
    const next = Array.from({ length: nextRows }, (_, r) =>
      Array.from({ length: nextCols }, (_, c) => model.matrix[r]?.[c] ?? null));
    // 收集被裁掉的学生提示（缩小行列时）
    if (nextRows < model.rows || nextCols < model.cols) {
      const dropped = [];
      model.matrix.forEach((row, r) => row.forEach((key, c) => {
        if (key && (r >= nextRows || c >= nextCols)) dropped.push(byKey.get(key)?.name || key);
      }));
      if (dropped.length) toast(`缩小行列后 ${dropped.length} 名学生回到未排座：${dropped.join("、")}`, "info");
    }
    model.rows = nextRows; model.cols = nextCols; model.matrix = next;
    selectedSeat = null;
    renderAll();
  }

  function seatCard(key, r, c) {
    const student = key ? byKey.get(key) : null;
    const isSelected = selectedSeat && selectedSeat.r === r && selectedSeat.c === c;
    const card = el("button", {
      type: "button",
      class: `seat${student ? "" : " seat-empty"}${isSelected ? " seat-selected" : ""}`,
      draggable: student ? "true" : null,
      "aria-label": student ? `第${r + 1}排第${c + 1}座 ${student.name}` : `第${r + 1}排第${c + 1}座 空座`,
      dataset: { r: String(r), c: String(c) },
    },
      el("span", { class: "seat-no" }, String(r * model.cols + c + 1).padStart(2, "0")),
      student ? el("span", { class: "seat-name" }, student.name) : el("span", { class: "seat-name muted" }, "空座"),
      student && student.student_no ? el("span", { class: "seat-sno muted" }, `学号 ${student.student_no}`) : null);
    card.addEventListener("click", () => onSeatClick(r, c));
    card.addEventListener("dragstart", (e) => { e.dataTransfer.setData(SEAT_DRAG_TYPE, `${r},${c}`); card.classList.add("dragging"); });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("dragover", (e) => { e.preventDefault(); card.classList.add("drag-over"); });
    card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
    card.addEventListener("drop", (e) => {
      e.preventDefault(); card.classList.remove("drag-over");
      const source = e.dataTransfer.getData(SEAT_DRAG_TYPE);
      if (!/^\d+,\d+$/.test(source)) return;
      const [sr, sc] = source.split(",").map(Number);
      if (model.matrix[sr]?.[sc]) swapSeats(sr, sc, r, c);
    });
    return card;
  }

  function onSeatClick(r, c) {
    const key = model.matrix[r][c];
    if (selectedPoolKey && !key) {
      // 放置池中学生到空座
      model.matrix[r][c] = selectedPoolKey;
      selectedPoolKey = null;
      renderAll();
      return;
    }
    if (selectedSeat && (selectedSeat.r !== r || selectedSeat.c !== c)) {
      swapSeats(selectedSeat.r, selectedSeat.c, r, c);
      return;
    }
    if (key) {
      selectedSeat = selectedSeat && selectedSeat.r === r && selectedSeat.c === c ? null : { r, c };
      renderAll();
    }
  }

  function swapSeats(r1, c1, r2, c2) {
    if (r1 === r2 && c1 === c2) return;
    const a = model.matrix[r1][c1];
    model.matrix[r1][c1] = model.matrix[r2][c2];
    model.matrix[r2][c2] = a;
    selectedSeat = null;
    renderAll();
  }

  function removeFromSeat() {
    if (!selectedSeat) return;
    model.matrix[selectedSeat.r][selectedSeat.c] = null;
    selectedSeat = null;
    renderAll();
  }

  function renderPool() {
    clear(poolBox);
    const seated = new Set(model.matrix.flat().filter(Boolean));
    const rest = students.filter((s) => !seated.has(s.id));
    poolBox.append(el("div", { class: "pool-head" },
      el("b", {}, `未排座学生（${rest.length}）`),
      rest.length ? el("span", { class: "muted" }, "点击学生后点击空座即可入座") : null));
    if (!rest.length) { poolBox.append(el("p", { class: "muted" }, "所有学生均已排座")); return; }
    poolBox.append(el("div", { class: "pool-list" }, rest.map((s) => {
      const key = s.id;
      const chip = el("button", {
        type: "button",
        class: `pool-chip${selectedPoolKey === key ? " chip-selected" : ""}`,
        onclick: () => { selectedPoolKey = selectedPoolKey === key ? null : key; selectedSeat = null; renderAll(); },
      }, `${s.name}`, s.student_no ? el("small", { class: "muted" }, ` ${s.student_no}`) : null);
      return chip;
    })));
  }

  function renderGrid() {
    clear(gridBox);
    const grid = el("div", { class: "seat-grid", role: "grid", style: { gridTemplateColumns: `auto repeat(${model.cols}, minmax(84px, 1fr))` } });
    model.matrix.forEach((row, r) => {
      row.forEach((key, c) => {
        if (c === 0) grid.append(el("div", { class: "row-label", role: "rowheader" }, `第${r + 1}排`));
        grid.append(seatCard(key, r, c));
      });
    });
    gridBox.append(grid);
  }

  function renderToolbar() {
    clear(sizeInputs);
    const rowsInput = el("input", { type: "number", min: "1", max: "30", value: String(model.rows), "aria-label": "行数" });
    const colsInput = el("input", { type: "number", min: "1", max: "30", value: String(model.cols), "aria-label": "列数" });
    const apply = el("button", { class: "secondary", type: "button", onclick: () => setSize(Number(rowsInput.value), Number(colsInput.value)) }, "应用行列");
    const removeBtn = el("button", { class: "secondary", type: "button", disabled: !selectedSeat, onclick: removeFromSeat }, "移出座位");
    const undo = el("button", {
      class: "secondary", type: "button",
      onclick: () => {
        model.matrix = normalizeLayout(layout);
        while (model.matrix.length < model.rows) model.matrix.push(Array(model.cols).fill(null));
        model.matrix = model.matrix.slice(0, model.rows).map((row) => { const next = row.slice(0, model.cols); while (next.length < model.cols) next.push(null); return next; });
        selectedSeat = null; selectedPoolKey = null; renderAll();
        toast("已撤销本次未保存的修改");
      },
    }, "撤销修改");
    sizeInputs.append(
      el("span", { class: "muted" }, "行"), rowsInput, el("span", { class: "muted" }, "列"), colsInput, apply,
      el("span", { class: "toolbar-sep" }), removeBtn, undo,
      selectedSeat ? el("span", { class: "tag tag-info" }, `已选第${selectedSeat.r + 1}排第${selectedSeat.c + 1}座，点另一座位可交换`) : null,
    );
  }

  function renderAll() {
    renderPool();
    renderGrid();
    renderToolbar();
  }

  const container = el("div", { class: "seatmap" },
    el("div", { class: "blackboard", "aria-hidden": "true" }, "黑 板"),
    el("div", { class: "podium", "aria-hidden": "true" }, "讲 台"),
    el("div", { class: "seat-scroll" }, gridBox), sizeInputs, poolBox,
  );
  renderAll();

  return {
    el: container,
    getLayout: () => ({ rows: model.rows, cols: model.cols, layout: model.matrix.map((row) => [...row]) }),
    validate,
  };
}
