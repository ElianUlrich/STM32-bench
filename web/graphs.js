// Pestaña Gráficos: variables del micro en vivo (por el ST-Link, con el micro
// corriendo) y números que llegan por el monitor serie o RTT.
// Usa las utilidades de app.js (api, el, icon, toast, prefs, state, $).
"use strict";

const graph = {
  source: "micro",
  plot: null,
  vars: { file: null, list: [], error: null, filter: "" },
  selected: [],                 // nombres elegidos (en orden)
  live: { state: "stopped" },
  liveNames: [],                // series que está mostrando el gráfico (micro)
  serialOn: false,
  serialNames: [],
  serialBuf: "",
  serialT0: null,
  legendTimer: null,
  hover: null,
};

function graphPlot() {
  if (!graph.plot) {
    graph.plot = new LivePlot($("graph-canvas"), {
      emptyText: "Elegí variables y tocá Graficar",
      onHover: (h) => { graph.hover = h; renderLegend(); },
    });
    graph.plot.setWindow(Number($("sel-graph-window").value) || null);
    graph.plot.setStacked($("btn-graph-stack").getAttribute("aria-pressed") === "true");
  }
  return graph.plot;
}

function graphFile() {
  return state.file && state.file.kind === "elf" ? state.file : null;
}

// ------------------------------------------------------------ al entrar

async function loadGraphs() {
  graphPlot();
  renderGraphSource();
  try {
    applyLiveStatus(await api("GET", "/api/live"), true);
  } catch (e) {
    toast(e.message, true);
  }
  await loadLiveVariables();
}

async function loadLiveVariables(force) {
  const file = graphFile();
  const path = file ? file.path : null;
  if (!force && graph.vars.file === path && (graph.vars.list.length || graph.vars.error)) {
    renderVarList();
    return;
  }
  graph.vars = { file: path, list: [], error: null, filter: $("inp-var-filter").value };
  graph.selected = path ? prefs.get("graphvars." + path, []) : [];
  renderVarList();
  if (!path) return;
  try {
    const data = await api("GET", "/api/live/variables?file=" + encodeURIComponent(path));
    if (graph.vars.file !== path) return;                 // cambió el archivo mientras tanto
    graph.vars.list = data.variables;
    const names = new Set(data.variables.map((v) => v.name));
    graph.selected = graph.selected.filter((n) => names.has(n));
  } catch (e) {
    graph.vars.error = e.message;
  }
  renderVarList();
}

// ---------------------------------------------------------- lista de variables

function renderGraphFile() {
  const box = $("graph-file");
  const file = graphFile();
  const chip = currentChip();
  if (!file) {
    box.replaceChildren(el("strong", {}, "Elegí un .elf en Archivos"),
      el("small", {}, "Trae el nombre, el tipo y la dirección de cada variable."));
    return;
  }
  box.replaceChildren(el("strong", {}, file.name),
    el("small", {}, chip ? `${chip.name} · por ST-Link` : "Sin placa"));
}

function renderVarList() {
  renderGraphFile();
  const list = $("var-list");
  const running = graph.live.state === "running";
  if (graph.vars.error) {
    list.replaceChildren(el("p", { class: "note" }, graph.vars.error));
  } else if (!graph.vars.file) {
    list.replaceChildren();
  } else if (!graph.vars.list.length) {
    list.replaceChildren(el("p", { class: "note" }, "Leyendo las variables del .elf…"));
  } else {
    const q = graph.vars.filter.trim().toLowerCase();
    const shown = graph.vars.list.filter((v) => !q || v.name.toLowerCase().includes(q));
    const full = graph.selected.length >= 8;
    const rows = shown.slice(0, 400).map((v) => {
      const idx = graph.selected.indexOf(v.name);
      const checked = idx >= 0;
      const box = el("input", { type: "checkbox", checked, disabled: (!checked && full) || running });
      box.addEventListener("change", () => toggleVar(v.name, box.checked));
      const sw = el("span", { class: "swatch" });
      if (checked) sw.style.background = `var(--plot-${(idx % 8) + 1})`;
      return el("label", { class: "var-row" + ((!checked && full) || running ? " disabled" : ""), title: `${v.name} · ${v.type} · ${v.address}` },
        box, sw, el("span", { class: "var-name" }, v.name), el("span", { class: "var-type" }, v.type));
    });
    if (!rows.length) rows.push(el("p", { class: "note" }, "Ninguna variable con ese nombre."));
    if (shown.length > 400) rows.push(el("p", { class: "note" }, `Y ${shown.length - 400} más: buscá por nombre.`));
    list.replaceChildren(...rows);
  }
  renderLiveControls();
}

function toggleVar(name, on) {
  graph.selected = graph.selected.filter((n) => n !== name);
  if (on) graph.selected.push(name);
  if (graph.vars.file) prefs.set("graphvars." + graph.vars.file, graph.selected);
  renderVarList();
}

// ------------------------------------------------------------- muestreo

