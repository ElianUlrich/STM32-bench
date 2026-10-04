// Banco STM32 — interfaz web (JavaScript sin dependencias).
"use strict";

const $ = (id) => document.getElementById(id);
const EOLS = { "\\n": "\n", "\\r\\n": "\r\n", "\\r": "\r", "": "" };
const MAX_LOG_LINES = 3000;
const MAX_SERIAL_CHARS = 200000;
const FILE_ACTIONS = ["flash", "verify"];

const state = {
  config: null,
  chip: null,
  method: "swd",
  stlinks: [],
  serialPorts: [],
  dfu: [],
  dfuError: null,
  file: null,
  projects: [],
  job: null,
  gdb: { state: "stopped", active: false },
  serial: { open: false },
  tab: "files",
  // Depurador: estado de la sesión (llega por SSE) y fuentes del .elf que se muestran.
  dbg: { status: { state: "off" }, sources: [], sourcesFor: null, texts: {}, current: null, lines: [],
         marked: new Set(), missing: [], changed: new Set(), lastValues: {} },
};

// ---------------------------------------------------------------- utilidades

// Preferencias locales (pestaña, chip elegido...). Nunca estado importante.
const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem("bench." + key); return v === null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("bench." + key, JSON.stringify(value)); } catch (e) { /* sin storage */ }
  },
};

async function api(method, path, body) {
  const init = { method, headers: { "X-Bench": "1" } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(path, init);
  if (resp.status === 401 && path !== "/api/login") {
    location.href = "/login.html";
    throw new Error("Sesión vencida");
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* respuesta sin JSON */ }
  if (!resp.ok) {
    const err = new Error((data && data.detail) || `Error ${resp.status}`);
    err.status = resp.status;
    err.code = data && data.code;
    throw err;
  }
  return data;
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";

// Ícono del sprite de index.html (decorativo: el texto o el aria-label del control lo nombran).
function icon(name, cls) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "ic" + (cls ? " " + cls : ""));
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

// Mientras dura una acción, el botón queda deshabilitado y muestra que está trabajando.
async function busy(btn, fn) {
  if (!btn || btn.classList.contains("is-busy")) return fn();
  const wasDisabled = btn.disabled;
  btn.classList.add("is-busy");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  try {
    return await fn();
  } finally {
    btn.classList.remove("is-busy");
    btn.removeAttribute("aria-busy");
    btn.disabled = wasDisabled;
    // Mientras tanto pudo empezar un trabajo: se recalcula qué botones corresponden.
    if (state.config) { updateActionButtons(); renderGdb(); }
  }
}

let toastTimer = null;
function toast(message, isError) {
  const t = $("toast");
  t.textContent = message;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 7000 : 3500);
}

function fmtSize(bytes) {
  if (bytes == null) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  if (bytes < 1024 ** 3) return (bytes / 1024 ** 2).toFixed(1) + " MB";
  return (bytes / 1024 ** 3).toFixed(1) + " GB";
}

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtTime(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  return d.toLocaleTimeString("es-AR", { hour12: false });
}

