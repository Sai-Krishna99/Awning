(() => {
  const config = JSON.parse(document.getElementById("review-config").textContent);
  const $ = (id) => document.getElementById(id);
  let dirty = false;

  const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
  const formatDollars = (cents) => (cents / 100).toFixed(2);

  const optionsToText = (options) =>
    options.map((o) => o.name + (o.price_delta_cents > 0 ? " +" + formatDollars(o.price_delta_cents) : "")).join(", ");

  // "Large +1.00, Oat milk +0.75" -> [{name, price_delta_cents}]; null when a part is malformed.
  const parseOptions = (text, known) => {
    const options = [];
    for (const part of (text || "").split(",").map((p) => p.trim()).filter(Boolean)) {
      const match = part.match(/^(.*?)\s*\+\s*\$?\s*(\d+(?:\.\d{1,2})?)$/);
      const name = (match ? match[1] : part).trim();
      if (!name || (!match && /[+$]/.test(part))) return null;
      const existing = known.find((o) => o.name === name);
      options.push({
        id: existing ? existing.id : "",
        name,
        price_delta_cents: match ? Math.round(parseFloat(match[2]) * 100) : 0,
      });
    }
    return options;
  };

  const toRow = (item) => ({
    key: newKey(),
    id: item.id,
    name: item.name,
    description: item.description,
    category: item.category,
    price: item.price_cents === null ? null : item.price_cents / 100,
    options_text: optionsToText(item.options),
    known_options: item.options,
    available: item.available,
    needs_review: item.needs_review,
    review_note: item.review_note,
    errors: {},
  });

  const allRows = () => { const rows = []; gridApi.forEachNode((node) => rows.push(node.data)); return rows; };

  const toItem = (row) => {
    const options = parseOptions(row.options_text, row.known_options);
    if (options === null) {
      row.errors.options = "Use the form: Large +1.00, Oat milk +0.75";
      return null;
    }
    return {
      id: row.id || "",
      name: (row.name || "").trim(),
      description: row.description || "",
      category: row.category || "",
      price_cents: row.price === null || row.price === undefined || row.price === "" ? null : Math.round(Number(row.price) * 100),
      options,
      available: row.available,
      needs_review: row.needs_review,
      review_note: row.review_note || "",
    };
  };

  const refreshState = () => {
    const rows = allRows();
    const flagged = rows.filter((r) => r.needs_review).length;
    const status = $("review-status");
    status.textContent = flagged
      ? `${flagged} ${flagged === 1 ? "item needs" : "items need"} your review`
      : rows.length ? "All items reviewed" : "No items yet. Add one to get started.";
    status.style.color = flagged ? "var(--terracotta-deep)" : "var(--green)";
    const blocked = flagged > 0 || rows.length === 0;
    $("publish-btn").disabled = blocked;
    $("publish-hint").textContent = flagged ? "Confirm the highlighted items first." : rows.length ? "" : "Add at least one item first.";
  };

  const clearErrors = () => {
    allRows().forEach((r) => { r.errors = {}; });
    $("grid-errors").classList.add("hidden");
    $("grid-errors").replaceChildren();
  };

  const columnFor = (field) => (field === "price_cents" ? "price" : field.startsWith("options") ? "options_text" : field);

  const showErrors = (errors) => {
    const rows = allRows();
    const box = $("grid-errors");
    const list = document.createElement("ul");
    list.className = "list-disc pl-5";
    for (const err of errors) {
      const column = columnFor(err.field);
      const row = rows[err.row];
      if (row) row.errors[column] = err.message;
      const item = document.createElement("li");
      item.textContent = `Row ${err.row + 1}${row && row.name ? ` (${row.name})` : ""}, ${column === "options_text" ? "options" : column}: ${err.message}`;
      list.append(item);
    }
    box.replaceChildren(list);
    box.classList.remove("hidden");
    gridApi.refreshCells({ force: true });
  };

  const message = (text, ok) => {
    const el = $("save-message");
    el.textContent = text;
    el.style.color = ok ? "var(--green)" : "#B91C1C";
  };

  const save = async () => {
    clearErrors();
    const rows = allRows();
    const items = rows.map(toItem);
    if (items.includes(null)) {
      const errors = [];
      rows.forEach((row, index) => { if (row.errors.options) errors.push({ row: index, field: "options", message: row.errors.options }); });
      showErrors(errors);
      message("Fix the highlighted cells, then save again.", false);
      return false;
    }
    let response;
    try {
      response = await fetch(`/api/m/${config.token}/items`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }),
      });
    } catch (e) {
      message("Couldn't reach the server. Check your connection and try again.", false);
      return false;
    }
    const body = await response.json().catch(() => ({}));
    if (response.status === 422 && body.errors) {
      showErrors(body.errors);
      message("Some cells need fixing before this can be saved.", false);
      return false;
    }
    if (!response.ok) {
      message((body.error && body.error.message) || "Couldn't save. Please try again.", false);
      return false;
    }
    body.items.forEach((saved, index) => {
      if (rows[index]) { rows[index].id = saved.id; rows[index].known_options = saved.options; }
    });
    dirty = false;
    message("Saved.", true);
    return true;
  };

  const isFlagged = (p) => !!(p.data && p.data.needs_review);
  const hasError = (field) => (p) => !!(p.data && p.data.errors && p.data.errors[field]);
  const cell = (field) => ({
    field,
    editable: true,
    cellClassRules: {
      "cell-flagged": isFlagged,
      "cell-missing": (p) => isFlagged(p) && field === "price" && (p.data.price === null || p.data.price === undefined),
      "cell-error": hasError(field),
    },
    tooltipValueGetter: (p) => (p.data.errors && p.data.errors[field]) || (isFlagged(p) ? p.data.review_note : undefined),
  });

  const confirmRenderer = (params) => {
    const wrap = document.createElement("span");
    if (!params.data.needs_review) {
      wrap.className = "ok-mark";
      wrap.textContent = "Reviewed";
      return wrap;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "confirm-btn";
    button.textContent = "Confirm";
    button.setAttribute("aria-label", `Confirm ${params.data.name || "this item"}`);
    button.addEventListener("click", () => {
      if (params.data.price === null || params.data.price === undefined || params.data.price === "") {
        params.data.errors.price = "Add a price before confirming.";
        message("Add a price before confirming.", false);
      } else {
        params.data.needs_review = false;
        dirty = true;
      }
      params.api.refreshCells({ rowNodes: [params.node], force: true });
      refreshState();
    });
    wrap.append(button);
    return wrap;
  };

  const wide = window.matchMedia("(min-width: 640px)").matches;
  const columnDefs = [
    { ...cell("name"), headerName: "Item", minWidth: 170, flex: 1.2 },
    {
      ...cell("price"), headerName: "Price ($)", minWidth: 100, width: 110, type: "rightAligned",
      cellEditor: "agNumberCellEditor", cellEditorParams: { min: 0, precision: 2 },
      valueFormatter: (p) => (p.value === null || p.value === undefined || p.value === "" ? "—" : Number(p.value).toFixed(2)),
    },
    { ...cell("options_text"), headerName: "Options (name +price)", minWidth: 230, flex: 1.5 },
    { ...cell("category"), headerName: "Category", minWidth: 120, flex: 0.8 },
    { ...cell("description"), headerName: "Description", minWidth: 200, flex: 1.5 },
    {
      field: "review_note", headerName: "Why check?", minWidth: 190, flex: 1, editable: false,
      tooltipField: "review_note",
      valueGetter: (p) => (p.data.needs_review ? p.data.review_note || "Please check this row." : ""),
    },
    { headerName: "Review", colId: "confirm", minWidth: 120, width: 120, editable: false, sortable: false, pinned: wide ? "right" : null, cellRenderer: confirmRenderer },
  ];

  if (!wide) columnDefs.splice(1, 0, columnDefs.pop());  // keep Confirm visible without scrolling on phones

  const theme = agGrid.themeQuartz.withParams({
    accentColor: "#1F4D3A", backgroundColor: "#FFFFFF", foregroundColor: "#26332C",
    borderColor: "#E6DCCB", headerBackgroundColor: "#F3EBDD", headerTextColor: "#1F4D3A",
    fontFamily: "Inter, system-ui, sans-serif", headerFontWeight: 600, rowHeight: 52, headerHeight: 46,
  });

  const gridApi = agGrid.createGrid($("grid"), {
    theme,
    columnDefs,
    rowData: config.items.map(toRow),
    getRowId: (p) => p.data.key,
    rowSelection: { mode: "multiRow", headerCheckbox: false },
    singleClickEdit: true,
    stopEditingWhenCellsLoseFocus: true,
    tooltipShowDelay: 300,
    onSelectionChanged: (e) => { $("delete-rows").disabled = e.api.getSelectedRows().length === 0; },
    onCellValueChanged: (e) => {
      dirty = true;
      if (e.colDef.field === "price") {
        e.data.price = e.newValue === "" || e.newValue === undefined ? null : e.newValue;
      }
      delete e.data.errors[e.colDef.field];
      if ((e.colDef.field === "name" || e.colDef.field === "price") && e.data.price !== null && e.data.name) {
        e.data.needs_review = false;
      }
      e.api.refreshCells({ rowNodes: [e.node], force: true });
      refreshState();
    },
  });

  $("add-row").addEventListener("click", () => {
    const row = toRow({ id: "", name: "", description: "", category: "", price_cents: null, options: [], available: true, needs_review: false, review_note: "" });
    const result = gridApi.applyTransaction({ add: [row], addIndex: 0 });
    dirty = true;
    refreshState();
    if (result.add[0]) gridApi.startEditingCell({ rowIndex: result.add[0].rowIndex, colKey: "name" });
  });

  $("delete-rows").addEventListener("click", () => {
    gridApi.applyTransaction({ remove: gridApi.getSelectedRows() });
    $("delete-rows").disabled = true;
    dirty = true;
    refreshState();
  });

  $("save-items").addEventListener("click", save);

  $("publish-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("publish-btn");
    button.disabled = true;
    if (await save()) {
      event.target.submit();
    } else {
      refreshState();
    }
  });

  window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
  refreshState();
})();