function renderLiveControls() {
  const running = graph.live.state === "running";
  const start = $("btn-live-start"), stop = $("btn-live-stop");
  start.hidden = running;
  stop.hidden = !running;
  const chip = currentChip();
  let reason = "";
  if (!graphFile()) reason = "Elegí un .elf en Archivos";
  else if (!graph.selected.length) reason = "Elegí al menos una variable";
  else if (!chip || !chip.methods.includes("swd")) reason = "Los gráficos de variables son para STM32 por ST-Link";
  else if (state.job) reason = "Hay una operación en curso";
  start.disabled = !!reason;
  start.title = reason;
  $("sel-graph-rate").disabled = running;
  const note = $("live-note");
  note.classList.toggle("error-text", graph.live.state === "error");
  if (graph.live.state === "error") note.textContent = graph.live.error || "Se detuvo el muestreo";
  else if (running) {
    const hz = graph.live.actual_hz;
    note.textContent = `Leyendo ${graph.live.variables.length} variable${graph.live.variables.length === 1 ? "" : "s"}`
      + (hz ? ` · ${UI.fmtValue(hz)} muestras/s` : "") + " · el micro sigue corriendo";
  } else note.textContent = reason && graphFile() ? reason : "Se leen con el micro corriendo, sin detenerlo, por el ST-Link.";
}

async function startLive() {
  const chip = currentChip();
  const file = graphFile();
  if (!file || !chip) return;
  try {
    const st = await api("POST", "/api/live/start", {
      chip: chip.id, file: file.path, variables: graph.selected,
      rate_hz: Number($("sel-graph-rate").value),
      interface: $("sel-interface").value || null,
      probe_serial: $("sel-probe").value || null,
    });
    applyLiveStatus(st, true);
  } catch (e) {
    toast(e.message, true);
  }
}

async function stopLive() {
  try { applyLiveStatus(await api("POST", "/api/live/stop")); } catch (e) { toast(e.message, true); }
}

// Estado del muestreo (de la API o por SSE). Con `samples`, redibuja todo lo recibido.
function applyLiveStatus(st, withSamples) {
  graph.live = st;
  if (st.state === "running" && st.rate_hz) $("sel-graph-rate").value = String(st.rate_hz);
  const names = (st.variables || []).map((v) => v.name);
  if (graph.source === "micro") {
    const same = names.join("\n") === graph.liveNames.join("\n");
    if ((!same && names.length) || (withSamples && st.samples)) {
      graph.liveNames = names;
      graphPlot().setSeries(names);
      if (st.samples) graphPlot().push(st.samples);
    }
  }
  renderVarList();
  renderLegend();
}

function onLiveSamples(samples) {
  if (graph.source !== "micro" || !graph.plot || !graph.liveNames.length) return;
  graph.plot.push(samples);
  scheduleLegend();
}

// ------------------------------------------------------------- serie / RTT

function onSerialText(text) {
  if (!graph.serialOn) return;
  graph.serialBuf += text;
  const lines = graph.serialBuf.split(/\r?\n/);
  graph.serialBuf = lines.pop().slice(-512);
  const now = performance.now() / 1000;
  const samples = [];
  for (const line of lines) {
    const parsed = UI.parseSerialValues(line);
    if (!parsed) continue;
    const names = parsed.names || parsed.values.map((_, i) => `valor ${i + 1}`);
    let grew = false;
    for (const n of names) {
      if (!graph.serialNames.includes(n) && graph.serialNames.length < 8) { graph.serialNames.push(n); grew = true; }
    }
    if (grew) {
      // Una serie nueva: se rearma el gráfico con lo que ya había.
      const old = graph.plot ? graph.plot.allSamples() : [];
      const prev = graph.plot ? graph.plot.names : [];
      graphPlot().setSeries(graph.serialNames);
      graph.plot.push(old.map((s) => [s[0]].concat(graph.serialNames.map((n) => {
        const i = prev.indexOf(n);
        return i >= 0 ? s[i + 1] : null;
      }))));
    }
    if (graph.serialT0 === null) graph.serialT0 = now;
    const row = [Number((now - graph.serialT0).toFixed(3))].concat(graph.serialNames.map(() => null));
    names.forEach((n, i) => {
      const k = graph.serialNames.indexOf(n);
      if (k >= 0) row[k + 1] = parsed.values[i];
    });
    samples.push(row);
  }
  if (samples.length && graph.source === "serial") {
    graphPlot().push(samples);
    scheduleLegend();
  }
}

function renderSerialPlotNote() {
  const note = $("serial-plot-note");
  const open = state.serial && state.serial.open;
  note.textContent = open ? `Puerto abierto: ${state.serial.port || ""}`.trim()
    : "Abrí un puerto (o RTT) en la pestaña Monitor serie.";
}

// --------------------------------------------------------------- vista