function fmtDuration(s) {
  if (s == null) return "—";
  if (s < 60) return s.toFixed(1) + " s";
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s % 60)} s`;
}

function fmtUptime(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (d ? d + " d " : "") + h + " h " + m + " min";
}

// Resolución del diálogo de confirmación abierto (si hay uno).
let confirmResolve = null;

function finishConfirm(ok) {
  const dlg = $("dlg-confirm");
  if (dlg.open) dlg.close();
  const resolve = confirmResolve;
  confirmResolve = null;
  if (resolve) resolve(ok);
}

function confirmDialog(title, text, okLabel, danger) {
  // Se resuelve desde los botones (y Esc), no con el evento "close": algunos
  // navegadores lo demoran si la pestaña no está visible.
  finishConfirm(false);
  return new Promise((resolve) => {
    confirmResolve = resolve;
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    const ok = $("confirm-ok");
    ok.textContent = okLabel || "Aceptar";
    ok.className = "btn " + (danger ? "danger" : "primary");
    $("dlg-confirm").showModal();
    $("confirm-cancel").focus();
  });
}

async function copyText(text) {
  // navigator.clipboard solo existe en contextos seguros (HTTPS/localhost);
  // por Tailscale con HTTP se usa el método clásico.
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* se intenta el método clásico */ }
  const area = el("textarea", {}, text);
  document.body.append(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  area.remove();
  return ok;
}

// ------------------------------------------------------------ configuración

function chipById(id) {
  return (state.config && state.config.chips.find((c) => c.id === id)) || null;
}

function currentChip() {
  return chipById(state.chip);
}

// Etiqueta del botón de cada método.
function methodLabel(chip, method) {
  if (method === "swd") return "ST-Link (SWD)";
  if (method === "dfu") return "USB DFU";
  if (method === "esptool") return "Puerto serie (esptool)";
  if (method === "avrdude") return chip && chip.avrdude && chip.avrdude.isp ? "Programador ISP (avrdude)" : "Bootloader serie (avrdude)";
  return method;
}

// <select> de placas agrupadas por familia (STM32, Espressif, Arduino).
function chipOptions(includeEmpty) {
  const groups = new Map();
  for (const c of state.config.chips) {
    if (!groups.has(c.group)) groups.set(c.group, []);
    groups.get(c.group).push(c);
  }
  const nodes = includeEmpty ? [el("option", { value: "" }, includeEmpty)] : [];
  for (const [group, chips] of groups) {
    nodes.push(el("optgroup", { label: group }, ...chips.map((c) => el("option", { value: c.id }, c.name))));
  }
  return nodes;
}

function renderConfig() {
  const cfg = state.config;
  $("mock-badge").hidden = !cfg.mock;

  const errors = $("config-errors");
  errors.hidden = !cfg.config_errors.length;
  errors.textContent = cfg.config_errors.length ? "Error de configuración: " + cfg.config_errors.join(" · ") : "";
  renderCfgCheck();

  $("sel-chip").replaceChildren(...chipOptions(null));
  const saved = prefs.get("chip", null);
  state.chip = cfg.chips.some((c) => c.id === saved) ? saved : (cfg.chips[0] && cfg.chips[0].id);
  $("sel-chip").value = state.chip || "";

  $("sel-upload-target").replaceChildren(...chipOptions("¿Para qué placa es?"));

  $("sel-interface").replaceChildren(...cfg.interfaces.map((i) =>
    el("option", { value: i.id, title: i.description }, `${i.id} (${i.cfg})`)));

  $("boot-desc").textContent = cfg.boot_control.description;
  $("upload-limit").textContent = `Máximo ${cfg.uploads.max_size_mb} MB por archivo`;
  onChipChange(true);
}

function renderCfgCheck() {
  const check = state.config.cfg_check;
  const box = $("cfg-missing");
  if (check && check.checked && check.missing.length) {
    box.hidden = false;
    box.textContent = "OpenOCD instalado no tiene estos archivos: " +
      check.missing.map((m) => `${m.cfg} (${m.kind} ${m.id})`).join(", ") +
      ". Esas combinaciones van a fallar hasta instalar un OpenOCD que los incluya.";
  } else if (check && !check.checked && !state.config.mock) {
    box.hidden = false;
    box.textContent = "No se pudieron verificar los .cfg: no se encontró el directorio de scripts de OpenOCD" +
      (check.scripts_dir ? ` (${check.scripts_dir})` : "") + ". ¿Está instalado? (scripts/build_openocd.sh)";
  } else {
    box.hidden = true;
  }
}

function onChipChange(initial) {
  const chip = currentChip();
  if (!chip) return;
  prefs.set("chip", chip.id);
  const methods = prefs.get("methods", {});
  const wanted = methods[chip.id] || chip.default_method;
  state.method = chip.methods.includes(wanted) ? wanted : chip.default_method;
  const iface = chip.swd && chip.swd.interface ? chip.swd.interface : state.config.default_interface;
  $("sel-interface").value = prefs.get("iface." + chip.id, iface);
  if (!$("sel-interface").value) $("sel-interface").value = iface;
  // La placa de destino de las subidas sigue a la placa elegida.
  $("sel-upload-target").value = chip.id;
  renderMethodButtons();
  updateMethodUI();
  if (!initial) refreshProbes();
}

function renderMethodButtons() {
  const chip = currentChip();
  const seg = $("seg-method");
  const methods = chip ? chip.methods : [];
  seg.style.gridTemplateColumns = `repeat(${Math.max(methods.length, 1)}, 1fr)`;
  seg.replaceChildren(...methods.map((m) => el("button", {
    type: "button", role: "radio", "data-method": m,
    onclick: () => setMethod(m),
  }, methodLabel(chip, m))));
}

function setMethod(method) {
  const chip = currentChip();
  if (!chip || !chip.methods.includes(method) || state.method === method) return;
  state.method = method;
  const methods = prefs.get("methods", {});
  methods[chip.id] = method;
  prefs.set("methods", methods);
  updateMethodUI();
  refreshProbes();
}

function methodReason(method, action) {
  const reasons = state.config.unsupported_reasons || {};
  if (method === "avrdude" && action === "erase") return reasons.avr_erase || "";
  return reasons[method] || "";
}

function usesSerialPort(chip, method) {
  if (method === "esptool") return true;
  return method === "avrdude" && !(chip && chip.avrdude && chip.avrdude.isp);
}

function updateMethodUI() {
  const chip = currentChip();
  const m = state.method;
  for (const btn of $("seg-method").querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.method === m);
    btn.setAttribute("aria-checked", btn.dataset.method === m ? "true" : "false");
  }
  $("swd-fields").hidden = m !== "swd";
  $("dfu-fields").hidden = m !== "dfu";
  $("serial-fields").hidden = !usesSerialPort(chip, m);
  $("isp-note").hidden = !(m === "avrdude" && chip && chip.avrdude && chip.avrdude.isp);
  $("leave-field").hidden = m !== "dfu";
  const reason = m === "swd" ? "" : methodReason(m, "halt");
  $("method-reason").hidden = !reason;
  $("method-reason").textContent = reason;

  // Dirección para .bin (STM32: base de flash; ESP: se detecta sola).
  const espProject = !!(state.file && state.file.esp && m === "esptool");
  const isBin = state.file && state.file.kind === "bin" && ["swd", "dfu", "esptool"].includes(m) && !espProject;
  $("addr-field").hidden = !isBin;
  $("esp-scope-field").hidden = !espProject;
  if (espProject) {
    const esp = state.file.esp;
    const opts = $("sel-esp-scope").options;
    opts[0].textContent = `Sólo este archivo (${ESP_ROLES[esp.role] || esp.role} @ ${esp.address})`;
    opts[1].textContent = `Todo el proyecto (${esp.files} archivos)`;
  }
  if (chip) {
    $("inp-address").placeholder = m === "esptool" ? "automática (0x10000 aplicación · 0x0 combinada)"
      : (m === "dfu" && chip.dfu ? chip.dfu.address : (chip.flash_base || ""));
  }
  renderFileBinding();
  updateActionButtons();
  renderGdb();
}

// ¿El archivo elegido corresponde a la placa elegida?
function fileMismatchReason() {
  const f = state.file;
  const chip = currentChip();
  if (!f || !chip) return "";
  if (!f.target) return "Este archivo no tiene placa asignada: asignala en la pestaña Archivos.";
  if (f.target !== chip.id) {
    const other = chipById(f.target);
    return `Este archivo es para ${other ? other.name : f.target}, no para ${chip.name}.`;
  }
  return "";
}

function renderFileBinding() {
  const warn = $("fw-target-warn");
  const reason = fileMismatchReason();
  warn.hidden = !reason;
  warn.textContent = reason;
}

function updateActionButtons() {
  const chip = currentChip();
  const caps = (chip && state.config.chip_capabilities[chip.id] || {})[state.method] || [];
  const busy = !!state.job;
  const mismatch = fileMismatchReason();
  for (const btn of document.querySelectorAll("[data-action]")) {
    const action = btn.dataset.action;
    let reason = "";
    if (!chip) reason = "No hay placas configuradas";
    else if (!caps.includes(action)) reason = methodReason(state.method, action) || "No disponible con este método";
    else if (busy) reason = "Hay una operación en curso";
    else if (FILE_ACTIONS.includes(action) && !state.file) reason = "Elegí un archivo de firmware";
    else if (FILE_ACTIONS.includes(action) && mismatch) reason = mismatch;
    btn.disabled = !!reason;
    btn.title = reason;
  }
  renderDebug();
}

// -------------------------------------------------------------- programadores

async function refreshProbes() {
  const withDfu = state.method === "dfu";
  try {
    const data = await api("GET", "/api/probes?dfu=" + (withDfu ? "1" : "0"));
    state.stlinks = data.stlinks;
    state.serialPorts = data.serial_ports || [];
    if (withDfu) {
      state.dfu = data.dfu;
      state.dfuError = data.dfu_error;
    }
    renderProbes();
  } catch (e) {
    toast("No se pudo listar los programadores: " + e.message, true);
  }
}

function renderProbes() {
  // ST-Link
  const sel = $("sel-probe");
  const previous = sel.value || prefs.get("probe", "");
  const options = [];
  if (!state.stlinks.length) options.push(el("option", { value: "" }, "No se detectó ningún ST-Link"));
  else if (state.stlinks.length > 1) options.push(el("option", { value: "" }, "Elegí un ST-Link…"));
  for (const p of state.stlinks) {
    options.push(el("option", { value: p.serial }, `${p.model} — ${p.serial}${p.accessible ? "" : "  ⚠ sin permisos"}`));
  }
  sel.replaceChildren(...options);
  if (state.stlinks.some((p) => p.serial === previous)) sel.value = previous;
  else if (state.stlinks.length === 1) sel.value = state.stlinks[0].serial;
  const chosen = state.stlinks.find((p) => p.serial === sel.value);
  let note = "";
  if (!state.stlinks.length) note = "Conectá el ST-Link a un USB de la BeagleBoard.";
  else if (chosen && !chosen.accessible) note = "Sin permiso sobre el USB: revisá las reglas udev (ver README).";
  else if (chosen && chosen.serial_was_binary) note = "ST-Link/V2 con número de serie binario: se usa su forma hexadecimal.";
  $("probe-note").textContent = note;

  // Puerto serie para esptool / avrdude
  const psel = $("sel-prog-port");
  // Se recuerda el puerto elegido POR PLACA (no arrastrar el de otra placa).
  const pprev = prefs.get("progport." + state.chip, "");
  const ports = state.serialPorts || [];
  const popts = [];
  if (!ports.length) popts.push(el("option", { value: "" }, "No hay puertos serie USB"));
  else if (ports.length > 1) popts.push(el("option", { value: "" }, "Elegí el puerto de la placa…"));
  for (const p of ports) popts.push(el("option", { value: p.device }, `${p.device} — ${p.description}`));
  psel.replaceChildren(...popts);
  if (ports.some((p) => p.device === pprev)) psel.value = pprev;
  else if (ports.length === 1) psel.value = ports[0].device;
  $("port-note").textContent = ports.length ? "" : "Conectá la placa (ESP32 / Arduino) por USB a la BeagleBoard.";

  // DFU
  const dsel = $("sel-dfu");
  const dprev = dsel.value;
  const doptions = [el("option", { value: "" }, "Automático")];
  for (const d of state.dfu) {
    doptions.push(el("option", { value: d.serial }, `${d.vid_pid} — ${d.serial || "sin serie"} (${d.path})`));
  }
  dsel.replaceChildren(...doptions);
  if (state.dfu.some((d) => d.serial === dprev)) dsel.value = dprev;
  let dnote = "";
  if (state.dfuError) dnote = state.dfuError;
  else if (!state.dfu.length) dnote = "Ningún STM32 en modo DFU ahora. Al operar, la app lo pone en bootloader (" +
    state.config.boot_control.backend + ").";
  else dnote = `${state.dfu.length} dispositivo(s) en modo DFU.`;
  $("dfu-note").textContent = dnote;
}

// ------------------------------------------------------------------ archivos

async function loadFiles() {
  try {
    const data = await api("GET", "/api/files");
    state.projects = data.projects;
    $("projects-dir").textContent = "Directorio: " + data.projects_dir;
    if (state.file) {
      const all = state.projects.flatMap((p) => p.files);
      const still = all.find((f) => f.path === state.file.path);
      setFile(still || null);
    }
    renderFiles();
  } catch (e) {
    toast("No se pudo leer el directorio de proyectos: " + e.message, true);
  }
}

function renderFiles() {
  const filter = $("inp-filter").value.trim().toLowerCase();
  const list = $("files-list");
  const blocks = [];
  for (const project of state.projects) {
    const files = project.files.filter((f) => !filter || f.path.toLowerCase().includes(filter)
      || (f.target && (chipById(f.target) || {}).name && chipById(f.target).name.toLowerCase().includes(filter)));
    if (!files.length) continue;
    const head = el("div", { class: "project-head" },
      el("span", {}, project.name),
      el("span", { class: "tag" }, project.uploaded ? "subido desde la web" : (project.key ? "sincronizado" : "")));
    const rows = files.map((f) => {
      // Selector de placa del archivo (asignar / cambiar).
      const target = el("select", {
        class: "target-sel" + (f.target ? "" : " unassigned"),
        title: "Placa a la que corresponde este archivo",
        onclick: (ev) => ev.stopPropagation(),
        onchange: (ev) => assignTarget(f, ev.target.value),
      }, ...chipOptions("⚠ sin placa"));
      target.value = f.target || "";
      const selected = !!(state.file && state.file.path === f.path);
      const row = el("div", {
        class: "file-row" + (selected ? " selected" : "") + (f.usable ? "" : " unusable"),
        title: f.usable ? f.path : "Nombre con caracteres no permitidos: renombralo para poder usarlo",
        onclick: () => { if (f.usable) { setFile(f); renderFiles(); } },
      },
        el("span", { class: "kind " + f.kind }, f.kind),
        // Botón: el archivo se elige también con el teclado (Tab + Enter).
        el("button", { class: "name", type: "button", disabled: !f.usable, "aria-pressed": selected ? "true" : "false",
          "aria-label": `Elegir ${f.path}`, onclick: (ev) => { ev.stopPropagation(); if (f.usable) { setFile(f); renderFiles(); } } },
          f.esp ? el("span", { class: "esp-addr", title: `Proyecto ESP-IDF: ${ESP_ROLES[f.esp.role] || f.esp.role}` },
            f.esp.address) : null,
          f.name, f.subdir ? el("span", { class: "sub" }, "  " + f.subdir) : null),
        target,
        el("span", { class: "size" }, fmtSize(f.size)),
        el("span", { class: "date" }, fmtDate(f.mtime)),
        f.deletable
          ? el("button", { class: "icon-btn", type: "button", title: "Borrar", "aria-label": "Borrar " + f.name,
            onclick: (ev) => { ev.stopPropagation(); deleteFile(f); } }, icon("trash"))
          : el("span", {}));
      return row;
    });
    blocks.push(el("div", { class: "project" }, head, ...rows));
  }
  if (!blocks.length) {
    blocks.push(el("div", { class: "empty-state" }, filter ? "Ningún archivo coincide con el filtro." :
      "No hay firmware todavía. Subí un archivo o sincronizá la carpeta de proyectos con Syncthing."));
  }
  list.replaceChildren(...blocks);
}

async function assignTarget(file, target) {
  try {
    const res = await api("PUT", "/api/files/target", { path: file.path, target: target || null });
    const chip = chipById(target);
    toast(target ? `${file.name} → ${chip ? chip.name : target}` : `${file.name}: sin placa asignada`);
    for (const w of res.warnings || []) toast(w, false);
    await loadFiles();
  } catch (e) {
    toast(e.message, true);
    renderFiles();   // vuelve el selector al valor anterior
  }
}

function setFile(file) {
  if (!state.file || !file || state.file.path !== file.path) $("sel-esp-scope").value = "file";
  state.file = file;
  if (state.tab === "debug" && !dbgActive()) loadDebugSources();
  const box = $("fw-selected");
  if (file) {
    const chip = chipById(file.target);
    box.className = "fw-selected";
    box.replaceChildren(el("span", { class: "kind " + file.kind }, file.kind), " ", file.path,
      el("span", { class: "muted" }, `  (${fmtSize(file.size)})`),
      el("div", { class: "fw-target" + (chip ? "" : " unassigned") }, chip ? `Placa: ${chip.name}` : "⚠ Sin placa asignada"));
    prefs.set("file", file.path);
    // Elegir un archivo elige su placa: así no se graba en otro micro.
    if (chip && chip.id !== state.chip) {
      state.chip = chip.id;
      $("sel-chip").value = chip.id;
      onChipChange(false);
      toast(`Placa: ${chip.name} (la del archivo)`);
      return;
    }
  } else {
    box.className = "fw-selected empty";
    box.replaceChildren("Elegí un archivo en la pestaña ", el("em", {}, "Archivos"));
  }
  updateMethodUI();
}

async function deleteFile(file) {
  const ok = await confirmDialog("Borrar archivo", `¿Borrar ${file.path}? No se puede deshacer.`, "Borrar", true);
  if (!ok) return;
  try {
    await api("DELETE", "/api/files?path=" + encodeURIComponent(file.path));
    toast("Archivo borrado");
    loadFiles();
  } catch (e) {
    toast(e.message, true);
  }
}

function uploadOne(file, project, target) {
  const form = new FormData();
  form.append("file", file);
  form.append("project", project);
  form.append("target", target);
  return postForm("/api/files/upload", form, file.name);
}

function postForm(url, form, label) {
  // XMLHttpRequest para poder mostrar el progreso de la subida.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-Bench", "1");
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        $("upload-status").textContent = `Subiendo ${label}… ${Math.round(ev.loaded / ev.total * 100)}%`;
      }
    };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* sin JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error((data && data.detail) || `Error ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Error de red"));
    xhr.send(form);
  });
}

async function uploadFiles(fileList) {
  const project = $("inp-project").value.trim() || "general";
  const target = $("sel-upload-target").value;
  const chip = chipById(target);
  if (!chip) {
    toast("Elegí para qué placa es el binario antes de subirlo", true);
    $("sel-upload-target").focus();
    return;
  }
  const maxBytes = state.config.uploads.max_size_mb * 1024 * 1024;
  let last = null;
  for (const file of fileList) {
    const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    if (!chip.extensions.includes(ext) && ext !== ".zip") {
      toast(`${file.name}: ${chip.name} acepta ${chip.extensions.join(", ")}`, true);
      continue;
    }
    if (file.size > maxBytes) {
      toast(`${file.name}: supera ${state.config.uploads.max_size_mb} MB`, true);
      continue;
    }
    try {
      last = await uploadOne(file, project, target);
      $("upload-status").textContent = last.folder
        ? `Proyecto ${file.name} → ${last.folder}: ${last.firmware.length} binario(s) y ${last.sources} fuente(s), para ${chip.name}`
        : `${file.name} subido para ${chip.name}`;
      for (const w of last.warnings || []) toast(w, false);
    } catch (e) {
      $("upload-status").textContent = "";
      toast(`${file.name}: ${e.message}`, true);
    }
  }
  await loadFiles();
  if (last) {
    const f = state.projects.flatMap((p) => p.files).find((x) => x.path === last.path);
    if (f) { setFile(f); renderFiles(); }
  }
}

// --------------------------------------------------------- proyecto ESP-IDF
// `idf.py build` deja en build/flasher_args.json qué .bin grabar y dónde. De la
// carpeta del proyecto (cientos de MB) se suben sólo esos archivos.

const ESP_MANIFEST = "flasher_args.json";
const ESP_ROLES = { bootloader: "gestor de arranque", "partition-table": "tabla de particiones",
  app: "programa", data: "datos" };

function entryFile(dir, path) {
  return new Promise((resolve) => dir.getFile(path, {}, (e) => e.file(resolve, () => resolve(null)), () => resolve(null)));
}

// Carpeta arrastrada: se va directo a build/flasher_args.json sin recorrer todo.
async function espFromDirEntry(dir) {
  for (const base of ["", "build/"]) {
    const manifest = await entryFile(dir, base + ESP_MANIFEST);
    if (manifest) return { name: dir.name, manifest, get: (rel) => entryFile(dir, base + rel) };
  }
  return null;
}

// Carpeta elegida con el botón: el navegador da la lista de todos los archivos (no los sube).
function espFromFileList(files) {
  const byPath = new Map(files.map((f) => [f.webkitRelativePath, f]));
  const depth = (f) => f.webkitRelativePath.split("/").length;
  const manifest = files.filter((f) => f.name === ESP_MANIFEST).sort((a, b) => depth(a) - depth(b))[0];
  if (!manifest) return null;
  const base = manifest.webkitRelativePath.slice(0, -ESP_MANIFEST.length);
  return { name: manifest.webkitRelativePath.split("/")[0], manifest, get: async (rel) => byPath.get(base + rel) || null };
}

async function uploadEspProject(src) {
  if (!src) {
    toast("Esa carpeta no tiene build/flasher_args.json: compilá el proyecto (idf.py build) y probá de nuevo", true);
    return;
  }
  const target = $("sel-upload-target").value;
  const chip = chipById(target);
  if (!chip || !chip.esptool) {
    toast("Elegí la placa ESP del proyecto (Placa) antes de subirlo", true);
    $("sel-upload-target").focus();
    return;
  }
  let doc;
  try { doc = JSON.parse(await src.manifest.text()); } catch (e) { doc = null; }
  if (!doc || typeof doc.flash_files !== "object") {
    toast(`${ESP_MANIFEST} no es válido: volvé a compilar el proyecto`, true);
    return;
  }
  const items = [];
  for (const [addr, rel] of Object.entries(doc.flash_files)) {
    const file = await src.get(rel);
    if (!file) { toast(`Falta ${rel} en la carpeta build: compilá el proyecto (idf.py build)`, true); return; }
    items.push({ addr: parseInt(addr, 16), rel, file });
  }
  items.sort((a, b) => a.addr - b.addr);
  const espChip = (doc.extra_esptool_args || {}).chip || "?";
  if (chip.esptool.chip !== espChip) {
    toast(`El proyecto es para ${espChip} y elegiste ${chip.name}: elegí la placa correcta`, true);
    return;
  }
  const total = items.reduce((n, i) => n + i.file.size, 0);
  const maxBytes = state.config.uploads.max_size_mb * 1024 * 1024;
  const big = items.find((i) => i.file.size > maxBytes);
  if (big) { toast(`${big.rel}: supera ${state.config.uploads.max_size_mb} MB`, true); return; }
  const roleOf = (rel) => ["bootloader", "app", "partition-table"].find((r) => doc[r] && doc[r].file === rel) || "data";
  const lines = items.map((i) => `0x${i.addr.toString(16).toUpperCase().padStart(5, "0")}  ${i.rel}  `
    + `(${ESP_ROLES[roleOf(i.rel)]}, ${fmtSize(i.file.size)}, ${fmtDate(i.file.lastModified / 1000)})`);
  const ok = await confirmDialog(`Proyecto ESP-IDF: ${src.name}`,
    `Para ${chip.name}. Se suben sólo ${items.length} archivos (${fmtSize(total)}) de la carpeta build:\n\n`
    + lines.join("\n") + "\n\nRevisá la fecha del programa: si es vieja, falta compilar.", "Subir", false);
  if (!ok) return;
  const form = new FormData();
  form.append("manifest", src.manifest, ESP_MANIFEST);
  for (const i of items) {
    form.append("files", i.file, i.rel.split("/").pop());
    form.append("paths", i.rel);
  }
  form.append("project", $("inp-project").value.trim() || "general");
  form.append("target", target);
  try {
    const res = await postForm("/api/files/esp-project", form, `proyecto ${src.name}`);
    $("upload-status").textContent = `Proyecto ${src.name} → ${res.folder}: ${res.firmware.length} archivos para ${chip.name}`;
    await loadFiles();
    const f = state.projects.flatMap((p) => p.files).find((x) => x.path === res.path);
    if (f) { setFile(f); renderFiles(); }
  } catch (e) {
    $("upload-status").textContent = "";
    toast(`${src.name}: ${e.message}`, true);
  }
}

// ------------------------------------------------------------------ acciones

const ACTION_CONFIRM = {
  erase: (chip) => ["Borrar flash completa", `Se borra TODA la flash de ${chip.name}`
    + (chip.esptool ? ", incluidos los datos guardados (NVS: presets, ajustes, WiFi)" : "") + ". ¿Continuar?", "Borrar", true],
};

async function runAction(action, extra) {
  const chip = currentChip();
  if (!chip) return;
  extra = extra || {};
  if (ACTION_CONFIRM[action] && !extra.stop_gdb) {
    const [title, text, label, danger] = ACTION_CONFIRM[action](chip);
    if (!(await confirmDialog(title, text, label, danger))) return;
  }
  const body = { action, chip: chip.id, method: state.method };
  if (state.method === "swd") {
    body.interface = $("sel-interface").value || null;
    body.probe_serial = $("sel-probe").value || null;
  } else if (state.method === "dfu") {
    body.dfu_serial = $("sel-dfu").value || null;
    body.leave = $("chk-leave").checked;
  } else if (usesSerialPort(chip, state.method)) {
    body.serial_port = $("sel-prog-port").value || null;
  }
  if (FILE_ACTIONS.includes(action)) {
    body.file = state.file.path;
    if (state.file.esp && state.method === "esptool") body.esp_scope = $("sel-esp-scope").value;
    else if (state.file.kind === "bin") body.address = $("inp-address").value.trim() || null;
  }
  Object.assign(body, extra);
  try {
    await api("POST", "/api/jobs", body);
  } catch (e) {
    if (e.code === "gdb_active") {
      const ok = await confirmDialog("Servidor GDB activo",
        e.message + "\n\n¿Detener el servidor GDB y continuar?", "Detener y continuar", true);
      if (ok) return runAction(action, Object.assign({}, extra, { stop_gdb: true }));
      return;
    }
    toast(e.message, true);
  }
}

function renderJob() {
  const job = state.job;
  const pill = $("pill-job");
  pill.querySelector(".dot").className = "dot" + (job ? " busy" : "");
  pill.querySelector("span").textContent = job ? "Ocupado" : "Libre";
  $("job-box").hidden = !job;
  if (job) $("job-desc").textContent = job.description;
  updateActionButtons();
  renderGdb();
}