function renderGraphSource() {
  for (const btn of $("seg-graph-source").querySelectorAll("button")) {
    const on = btn.dataset.source === graph.source;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-checked", on ? "true" : "false");
  }
  $("graphs-micro").hidden = graph.source !== "micro";
  $("graphs-serial").hidden = graph.source !== "serial";
  $("chk-serial-plot").checked = graph.serialOn;
  renderSerialPlotNote();
}

function setGraphSource(source) {
  if (source === graph.source) return;
  graph.source = source;
  prefs.set("graphsource", source);
  const plot = graphPlot();
  if (source === "micro") {
    graph.liveNames = [];
    applyLiveStatus(graph.live, false);
    api("GET", "/api/live").then((st) => applyLiveStatus(st, true)).catch(() => {});
  } else {
    plot.setSeries(graph.serialNames);
  }
  plot.opts.emptyText = source === "micro" ? "Elegí variables y tocá Graficar"
    : "Activá \"Graficar lo que llega\" y abrí el puerto";
  renderGraphSource();
  renderLegend();
}

function scheduleLegend() {
  if (graph.legendTimer) return;
  graph.legendTimer = setTimeout(() => { graph.legendTimer = null; renderLegend(); }, 250);
}

function renderLegend() {
  const box = $("graph-legend");
  const plot = graph.plot;
  if (!plot || !plot.names.length) { box.replaceChildren(); return; }
  const stats = plot.stats();
  const items = plot.names.map((name, i) => {
    const s = stats[i];
    const value = graph.hover ? graph.hover.values[i] : s.last;
    const btn = el("button", { type: "button", class: "legend-item", "aria-pressed": plot.visible[i] ? "true" : "false",
      title: plot.visible[i] ? "Ocultar esta serie" : "Mostrar esta serie" },
      el("span", { class: "swatch" }), el("span", {}, name), el("span", { class: "lv" }, UI.fmtValue(value)),
      el("span", { class: "lr" }, s.min === null ? "" : `${UI.fmtValue(s.min)} … ${UI.fmtValue(s.max)}`));
    btn.querySelector(".swatch").style.background = `var(--plot-${(i % 8) + 1})`;
    btn.addEventListener("click", () => { plot.setVisible(i, !plot.visible[i]); renderLegend(); });
    return btn;
  });
  if (graph.hover) items.push(el("span", { class: "legend-time" }, `en ${UI.fmtValue(graph.hover.t)} s`));
  box.replaceChildren(...items);
}

function downloadGraphCsv() {
  const plot = graph.plot;
  if (!plot || !plot.t.length) { toast("Todavía no hay nada graficado"); return; }
  const blob = new Blob([UI.samplesCsv(plot.names, plot.allSamples())], { type: "text/csv" });
  const a = el("a", { href: URL.createObjectURL(blob), download: `grafico-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function bindGraphs() {
  graph.source = prefs.get("graphsource", "micro") === "serial" ? "serial" : "micro";
  for (const btn of $("seg-graph-source").querySelectorAll("button")) {
    btn.addEventListener("click", () => setGraphSource(btn.dataset.source));
  }
  $("inp-var-filter").addEventListener("input", (ev) => { graph.vars.filter = ev.target.value; renderVarList(); });
  $("btn-live-start").addEventListener("click", (ev) => busy(ev.currentTarget, startLive));
  $("btn-live-stop").addEventListener("click", (ev) => busy(ev.currentTarget, stopLive));
  $("sel-graph-window").addEventListener("change", (ev) => {
    prefs.set("graphwindow", ev.target.value);
    graphPlot().setWindow(Number(ev.target.value) || null);
    renderLegend();
  });
  const stack = $("btn-graph-stack");
  stack.setAttribute("aria-pressed", prefs.get("graphstacked", true) ? "true" : "false");
  stack.addEventListener("click", () => {
    const on = stack.getAttribute("aria-pressed") !== "true";
    stack.setAttribute("aria-pressed", on ? "true" : "false");
    prefs.set("graphstacked", on);
    graphPlot().setStacked(on);
  });
  const win = prefs.get("graphwindow", "10");
  if ([...$("sel-graph-window").options].some((o) => o.value === win)) $("sel-graph-window").value = win;
  $("btn-graph-pause").addEventListener("click", () => {
    const plot = graphPlot();
    plot.setPaused(!plot.paused);
    const btn = $("btn-graph-pause");
    btn.setAttribute("aria-pressed", plot.paused ? "true" : "false");
    btn.replaceChildren(icon(plot.paused ? "play" : "pause"), el("span", {}, plot.paused ? "Seguir" : "Pausar"));
    renderLegend();
  });
  $("btn-graph-clear").addEventListener("click", () => {
    if (graph.source === "serial") { graph.serialNames = []; graph.serialT0 = null; }
    graphPlot().setSeries(graph.source === "serial" ? [] : graph.liveNames);
    renderLegend();
  });
  $("btn-graph-csv").addEventListener("click", downloadGraphCsv);
  $("chk-serial-plot").addEventListener("change", (ev) => {
    graph.serialOn = ev.target.checked;
    graph.serialBuf = "";
  });
}