function showResult(job) {
  const box = $("last-result");
  box.hidden = false;
  box.className = "last-result " + job.state;
  const glyph = { ok: "check", error: "x", cancelled: "stop" }[job.state];
  const children = [el("strong", {}, glyph ? icon(glyph) : null, job.message), el("div", { class: "muted" }, job.description)];
  if (job.warnings && job.warnings.length) {
    children.push(el("ul", {}, ...job.warnings.map((w) => el("li", {}, w))));
  }
  box.replaceChildren(...children);
}

// --------------------------------------------------------------- servidor GDB

function renderGdb() {
  const g = state.gdb;
  const noGdb = state.method !== "swd";
  const dot = $("gdb-dot");
  const labels = { stopped: "Detenido", starting: "Iniciando…", listening: "Escuchando", error: "Error" };
  dot.className = "dot" + ({ listening: " ok", starting: " busy", error: " err" }[g.state] || "");
  let label = labels[g.state] || g.state;
  if (g.state === "listening" && g.chip_name) label += ` — ${g.chip_name}`;
  if (g.client_connected) label += " · cliente conectado";
  $("gdb-state").textContent = label;
  const listening = g.active && g.connect;
  $("gdb-connect").hidden = !listening;
  $("gdb-addr").textContent = listening ? g.connect : "";
  let note = "";
  if (noGdb) note = "Sólo disponible por SWD con ST-Link (STM32). " + methodReason(state.method, "gdb");
  else if (g.state === "error") note = g.last_error || "";
  else if (listening) {
    note = `En GDB: target extended-remote ${g.connect}`;
    if (g.rtt) {
      const rttLabel = { encontrado: "RTT conectado", buscando: "RTT: buscando el bloque", no_encontrado: "RTT: bloque no encontrado (¿el programa corre?)" };
      note += ` · ${rttLabel[g.rtt_state] || "RTT"} (puerto ${g.rtt.port})`;
    }
  }
  else note = `Puerto ${state.config ? state.config.gdb.port : 3333}, escucha en: ${state.config ? state.config.gdb.bind : ""}`;
  $("gdb-note").textContent = note;
  $("btn-gdb-start").disabled = noGdb || g.active || !!state.job;
  $("btn-gdb-start").title = noGdb ? methodReason(state.method, "gdb") : "";
  $("btn-gdb-stop").disabled = !g.active;

  const pill = $("pill-gdb");
  pill.querySelector(".dot").className = "dot" + ({ listening: " ok", starting: " busy", error: " err" }[g.state] || "");
  pill.querySelector("span").textContent = g.active ? `GDB ${g.connect || ""}` : (g.state === "error" ? "GDB con error" : "GDB detenido");
  if (dbgActive()) {
    pill.querySelector("span").textContent = "Depurando en la web";
    if (g.client_connected) $("gdb-state").textContent = labels[g.state] + (g.chip_name ? ` — ${g.chip_name}` : "") + " · usado por la pestaña Depurar";
  }
}

async function startGdb() {
  const chip = currentChip();
  try {
    state.gdb = await api("POST", "/api/gdb/start", {
      chip: chip.id,
      interface: $("sel-interface").value || null,
      probe_serial: $("sel-probe").value || null,
      // Si el .elf elegido usa RTT, OpenOCD publica también ese canal.
      file: state.file && state.file.kind === "elf" ? state.file.path : null,
    });
    renderGdb();
  } catch (e) {
    toast(e.message, true);
  }
}

async function stopGdb() {
  try {
    state.gdb = await api("POST", "/api/gdb/stop");
    renderGdb();
  } catch (e) {
    toast(e.message, true);
  }
}

// --------------------------------------------------------------- depurador

const DBG_LABELS = { off: "Sin sesión", starting: "Iniciando…", running: "Corriendo…", stopped: "Detenido", error: "Error" };
const C_KEYWORDS = new Set(("auto break case char const continue default do double else enum extern float for goto if " +
  "inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void " +
  "volatile while bool true false NULL uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t size_t " +
  "__attribute__ __IO __I __O").split(" "));

function dbgStatus() { return state.dbg.status || { state: "off" }; }
function dbgActive() { return !!dbgStatus().active; }

// .elf que se muestra: el de la sesión activa o el elegido en Archivos.
function dbgElf() {
  const st = dbgStatus();
  if (st.active && st.elf) return st.elf;
  return state.file && state.file.kind === "elf" ? state.file.path : null;
}

function pendingFor(elf) { return elf ? prefs.get("dbgbp." + elf, []) : []; }
function setPending(elf, list) { if (elf) prefs.set("dbgbp." + elf, list); }
function currentSource() { return state.dbg.sources.find((s) => s.id === state.dbg.current) || null; }

async function loadDebugSources(force) {
  const elf = dbgElf();
  if (!elf) {
    state.dbg.sources = [];
    state.dbg.sourcesFor = null;
    state.dbg.current = null;
    renderDebugSources();
    return;
  }
  if (!force && !state.dbg.stale && state.dbg.sourcesFor === elf) return;
  state.dbg.stale = false;
  state.dbg.sourcesFor = elf;
  state.dbg.texts = {};
  state.dbg.current = null;
  state.dbg.sourceError = null;
  state.dbg.missing = [];
  try {
    const data = await api("GET", "/api/debug/sources?file=" + encodeURIComponent(elf));
    if (state.dbg.sourcesFor !== elf) return;
    state.dbg.sources = data.sources;
    state.dbg.missing = data.missing || [];
  } catch (e) {
    state.dbg.sources = [];
    state.dbg.sourceError = e.message;
  }
  renderDebugSources();
  const frame = dbgStatus().frame;
  const wanted = frame && frame.source_id != null ? frame.source_id : prefs.get("dbgsrc." + elf, null);
  const first = state.dbg.sources.find((s) => s.id === wanted)
    || state.dbg.sources.find((s) => /\.(c|cc|cpp|s)$/i.test(s.base)) || state.dbg.sources[0];
  if (first) await showSource(first.id, frame && frame.source_id === first.id ? frame.line : null);
}

function renderDebugSources() {
  const sel = $("sel-dbg-source");
  sel.replaceChildren(...state.dbg.sources.map((s) => el("option", { value: s.id }, s.name)));
  sel.hidden = !state.dbg.sources.length;
  if (state.dbg.current != null) sel.value = state.dbg.current;
  const empty = $("dbg-empty");
  if (!state.dbg.sources.length) {
    const code = $("dbg-code");
    code.replaceChildren(empty);
    $("dbg-source-path").textContent = "";
    state.dbg.lines = [];
    state.dbg.current = null;
    if (!dbgElf()) empty.replaceChildren("Elegí un ", el("strong", {}, ".elf"), " en la pestaña Archivos (con su placa asignada).");
    else if (state.dbg.sourceError) empty.replaceChildren(state.dbg.sourceError);
    else empty.replaceChildren("No se encontraron los archivos fuente de este .elf. Subí el proyecto como ",
      el("strong", {}, ".zip"), " (el .elf y la carpeta con los .c/.h) o sincronizalo con Syncthing.");
  }
  renderDebug();
}

function highlightLine(line, inComment) {
  const nodes = [];
  let plain = "";
  const flush = () => { if (plain) { nodes.push(document.createTextNode(plain)); plain = ""; } };
  const push = (cls, text) => { flush(); if (text) nodes.push(el("span", { class: cls }, text)); };
  let i = 0;
  if (inComment) {
    const end = line.indexOf("*/");
    if (end < 0) { push("tk-com", line); return [nodes, true]; }
    push("tk-com", line.slice(0, end + 2));
    i = end + 2;
  }
  while (i < line.length) {
    const rest = line.slice(i);
    let m;
    if (rest.startsWith("//")) { push("tk-com", rest); return [nodes, false]; }
    if (rest.startsWith("/*")) {
      const end = rest.indexOf("*/", 2);
      if (end < 0) { push("tk-com", rest); return [nodes, true]; }
      push("tk-com", rest.slice(0, end + 2));
      i += end + 2;
      continue;
    }
    if ((m = rest.match(/^"(\\.|[^"\\])*"?/)) || (m = rest.match(/^'(\\.|[^'\\])*'?/))) {
      push("tk-str", m[0]); i += m[0].length; continue;
    }
    if (!plain.trim() && !nodes.length && (m = rest.match(/^#\s*[A-Za-z_]+/))) {
      push("tk-pp", m[0]); i += m[0].length; continue;
    }
    if ((m = rest.match(/^(0[xX][0-9A-Fa-f]+|0[bB][01]+|\d+(\.\d+)?)[uUlLfF]*\b/)) && !/\w/.test(line[i - 1] || "")) {
      push("tk-num", m[0]); i += m[0].length; continue;
    }
    if ((m = rest.match(/^[A-Za-z_]\w*/))) {
      if (C_KEYWORDS.has(m[0])) push("tk-kw", m[0]); else plain += m[0];
      i += m[0].length;
      continue;
    }
    plain += line[i];
    i += 1;
  }
  flush();
  return [nodes, false];
}

function buildCode(src, text) {
  const code = $("dbg-code");
  const withCode = new Set(src.lines);
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const frag = document.createDocumentFragment();
  const rows = [];
  let inComment = false;
  lines.forEach((text, idx) => {
    const n = idx + 1;
    const [nodes, still] = highlightLine(text.replace(/\t/g, "    "), inComment);
    inComment = still;
    const row = el("div", { class: "cl" + (withCode.has(n) ? " code" : ""), "data-line": n },
      el("span", { class: "gut" }), el("span", { class: "ln" }, n), el("span", { class: "src" }, ...nodes));
    rows.push(row);
    frag.append(row);
  });
  code.replaceChildren(frag);
  code.scrollTop = 0;
  state.dbg.lines = rows;
  state.dbg.marked = new Set();
}

async function showSource(id, scrollLine) {
  const elf = state.dbg.sourcesFor;
  const src = state.dbg.sources.find((s) => s.id === Number(id));
  if (!src) return;
  if (!(src.id in state.dbg.texts)) {
    try {
      const data = await api("GET", `/api/debug/source?file=${encodeURIComponent(elf)}&id=${src.id}`);
      state.dbg.texts[src.id] = data.text;
    } catch (e) {
      toast(e.message, true);
      return;
    }
  }
  if (state.dbg.sourcesFor !== elf) return;
  if (state.dbg.current !== src.id || !state.dbg.lines.length) {
    state.dbg.current = src.id;
    prefs.set("dbgsrc." + elf, src.id);
    buildCode(src, state.dbg.texts[src.id]);
    $("sel-dbg-source").value = src.id;
    $("dbg-source-path").textContent = src.rel || src.name;
  }
  updateCodeMarks();
  if (scrollLine) scrollToLine(scrollLine);
}

function scrollToLine(n) {
  const row = state.dbg.lines[n - 1];
  if (!row) return;
  const code = $("dbg-code");
  const top = row.offsetTop - code.offsetTop;
  if (top < code.scrollTop + 20 || top > code.scrollTop + code.clientHeight - 40) {
    code.scrollTop = Math.max(0, top - code.clientHeight / 3);
  }
}

// Breakpoints que se ven en el código: los de la sesión o, sin sesión, los guardados para la próxima.
function visibleBreakpoints() {
  const st = dbgStatus();
  if (st.active) return { list: st.breakpoints || [], pending: false };
  return { list: pendingFor(dbgElf()), pending: true };
}

function updateCodeMarks() {
  const rows = state.dbg.lines;
  if (!rows || !rows.length) return;
  for (const n of state.dbg.marked) {
    const row = rows[n - 1];
    if (row) row.classList.remove("bp", "pending", "cur");
  }
  state.dbg.marked = new Set();
  const cur = state.dbg.current;
  const { list, pending } = visibleBreakpoints();
  for (const b of list) {
    if (b.source_id !== cur || !rows[b.line - 1]) continue;
    rows[b.line - 1].classList.add(pending ? "pending" : "bp");
    state.dbg.marked.add(b.line);
  }
  const st = dbgStatus();
  const f = st.frame;
  if (st.active && st.state === "stopped" && f && f.source_id === cur && rows[f.line - 1]) {
    rows[f.line - 1].classList.add("cur");
    state.dbg.marked.add(f.line);
  }
}

async function toggleBreakpoint(line) {
  const src = currentSource();
  if (!src) return;
  if (!dbgActive()) {
    const elf = dbgElf();
    const list = pendingFor(elf);
    const i = list.findIndex((b) => b.source_id === src.id && b.line === line);
    if (i >= 0) list.splice(i, 1); else list.push({ source_id: src.id, line });
    setPending(elf, list);
    updateCodeMarks();
    renderDebug();
    return;
  }
  try {
    applyDebugStatus(await api("POST", "/api/debug/breakpoint", { source_id: src.id, line }));
  } catch (e) {
    toast(e.message, true);
  }
}

function canDebug() {
  const chip = currentChip();
  return !!(chip && chip.methods.includes("swd") && state.method === "swd");
}

async function startDebug() {
  const chip = currentChip();
  const file = state.file;
  if (!file || file.kind !== "elf") { toast("Elegí un .elf en la pestaña Archivos", true); return; }
  if (!canDebug()) { toast("La depuración es para STM32 por ST-Link (SWD)", true); return; }
  const mode = $("sel-dbg-mode").value;
  prefs.set("dbgmode", mode);
  $("btn-dbg-start").disabled = true;
  try {
    const st = await api("POST", "/api/debug/start", {
      chip: chip.id, file: file.path, mode,
      interface: $("sel-interface").value || null,
      probe_serial: $("sel-probe").value || null,
    });
    applyDebugStatus(st);
    // Breakpoints marcados antes de iniciar.
    const pend = pendingFor(file.path);
    for (const b of pend) {
      try { applyDebugStatus(await api("POST", "/api/debug/breakpoint", b)); }
      catch (e) { toast(`Breakpoint en la línea ${b.line}: ${e.message}`, true); }
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    renderDebug();
  }
}

async function stopDebug() {
  try { applyDebugStatus(await api("POST", "/api/debug/stop")); } catch (e) { toast(e.message, true); }
}

async function dbgControl(action) {
  // Paso y Entrar se repiten la cantidad elegida (×1, ×5, ×10, ×50, ×100).
  const count = (action === "next" || action === "step") ? parseInt($("sel-dbg-count").value, 10) || 1 : 1;
  try { applyDebugStatus(await api("POST", "/api/debug/control", { action, count })); } catch (e) { toast(e.message, true); }
}

function valueMap(st) {
  const m = {};
  for (const v of st.locals || []) m["l:" + v.name] = v.value;
  for (const v of st.globals || []) m["g:" + v.name] = v.value;
  for (const v of st.watches || []) m["w:" + v.expr] = v.value;
  return m;
}

function applyDebugStatus(st) {
  const prev = dbgStatus();
  // Al terminar la sesión, sus breakpoints quedan guardados para la próxima.
  if (prev.active && !st.active && prev.elf && prev.breakpoints) {
    setPending(prev.elf, prev.breakpoints.map((b) => ({ source_id: b.source_id, line: b.line })));
  }
  if (st.state === "stopped") {
    state.dbg.changed = new Set();
    const old = state.dbg.lastValues || {};
    const now = valueMap(st);
    if (prev.active) for (const [k, v] of Object.entries(now)) if (k in old && old[k] !== v) state.dbg.changed.add(k);
    state.dbg.lastValues = now;
  }
  state.dbg.status = st;
  const elf = dbgElf();
  if (state.tab === "debug" && elf !== state.dbg.sourcesFor) {
    loadDebugSources(true);
  } else if (st.state === "stopped" && st.frame && st.frame.source_id != null && state.tab === "debug") {
    showSource(st.frame.source_id, st.frame.line);
  }
  renderDebug();
  renderGdb();
}

function editValue(td, name, value) {
  if (td.querySelector("input")) return;
  const input = el("input", { type: "text", value: value || "", spellcheck: "false", autocomplete: "off" });
  td.replaceChildren(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const text = input.value.trim();
    if (save && text && text !== value) {
      try { applyDebugStatus(await api("POST", "/api/debug/set", { name, value: text })); return; }
      catch (e) { toast(e.message, true); }
    }
    renderDebug();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(false));
}

function varRows(items, prefix, editable) {
  if (!items.length) return [el("tr", {}, el("td", { class: "empty", colspan: 2 }, "—"))];
  const st = dbgStatus();
  const canEdit = st.active && st.state === "stopped";
  return items.map((v) => {
    const isErr = v.value === undefined && v.error;
    const changed = state.dbg.changed && state.dbg.changed.has(prefix + v.name);
    const simple = v.value !== undefined && !/^[{[]/.test(v.value);
    const td = el("td", {
      class: "v" + (isErr ? " err" : "") + (changed ? " changed" : "") + (editable && canEdit && simple ? " editable" : ""),
      title: editable && canEdit && simple ? "Clic para cambiar el valor" : (v.type || ""),
    }, isErr ? v.error : (v.value !== undefined ? v.value : "—"));
    if (editable && canEdit && simple) td.addEventListener("click", () => editValue(td, v.name, v.value));
    return el("tr", {}, el("td", { class: "n", title: v.type || "" }, v.name), td);
  });
}

function renderDebug() {
  if (!state.dbg) return;
  const st = dbgStatus();
  const active = !!st.active;
  const running = active && (st.state === "running" || !!st.steps);
  const stopped = active && st.state === "stopped" && !st.steps;
  const f = st.frame;

  // Estado
  const dot = $("dbg-state").querySelector(".dot");
  dot.className = "dot" + ({ stopped: " ok", running: " busy", starting: " busy", error: " err" }[st.state] || "");
  let label = DBG_LABELS[st.state] || st.state;
  if (st.steps) label = `Paso ${st.steps.done} de ${st.steps.total}…`;
  else if (stopped && f) label = `Detenido en ${f.func}()` + (f.file ? ` · ${f.file}:${f.line}` : "") + (st.stop_reason ? ` (${st.stop_reason})` : "");
  $("dbg-state").querySelector("span").textContent = label;
  $("dbg-state").title = label;

  // Botones
  const elfOk = !!(state.file && state.file.kind === "elf");
  $("btn-dbg-start").hidden = active;
  $("btn-dbg-start").disabled = !elfOk || !canDebug() || !!state.job || st.state === "starting";
  $("btn-dbg-stop").hidden = !(active || st.state === "error");
  $("sel-dbg-mode").disabled = active;
  for (const btn of document.querySelectorAll(".dbg-ctl")) {
    const a = btn.dataset.dbg;
    btn.disabled = !(a === "pause" ? running : (a === "reset" ? active && (stopped || running) : stopped));
  }

  // Nota
  const note = $("dbg-note");
  let text = "";
  let isError = false;
  if (st.message) { text = st.message; isError = st.state === "error"; }
  else if (!active && !elfOk) text = "Elegí el .elf a depurar en la pestaña Archivos (con su placa asignada).";
  else if (!active && !canDebug()) text = "La depuración es para STM32 por ST-Link (SWD): elegí esa placa y ese método.";
  else if (!active) text = "Marcá breakpoints con un clic en el número de línea y tocá Iniciar depuración. "
    + "“Grabar y parar en main” graba el .elf y frena al principio de main().";
  else if (running) text = "El programa corre en el micro. Tocá Pausar (F6) para ver dónde está, o esperá un breakpoint.";
  if (state.dbg.missing && state.dbg.missing.length && !text) {
    text = `No se encontraron ${state.dbg.missing.length} archivos fuente (p. ej. ${state.dbg.missing[0]}): subí el proyecto en .zip con las fuentes.`;
  }
  note.hidden = !text;
  note.textContent = text;
  note.className = "note" + (isError ? " error" : "");

  // Paneles
  $("dbg-locals").replaceChildren(...varRows(st.locals || [], "l:", true));
  $("dbg-globals").replaceChildren(...varRows(st.globals || [], "g:", true));
  const watches = st.watches || [];
  $("dbg-watches").replaceChildren(...watches.map((w, i) => {
    const changed = state.dbg.changed && state.dbg.changed.has("w:" + w.expr);
    const value = w.value !== undefined ? w.value : (w.error || "—");
    return el("tr", {},
      el("td", { class: "n" }, w.expr),
      el("td", { class: "v" + (w.error ? " err" : "") + (changed ? " changed" : "") }, value),
      el("td", { class: "x" }, el("button", { class: "icon-btn", type: "button", title: "Quitar",
        "aria-label": `Dejar de vigilar ${w.expr}`,
        onclick: () => api("DELETE", "/api/debug/watch?index=" + i).then(applyDebugStatus).catch((e) => toast(e.message, true)) }, icon("x"))));
  }));
  $("dbg-stack").replaceChildren(...(st.stack || []).map((fr) => el("li", {
    class: fr.level === 0 ? "top" : "",
    title: fr.addr || "",
    onclick: () => { if (fr.source_id != null) showSource(fr.source_id, fr.line); },
  }, el("span", {}, fr.func + "()"), el("small", {}, fr.file ? `${fr.file}:${fr.line}` : (fr.addr || "")))));
  const { list, pending } = visibleBreakpoints();
  $("dbg-bps").replaceChildren(...(list.length ? list.map((b) => {
    const src = state.dbg.sources.find((s) => s.id === b.source_id);
    const name = b.file || (src ? src.base : "?");
    return el("li", { onclick: () => showSource(b.source_id, b.line), title: pending ? "Se aplica al iniciar la depuración" : "" },
      el("span", {}, `${pending ? "○" : "●"} ${name}:${b.line}`),
      el("span", {}, b.hits ? el("small", {}, `${b.hits}×  `) : null,
        el("button", { class: "icon-btn", type: "button", title: "Sacar",
          "aria-label": `Sacar el breakpoint de ${name}:${b.line}`,
          onclick: (ev) => { ev.stopPropagation(); removeBreakpoint(b); } }, icon("x"))));
  }) : [el("li", { class: "muted" }, el("small", {}, "Clic en un número de línea para agregar"))]));
  $("dbg-regs").replaceChildren(...(st.registers || []).map((r) => el("tr", {},
    el("td", { class: "n" }, r.name), el("td", { class: "v" }, r.value))));
  updateCodeMarks();
}

async function removeBreakpoint(b) {
  if (!dbgActive()) {
    const elf = dbgElf();
    setPending(elf, pendingFor(elf).filter((x) => !(x.source_id === b.source_id && x.line === b.line)));
    renderDebug();
    return;
  }
  try { applyDebugStatus(await api("POST", "/api/debug/breakpoint", { source_id: b.source_id, line: b.line })); }
  catch (e) { toast(e.message, true); }
}

async function clearBreakpoints() {
  if (!dbgActive()) { setPending(dbgElf(), []); renderDebug(); return; }
  try { applyDebugStatus(await api("POST", "/api/debug/breakpoints/clear")); } catch (e) { toast(e.message, true); }
}

function bindDebugUI() {
  $("sel-dbg-mode").value = prefs.get("dbgmode", "load");
  $("btn-dbg-start").addEventListener("click", startDebug);
  $("btn-dbg-stop").addEventListener("click", stopDebug);
  for (const btn of document.querySelectorAll(".dbg-ctl")) btn.addEventListener("click", () => dbgControl(btn.dataset.dbg));
  $("sel-dbg-count").value = String(prefs.get("dbgcount", 1));
  $("sel-dbg-count").addEventListener("change", (ev) => prefs.set("dbgcount", parseInt(ev.target.value, 10) || 1));
  $("sel-dbg-source").addEventListener("change", (ev) => showSource(Number(ev.target.value)));
  $("btn-dbg-clear-bps").addEventListener("click", (ev) => { ev.preventDefault(); clearBreakpoints(); });
  $("dbg-code").addEventListener("click", (ev) => {
    const target = ev.target.closest(".gut, .ln");
    const row = ev.target.closest(".cl");
    if (target && row) toggleBreakpoint(Number(row.dataset.line));
  });
  $("dbg-watch-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("inp-watch");
    if (!input.value.trim()) return;
    try { applyDebugStatus(await api("POST", "/api/debug/watch", { expr: input.value })); input.value = ""; }
    catch (e) { toast(e.message, true); }
  });
  // Atajos como en los IDE (sólo con la pestaña Depurar abierta y fuera de un campo de texto).
  document.addEventListener("keydown", (ev) => {
    if (state.tab !== "debug" || !dbgActive() || /^(INPUT|SELECT|TEXTAREA)$/.test(ev.target.tagName)) return;
    const keys = { F5: "continue", F6: "pause", F10: "next", F11: ev.shiftKey ? "finish" : "step" };
    const action = keys[ev.key];
    if (!action) return;
    ev.preventDefault();
    const btn = document.querySelector(`.dbg-ctl[data-dbg="${action}"]`);
    if (btn && !btn.disabled) dbgControl(action);
  });
}

// --------------------------------------------------------------------- log

function appendLog(ev) {
  const out = $("log-out");
  const stick = $("chk-log-auto").checked && (out.scrollHeight - out.scrollTop - out.clientHeight < 40);
  let cls = "";
  if (ev.level === "cmd") cls = "l-cmd";
  else if (ev.level === "error") cls = "l-error";
  else if (ev.level === "ok") cls = "l-ok";
  else if (ev.level === "warn") cls = "l-warn";
  else if (ev.source === "gdb") cls = "l-gdb";
  else if (ev.source === "app") cls = "l-app";
  const line = el("div", { class: cls },
    el("span", { class: "ts" }, fmtTime(ev.ts) + " "),
    ev.source && ev.source !== "app" ? el("span", { class: "ts" }, `[${ev.source}] `) : null,
    ev.line);
  out.append(line);
  while (out.childElementCount > MAX_LOG_LINES) out.firstElementChild.remove();
  if (stick) out.scrollTop = out.scrollHeight;
}

// ------------------------------------------------------------ monitor serie

async function loadSerial() {
  try {
    const s = await api("GET", "/api/serial");
    const port = $("sel-port");
    const prevPort = s.port || port.value || prefs.get("serial.port", "");
    // RTT: el "printf" del programa por el ST-Link, sin adaptador (programas con RTT).
    const options = [el("option", { value: "RTT" }, "RTT — por el ST-Link (programa con RTT, sin adaptador)")];
    options.push(...(s.ports.length
      ? s.ports.map((p) => el("option", { value: p.device }, `${p.device} — ${p.description}`))
      : [el("option", { value: "", disabled: true }, "(sin puertos USB-serie)")]));
    port.replaceChildren(...options);
    if (prevPort === "RTT" || s.ports.some((p) => p.device === prevPort)) port.value = prevPort;
    else if (s.ports.length) port.value = s.ports[0].device;
    const baud = $("sel-baud");
    const prevBaud = String(s.baud || prefs.get("serial.baud", s.default_baud));
    baud.replaceChildren(...s.bauds.map((b) => el("option", { value: String(b) }, `${b} baud`)));
    baud.value = s.bauds.map(String).includes(prevBaud) ? prevBaud : String(s.default_baud);
    state.serial = s;
    renderSerialStatus();
  } catch (e) {
    toast(e.message, true);
  }
}

function renderSerialStatus() {
  const s = state.serial;
  $("btn-serial-open").disabled = s.open;
  $("btn-serial-close").disabled = !s.open;
  $("sel-port").disabled = s.open;
  $("sel-baud").disabled = s.open || $("sel-port").value === "RTT";
}

function appendSerial(text, meta) {
  const out = $("serial-out");
  const stick = $("chk-serial-auto").checked && (out.scrollHeight - out.scrollTop - out.clientHeight < 40);
  if (meta) out.append(el("span", { class: "meta" }, text));
  else out.append(document.createTextNode(text));
  if (out.textContent.length > MAX_SERIAL_CHARS) {
    while (out.firstChild && out.textContent.length > MAX_SERIAL_CHARS * 0.8) out.firstChild.remove();
  }
  if (stick) out.scrollTop = out.scrollHeight;
}

// ------------------------------------------------------------------ historial

async function loadHistory() {
  try {
    const data = await api("GET", "/api/history");
    const labels = { ok: "OK", error: "Error", cancelled: "Cancelado" };
    const rows = data.entries.map((e) => el("tr", {},
      el("td", {}, fmtDate(e.started)),
      el("td", {}, e.description,
        el("small", {}, e.message + (e.warnings && e.warnings.length ? ` · ${e.warnings.length} aviso(s)` : ""))),
      el("td", {}, el("span", { class: "res " + e.state }, labels[e.state] || e.state)),
      el("td", {}, fmtDuration(e.duration_s))));
    if (!rows.length) rows.push(el("tr", {}, el("td", { colspan: "4", class: "empty-state" }, "Sin operaciones todavía")));
    $("history-table").querySelector("tbody").replaceChildren(...rows);
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------- sistema

function stat(key, value, ratio) {
  const node = el("div", { class: "stat" }, el("div", { class: "k" }, key), el("div", { class: "v" }, value));
  if (ratio != null) {
    const fill = el("i");
    fill.style.width = Math.min(100, Math.max(0, ratio * 100)).toFixed(0) + "%";
    node.append(el("div", { class: "bar" }, fill));
  }
  return node;
}

async function loadSystem() {
  try {
    const s = await api("GET", "/api/system");
    const mem = s.memory, root = s.disk_root, proj = s.disk_projects;
    const temps = s.temperatures && s.temperatures.length
      ? s.temperatures.map((t) => `${t.celsius.toFixed(1)} °C (${t.name})`).join(", ") : "No disponible";
    const ips = s.addresses && s.addresses.length
      ? s.addresses.map((a) => `${a.interface}: ${a.ip}`).join("\n") : "—";
    $("sys-grid").replaceChildren(
      stat("Equipo", `${s.hostname} · kernel ${s.kernel}`),
      stat("IP Tailscale", s.tailscale_ip || "No detectada"),
      stat("Direcciones", ips),
      stat("Encendida hace", fmtUptime(s.uptime_s)),
      stat("Temperatura", temps),
      stat("Carga (1/5/15 min)", s.load ? s.load.map((x) => x.toFixed(2)).join(" / ") : "—"),
      stat("RAM", mem ? `${fmtSize(mem.used)} de ${fmtSize(mem.total)}` : "—", mem ? mem.used / mem.total : null),
      stat("Disco (/)", root ? `${fmtSize(root.used)} de ${fmtSize(root.total)}` : "—", root ? root.used / root.total : null),
      stat("Disco (proyectos)", proj ? `${fmtSize(proj.free)} libres` : "—", proj ? proj.used / proj.total : null),
      stat("OpenOCD", el("code", {}, s.versions.openocd || "no detectado")),
      stat("dfu-util", el("code", {}, s.versions.dfu_util || "no detectado")),
      stat("esptool", el("code", {}, s.versions.esptool || "no detectado")),
      stat("avrdude", el("code", {}, s.versions.avrdude || "no detectado")),
      stat("MAC (" + (s.mac ? s.mac.interface : "eth0") + ")", s.mac
        ? `${s.mac.address} · ${s.mac.fixed ? "fija" : "cambia en cada arranque (sudo bash scripts/fijar_mac.sh)"}`
        : "—"),
    );
    $("pill-host").querySelector("span").textContent = `${s.hostname}${s.tailscale_ip ? " · " + s.tailscale_ip : ""}`;
  } catch (e) {
    /* se reintenta en el próximo ciclo */
  }
}

// ------------------------------------------------------------------ USB

const USB_ACTIONS = {
  cycle: { label: "Reiniciar", confirm: "se apaga 3 s y vuelve a prender" },
  off: { label: "Apagar", confirm: "queda SIN energía hasta que toques Prender" },
  on: { label: "Prender", confirm: null },
};

function usbDeviceText(d) {
  if (!d) return "";
  const extra = [d.vid_pid].concat(d.serial_ports || [], d.network || []).join(" · ");
  return [el("div", {}, d.description + (d.product && !d.description.includes(d.product) ? ` (${d.product})` : "")),
          el("small", { class: "muted" }, extra)];
}

async function usbAction(hub, port, action) {
  const info = USB_ACTIONS[action];
  const who = port.device ? port.device.description : (port.raw || "puerto vacío");
  if (info.confirm) {
    const ok = await confirmDialog(`${info.label} USB ${hub.location}, puerto ${port.port}`,
      `${who}: ${info.confirm}.` + (hub.switching !== "ppps" ? " Este hub corta TODOS sus puertos juntos." : ""),
      info.label, action === "off");
    if (!ok) return;
  }
  try {
    renderUsb(await api("POST", "/api/usb/power", { location: hub.location, port: port.port, action }));
    toast(`USB ${hub.location}.${port.port}: ${info.label.toLowerCase()} listo`);
  } catch (e) {
    toast(e.message, true);
  }
}

function renderUsb(data) {
  const power = data.power || {};
  const box = $("usb-power");
  if (!power.available) {
    box.replaceChildren(el("p", { class: "note" }, "Alimentación por puerto: " + (power.message || "no disponible")));
  } else {
    box.replaceChildren(...power.hubs.map((hub) => {
      const rows = hub.ports.map((p) => {
        const state = !p.powered ? "Apagado" : (p.connected ? "Prendido" : "Vacío");
        const buttons = p.blocked ? el("small", { class: "muted" }, p.blocked)
          : el("div", { class: "usb-actions" }, ...p.actions
            .filter((a) => (a === "on" ? !p.powered : (a === "off" ? p.powered : true)))
            .map((a) => el("button", { class: "btn small" + (a === "off" ? " danger-outline" : ""), type: "button",
              onclick: () => usbAction(hub, p, a) }, USB_ACTIONS[a].label)));
        return el("tr", { class: p.powered ? "" : "off" }, el("td", {}, String(p.port)),
          el("td", {}, ...(p.device ? usbDeviceText(p.device) : [p.raw || "—"])), el("td", {}, state), el("td", {}, buttons));
      });
      return el("div", {},
        el("div", { class: "usb-hub" }, `Alimentación — hub ${hub.location} `,
          el("small", {}, `${hub.description} · ${hub.switching === "ppps" ? "corte por puerto" : "corta todos los puertos juntos"}`)),
        el("table", { class: "table usb-table" },
          el("thead", {}, el("tr", {}, el("th", {}, "Puerto"), el("th", {}, "Dispositivo"), el("th", {}, "Estado"), el("th", {}, ""))),
          el("tbody", {}, ...rows)));
    }));
  }
  const devices = (data.devices || []).filter((d) => !d.is_hub);
  $("usb-table").replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, "Ubicación"), el("th", {}, "Dispositivo"), el("th", {}, "Velocidad"))),
    el("tbody", {}, ...(devices.length ? devices.map((d) => el("tr", { class: d.vid_pid === "0483:df11" ? "dfu" : "" },
      el("td", {}, el("code", {}, d.path)), el("td", {}, ...usbDeviceText(d)),
      el("td", {}, d.speed_mbps ? `${d.speed_mbps} Mbit/s` : "—")))
      : [el("tr", {}, el("td", { colspan: 3 }, "No hay dispositivos USB conectados"))])));
}

async function loadUsb() {
  try { renderUsb(await api("GET", "/api/usb")); } catch (e) { /* se reintenta */ }
}

async function powerAction(kind) {
  const isOff = kind === "poweroff";
  const ok = await confirmDialog(
    isOff ? "Apagar la BeagleBoard" : "Reiniciar la BeagleBoard",
    isOff ? "La placa se apaga y NO se puede volver a encender por software: alguien tiene que " +
      "desconectar y reconectar la alimentación.\n\n¿Apagar ahora?"
      : "La placa se reinicia (1 a 2 minutos sin acceso). ¿Reiniciar ahora?",
    isOff ? "Apagar" : "Reiniciar", true);
  if (!ok) return;
  try {
    await api("POST", "/api/system/" + kind, { confirm: true });
  } catch (e) {
    toast(e.message, true);
    return;
  }
  if (events) events.close();
  $("power-screen").hidden = false;
  if (isOff) {
    $("power-title").textContent = "Apagando la placa";
    $("power-text").textContent = "Esperá ~30 s antes de cortar la alimentación; la placa no se puede volver a encender por software.";
    let left = 30;
    const tick = () => {
      $("power-countdown").textContent = left > 0 ? `${left} s` : "Ya podés cortar la alimentación.";
      if (left-- > 0) setTimeout(tick, 1000);
    };
    tick();
  } else {
    $("power-title").textContent = "Reiniciando la placa";
    $("power-text").textContent = "La página se vuelve a conectar sola cuando la placa esté de nuevo en línea (1 a 2 minutos).";
    const started = Date.now();
    const poll = async () => {
      $("power-countdown").textContent = `${Math.round((Date.now() - started) / 1000)} s`;
      if (Date.now() - started > 10000) {
        try {
          const r = await fetch("/api/me");
          if (r.ok || r.status === 401) { location.reload(); return; }
        } catch (e) { /* todavía no responde */ }
      }
      setTimeout(poll, 3000);
    };
    setTimeout(poll, 1000);
  }
}

// ---------------------------------------------------------- eventos (SSE)

let events = null;
let bootTimer = null;

function connectEvents() {
  events = new EventSource("/api/events");
  events.onmessage = (msg) => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch (e) { return; }
    handleEvent(ev);
  };
  events.onerror = async () => {
    // EventSource reintenta solo; si la sesión venció, se va al login.
    try { await api("GET", "/api/me"); } catch (e) { /* api() redirige si es 401 */ }
  };
}

function handleEvent(ev) {
  switch (ev.type) {
    case "snapshot":
      $("log-out").replaceChildren();
      $("serial-out").replaceChildren();
      state.job = ev.job && ev.job.state === "running" ? ev.job : null;
      state.gdb = ev.gdb;
      state.serial = Object.assign(state.serial || {}, ev.serial);
      if (ev.debug) applyDebugStatus(ev.debug);
      renderJob();
      renderGdb();
      renderSerialStatus();
      break;
    case "log":
      appendLog(ev);
      break;
    case "job":
      if (ev.job.state === "running") {
        state.job = ev.job;
      } else {
        state.job = null;
        showResult(ev.job);
        loadHistory();
        if (state.method === "dfu") refreshProbes();
      }
      renderJob();
      break;
    case "prompt":
      handlePrompt(ev);
      break;
    case "gdb":
      state.gdb = ev.status;
      renderGdb();
      break;
    case "serial":
      appendSerial(ev.data, ev.meta);
      break;
    case "serial_status":
      state.serial = Object.assign(state.serial || {}, ev.status);
      renderSerialStatus();
      break;
    case "files_changed":
      loadFiles();
      // Pudieron llegar las fuentes del .elf que se está mirando (p. ej. un .zip).
      state.dbg.stale = true;
      if (state.tab === "debug") loadDebugSources(true);
      break;
    case "debug":
      applyDebugStatus(ev.status);
      break;
  }
}

function handlePrompt(ev) {
  const dlg = $("dlg-boot");
  clearInterval(bootTimer);
  if (!ev.open) {
    if (dlg.open) dlg.close();
    return;
  }
  $("boot-text").textContent = ev.message;
  const countdown = $("boot-countdown");
  countdown.textContent = "";
  if (ev.timeout_s) {
    const end = Date.now() + ev.timeout_s * 1000;
    const tick = () => {
      const left = Math.max(0, Math.round((end - Date.now()) / 1000));
      countdown.textContent = `Tiempo restante: ${left} s`;
      if (!left) clearInterval(bootTimer);
    };
    tick();
    bootTimer = setInterval(tick, 1000);
  }
  if (!dlg.open) dlg.showModal();
}

// ------------------------------------------------------------ pestañas / UI

const TAB_HASH = { files: "archivos", debug: "depurar", serial: "serie", history: "historial", system: "sistema" };
const HASH_TAB = Object.fromEntries(Object.entries(TAB_HASH).map(([k, v]) => [v, k]));

function showTab(name, opts) {
  if (!TAB_HASH[name]) name = "files";
  state.tab = name;
  prefs.set("tab", name);
  // La pestaña queda en la URL (#depurar...): se puede compartir y "atrás" vuelve a la anterior.
  if (!(opts && opts.fromHash) && location.hash !== "#" + TAB_HASH[name]) {
    history.pushState(null, "", "#" + TAB_HASH[name]);
  }
  for (const btn of document.querySelectorAll(".tabs button")) {
    const active = btn.dataset.tab === name;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
    btn.tabIndex = active ? 0 : -1;
  }
  for (const tab of document.querySelectorAll(".tab")) tab.hidden = tab.id !== "tab-" + name;
  if (name === "system") { loadSystem(); loadUsb(); }
  if (name === "history") loadHistory();
  if (name === "serial") loadSerial();
  document.querySelector(".col-right").classList.toggle("debugging", name === "debug");
  if (name === "debug") loadDebugSources();
}

function bindUI() {
  $("btn-logout").addEventListener("click", async () => {
    try { await api("POST", "/api/logout"); } finally { location.href = "/login.html"; }
  });
  $("sel-chip").addEventListener("change", (ev) => { state.chip = ev.target.value; onChipChange(false); });
  $("sel-interface").addEventListener("change", (ev) => prefs.set("iface." + state.chip, ev.target.value));
  $("sel-probe").addEventListener("change", (ev) => { prefs.set("probe", ev.target.value); renderProbes(); });
  $("btn-refresh-probes").addEventListener("click", refreshProbes);
  $("btn-refresh-ports").addEventListener("click", refreshProbes);
  $("sel-prog-port").addEventListener("change", (ev) => prefs.set("progport." + state.chip, ev.target.value));
  $("btn-refresh-dfu").addEventListener("click", refreshProbes);
  for (const btn of document.querySelectorAll("[data-action]")) {
    btn.addEventListener("click", () => busy(btn, () => runAction(btn.dataset.action)));
  }
  $("btn-cancel").addEventListener("click", () => api("POST", "/api/jobs/cancel").catch((e) => toast(e.message, true)));
  $("btn-boot-cancel").addEventListener("click", () => api("POST", "/api/jobs/cancel").catch((e) => toast(e.message, true)));
  $("dlg-boot").addEventListener("cancel", (ev) => ev.preventDefault());  // Esc no lo cierra
  $("confirm-ok").addEventListener("click", () => finishConfirm(true));
  $("confirm-cancel").addEventListener("click", () => finishConfirm(false));
  $("dlg-confirm").addEventListener("cancel", () => finishConfirm(false));   // tecla Esc
  $("btn-gdb-start").addEventListener("click", () => busy($("btn-gdb-start"), startGdb));
  $("btn-gdb-stop").addEventListener("click", () => busy($("btn-gdb-stop"), stopGdb));
  $("btn-copy-gdb").addEventListener("click", async () => {
    toast((await copyText($("gdb-addr").textContent)) ? "Copiado" : "No se pudo copiar", false);
  });

  // Archivos y subida
  const drop = $("dropzone");
  $("btn-pick").addEventListener("click", (ev) => { ev.stopPropagation(); $("file-input").click(); });
  // Toda la zona abre el selector (salvo los controles de adentro); con el teclado, Enter o Espacio.
  drop.addEventListener("click", (ev) => { if (!ev.target.closest("select, input, label, button")) $("file-input").click(); });
  drop.addEventListener("keydown", (ev) => {
    if (ev.target === drop && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); $("file-input").click(); }
  });
  $("file-input").addEventListener("change", (ev) => { uploadFiles([...ev.target.files]); ev.target.value = ""; });
  $("btn-pick-dir").addEventListener("click", (ev) => { ev.stopPropagation(); $("dir-input").click(); });
  $("dir-input").addEventListener("change", (ev) => {
    const files = [...ev.target.files];
    ev.target.value = "";
    if (files.length) uploadEspProject(espFromFileList(files));
  });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault();
    // Hay que leer todo antes del primer await: después el navegador vacía dataTransfer.
    const entries = [...ev.dataTransfer.items].map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
    const files = [...ev.dataTransfer.files];
    const dir = entries.find((e) => e.isDirectory);
    if (dir) espFromDirEntry(dir).then(uploadEspProject);
    else uploadFiles(files);
  });
  // Evita que soltar un archivo fuera de la zona lo abra en el navegador.
  window.addEventListener("dragover", (ev) => ev.preventDefault());
  window.addEventListener("drop", (ev) => ev.preventDefault());
  $("inp-project").addEventListener("change", (ev) => prefs.set("project", ev.target.value));
  $("inp-project").value = prefs.get("project", "general");
  $("inp-filter").addEventListener("input", renderFiles);
  $("btn-refresh-files").addEventListener("click", loadFiles);

  // Serie
  $("btn-serial-open").addEventListener("click", async () => {
    const port = $("sel-port").value, baud = parseInt($("sel-baud").value, 10);
    if (!port) { toast("No hay puerto elegido", true); return; }
    prefs.set("serial.port", port);
    prefs.set("serial.baud", baud);
    if (port === "RTT") {
      const chip = currentChip();
      if (!state.file || state.file.kind !== "elf") {
        toast("Para RTT elegí en Archivos el .elf del programa que está corriendo (con RTT)", true);
        return;
      }
      try {
        const r = await api("POST", "/api/rtt/open", {
          chip: chip.id, file: state.file.path,
          interface: $("sel-interface").value || null, probe_serial: $("sel-probe").value || null,
        });
        state.serial = Object.assign(state.serial || {}, r.serial);
        state.gdb = r.gdb;
        renderSerialStatus();
        renderGdb();
      } catch (e) { toast(e.message, true); }
      return;
    }
    try { state.serial = await api("POST", "/api/serial/open", { port, baud }); renderSerialStatus(); }
    catch (e) { toast(e.message, true); }
  });
  $("btn-serial-close").addEventListener("click", async () => {
    try { state.serial = await api("POST", "/api/serial/close"); renderSerialStatus(); }
    catch (e) { toast(e.message, true); }
  });
  $("btn-serial-clear").addEventListener("click", () => $("serial-out").replaceChildren());
  $("sel-port").addEventListener("change", renderSerialStatus);
  $("serial-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("inp-serial");
    try {
      await api("POST", "/api/serial/send", { text: input.value, eol: EOLS[$("sel-eol").value] });
      input.value = "";
    } catch (e) {
      toast(e.message, true);
    }
  });

  // Log
  $("btn-log-clear").addEventListener("click", () => $("log-out").replaceChildren());
  $("btn-log-toggle").addEventListener("click", () => {
    const panel = $("log-panel");
    panel.classList.toggle("collapsed");
    const collapsed = panel.classList.contains("collapsed");
    $("btn-log-toggle").textContent = collapsed ? "Mostrar" : "Ocultar";
    prefs.set("logCollapsed", collapsed);
  });
  if (prefs.get("logCollapsed", false)) $("btn-log-toggle").click();

  // Pestañas y energía
  const tabButtons = [...document.querySelectorAll(".tabs button")];
  for (const btn of tabButtons) {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
    // Flechas, Inicio y Fin recorren las pestañas (patrón de pestañas de WAI-ARIA).
    btn.addEventListener("keydown", (ev) => {
      const i = tabButtons.indexOf(btn);
      const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabButtons.length - 1 }[ev.key];
      if (next === undefined) return;
      ev.preventDefault();
      const target = tabButtons[(next + tabButtons.length) % tabButtons.length];
      showTab(target.dataset.tab);
      target.focus();
    });
  }
  window.addEventListener("popstate", () => showTab(HASH_TAB[location.hash.slice(1)] || "files", { fromHash: true }));
  bindSplitter();
  $("btn-poweroff").addEventListener("click", () => powerAction("poweroff"));
  $("btn-reboot").addEventListener("click", () => powerAction("reboot"));
  $("btn-usb-refresh").addEventListener("click", loadUsb);
  bindDebugUI();
}

// --------------------------------------------------------- separador del log

function bindSplitter() {
  const col = document.querySelector(".col-right");
  const bar = $("splitter");
  const setHeight = (h) => {
    const max = Math.max(120, col.clientHeight - 220);
    const value = Math.round(Math.min(max, Math.max(90, h)));
    col.style.setProperty("--log-h", value + "px");
    bar.setAttribute("aria-valuenow", String(value));
    return value;
  };
  const saved = prefs.get("logHeight", null);
  if (saved) setHeight(saved);
  bar.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    bar.setPointerCapture(ev.pointerId);
    bar.classList.add("dragging");
    const bottom = $("log-panel").getBoundingClientRect().bottom;
    const move = (e) => setHeight(bottom - e.clientY - 6);
    const up = () => {
      bar.classList.remove("dragging");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      prefs.set("logHeight", $("log-panel").getBoundingClientRect().height);
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
  });
  bar.addEventListener("keydown", (ev) => {
    const step = { ArrowUp: 24, ArrowDown: -24 }[ev.key];
    if (!step) return;
    ev.preventDefault();
    prefs.set("logHeight", setHeight($("log-panel").getBoundingClientRect().height + step));
  });
}

// ------------------------------------------------------------------ arranque

async function init() {
  bindUI();
  try {
    const me = await api("GET", "/api/me");
    $("user-name").textContent = me.user;
  } catch (e) {
    return;   // api() ya redirigió al login
  }
  state.config = await api("GET", "/api/config");
  renderConfig();
  connectEvents();
  await Promise.all([refreshProbes(), loadFiles(), loadSerial(), loadSystem()]);
  const savedFile = prefs.get("file", null);
  if (!state.file && savedFile) {
    const f = state.projects.flatMap((p) => p.files).find((x) => x.path === savedFile);
    if (f) { setFile(f); renderFiles(); }
  }
  const fromUrl = HASH_TAB[location.hash.slice(1)];
  showTab(fromUrl || prefs.get("tab", "files"), { fromHash: !!fromUrl });

  // La detección de OpenOCD/dfu-util corre en segundo plano al arrancar el servidor.
  setTimeout(async () => {
    try {
      const cfg = await api("GET", "/api/config");
      state.config.cfg_check = cfg.cfg_check;
      state.config.versions = cfg.versions;
      renderCfgCheck();
    } catch (e) { /* no crítico */ }
  }, 5000);

  // Refrescos periódicos livianos (sin dfu-util salvo con DFU elegido).
  setInterval(() => { if (!document.hidden && !state.job) refreshProbes(); }, 10000);
  setInterval(() => { if (!document.hidden) loadSystem(); if (!document.hidden && state.tab === "system") loadUsb(); }, 15000);
}

init();
