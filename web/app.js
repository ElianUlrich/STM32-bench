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
  dfu: [],
  dfuError: null,
  file: null,
  projects: [],
  job: null,
  gdb: { state: "stopped", active: false },
  serial: { open: false },
  tab: "files",
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

function currentChip() {
  return state.config.chips.find((c) => c.id === state.chip) || null;
}

function renderConfig() {
  const cfg = state.config;
  $("mock-badge").hidden = !cfg.mock;

  const errors = $("config-errors");
  errors.hidden = !cfg.config_errors.length;
  errors.textContent = cfg.config_errors.length ? "Error de configuración: " + cfg.config_errors.join(" · ") : "";
  renderCfgCheck();

  const sel = $("sel-chip");
  sel.replaceChildren(...cfg.chips.map((c) => el("option", { value: c.id }, `${c.name} — ${c.family}`)));
  const saved = prefs.get("chip", null);
  state.chip = cfg.chips.some((c) => c.id === saved) ? saved : (cfg.chips[0] && cfg.chips[0].id);
  sel.value = state.chip || "";

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
  updateMethodUI();
  if (!initial) refreshProbes();
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

function updateMethodUI() {
  const chip = currentChip();
  const cfg = state.config;
  const isDfu = state.method === "dfu";
  for (const btn of $("seg-method").querySelectorAll("button")) {
    const m = btn.dataset.method;
    const supported = chip && chip.methods.includes(m);
    btn.disabled = !supported;
    btn.title = supported ? "" : `${chip ? chip.name : "El chip"} no tiene configurado este método en chips.yaml`;
    btn.classList.toggle("active", m === state.method);
    btn.setAttribute("aria-checked", m === state.method ? "true" : "false");
  }
  $("swd-fields").hidden = isDfu;
  $("dfu-fields").hidden = !isDfu;
  $("leave-field").hidden = !isDfu;
  $("dfu-reason").hidden = !isDfu;
  $("dfu-reason").textContent = isDfu ? "Reset, Halt, Run y el servidor GDB no están disponibles por USB DFU: " +
    "el bootloader de ST solo escribe, lee y borra la flash, sin acceso al núcleo. Para depurar usá SWD." : "";

  // Dirección para .bin: por defecto la base de flash (o la dirección DFU del chip).
  const isBin = state.file && state.file.kind === "bin";
  $("addr-field").hidden = !isBin;
  if (chip) {
    $("inp-address").placeholder = isDfu && chip.dfu ? chip.dfu.address : chip.flash_base;
  }
  updateActionButtons();
  renderGdb();
}

function updateActionButtons() {
  const caps = state.config ? state.config.capabilities[state.method] : [];
  const busy = !!state.job;
  for (const btn of document.querySelectorAll("[data-action]")) {
    const action = btn.dataset.action;
    let reason = "";
    if (!caps.includes(action)) reason = state.config.dfu_unsupported_reason;
    else if (busy) reason = "Hay una operación en curso";
    else if (FILE_ACTIONS.includes(action) && !state.file) reason = "Elegí un archivo de firmware";
    else if (!state.chip) reason = "No hay chips configurados";
    btn.disabled = !!reason;
    btn.title = reason;
  }
}

// -------------------------------------------------------------- programadores

async function refreshProbes() {
  const withDfu = state.method === "dfu";
  try {
    const data = await api("GET", "/api/probes?dfu=" + (withDfu ? "1" : "0"));
    state.stlinks = data.stlinks;
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
    const files = project.files.filter((f) => !filter || f.path.toLowerCase().includes(filter));
    if (!files.length) continue;
    const head = el("div", { class: "project-head" },
      el("span", {}, project.name),
      el("span", { class: "tag" }, project.uploaded ? "subido desde la web" : (project.key ? "sincronizado" : "")));
    const rows = files.map((f) => {
      const row = el("div", {
        class: "file-row" + (state.file && state.file.path === f.path ? " selected" : "") + (f.usable ? "" : " unusable"),
        title: f.usable ? f.path : "Nombre con caracteres no permitidos: renombralo para poder usarlo",
        onclick: () => { if (f.usable) { setFile(f); renderFiles(); } },
      },
        el("span", { class: "kind " + f.kind }, f.kind),
        el("span", { class: "name" }, f.name, f.subdir ? el("span", { class: "sub" }, "  " + f.subdir) : null),
        el("span", { class: "size" }, fmtSize(f.size)),
        el("span", { class: "date" }, fmtDate(f.mtime)),
        f.deletable
          ? el("button", { class: "icon-btn", type: "button", title: "Borrar", "aria-label": "Borrar " + f.name,
            onclick: (ev) => { ev.stopPropagation(); deleteFile(f); } }, "✕")
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

function setFile(file) {
  state.file = file;
  const box = $("fw-selected");
  if (file) {
    box.className = "fw-selected";
    box.replaceChildren(el("span", { class: "kind " + file.kind }, file.kind), " ", file.path,
      el("span", { class: "muted" }, `  (${fmtSize(file.size)})`));
    prefs.set("file", file.path);
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

function uploadOne(file, project) {
  // XMLHttpRequest para poder mostrar el progreso de la subida.
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("project", project);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/files/upload");
    xhr.setRequestHeader("X-Bench", "1");
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        $("upload-status").textContent = `Subiendo ${file.name}… ${Math.round(ev.loaded / ev.total * 100)}%`;
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
  const maxBytes = state.config.uploads.max_size_mb * 1024 * 1024;
  let last = null;
  for (const file of fileList) {
    const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    if (!state.config.uploads.extensions.includes(ext)) {
      toast(`${file.name}: extensión no permitida (.elf, .hex, .bin)`, true);
      continue;
    }
    if (file.size > maxBytes) {
      toast(`${file.name}: supera ${state.config.uploads.max_size_mb} MB`, true);
      continue;
    }
    try {
      last = await uploadOne(file, project);
      $("upload-status").textContent = `✔ ${file.name} subido`;
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

// ------------------------------------------------------------------ acciones

const ACTION_CONFIRM = {
  erase: (chip) => ["Borrar flash completa", `Se borra TODA la flash de ${chip.name}. ¿Continuar?`, "Borrar", true],
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
  } else {
    body.dfu_serial = $("sel-dfu").value || null;
    body.leave = $("chk-leave").checked;
  }
  if (FILE_ACTIONS.includes(action)) {
    body.file = state.file.path;
    if (state.file.kind === "bin") body.address = $("inp-address").value.trim() || null;
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
  const icon = { ok: "✔", error: "✘", cancelled: "■" }[job.state] || "";
  const children = [el("strong", {}, `${icon} ${job.message}`), el("div", { class: "muted" }, job.description)];
  if (job.warnings && job.warnings.length) {
    children.push(el("ul", {}, ...job.warnings.map((w) => el("li", {}, w))));
  }
  box.replaceChildren(...children);
}

// --------------------------------------------------------------- servidor GDB

function renderGdb() {
  const g = state.gdb;
  const isDfu = state.method === "dfu";
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
  if (isDfu) note = "No disponible por USB DFU (sin acceso al núcleo). Elegí el método SWD para depurar.";
  else if (g.state === "error") note = g.last_error || "";
  else if (listening) note = `En GDB: target extended-remote ${g.connect}`;
  else note = `Puerto ${state.config ? state.config.gdb.port : 3333}, escucha en: ${state.config ? state.config.gdb.bind : ""}`;
  $("gdb-note").textContent = note;
  $("btn-gdb-start").disabled = isDfu || g.active || !!state.job;
  $("btn-gdb-start").title = isDfu ? state.config.dfu_unsupported_reason : "";
  $("btn-gdb-stop").disabled = !g.active;

  const pill = $("pill-gdb");
  pill.querySelector(".dot").className = "dot" + ({ listening: " ok", starting: " busy", error: " err" }[g.state] || "");
  pill.querySelector("span").textContent = g.active ? `GDB ${g.connect || ""}` : (g.state === "error" ? "GDB con error" : "GDB detenido");
}

async function startGdb() {
  const chip = currentChip();
  try {
    state.gdb = await api("POST", "/api/gdb/start", {
      chip: chip.id,
      interface: $("sel-interface").value || null,
      probe_serial: $("sel-probe").value || null,
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
    port.replaceChildren(...(s.ports.length
      ? s.ports.map((p) => el("option", { value: p.device }, `${p.device} — ${p.description}`))
      : [el("option", { value: "" }, "Sin puertos USB-serie")]));
    if (s.ports.some((p) => p.device === prevPort)) port.value = prevPort;
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
  $("sel-baud").disabled = s.open;
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
    );
    $("pill-host").querySelector("span").textContent = `${s.hostname}${s.tailscale_ip ? " · " + s.tailscale_ip : ""}`;
  } catch (e) {
    /* se reintenta en el próximo ciclo */
  }
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

function showTab(name) {
  state.tab = name;
  prefs.set("tab", name);
  for (const btn of document.querySelectorAll(".tabs button")) {
    btn.classList.toggle("active", btn.dataset.tab === name);
    btn.setAttribute("aria-selected", btn.dataset.tab === name ? "true" : "false");
  }
  for (const tab of document.querySelectorAll(".tab")) tab.hidden = tab.id !== "tab-" + name;
  if (name === "system") loadSystem();
  if (name === "history") loadHistory();
  if (name === "serial") loadSerial();
}

function bindUI() {
  $("btn-logout").addEventListener("click", async () => {
    try { await api("POST", "/api/logout"); } finally { location.href = "/login.html"; }
  });
  $("sel-chip").addEventListener("change", (ev) => { state.chip = ev.target.value; onChipChange(false); });
  $("sel-interface").addEventListener("change", (ev) => prefs.set("iface." + state.chip, ev.target.value));
  $("sel-probe").addEventListener("change", (ev) => { prefs.set("probe", ev.target.value); renderProbes(); });
  for (const btn of $("seg-method").querySelectorAll("button")) {
    btn.addEventListener("click", () => setMethod(btn.dataset.method));
  }
  $("btn-refresh-probes").addEventListener("click", refreshProbes);
  $("btn-refresh-dfu").addEventListener("click", refreshProbes);
  for (const btn of document.querySelectorAll("[data-action]")) {
    btn.addEventListener("click", () => runAction(btn.dataset.action));
  }
  $("btn-cancel").addEventListener("click", () => api("POST", "/api/jobs/cancel").catch((e) => toast(e.message, true)));
  $("btn-boot-cancel").addEventListener("click", () => api("POST", "/api/jobs/cancel").catch((e) => toast(e.message, true)));
  $("dlg-boot").addEventListener("cancel", (ev) => ev.preventDefault());  // Esc no lo cierra
  $("confirm-ok").addEventListener("click", () => finishConfirm(true));
  $("confirm-cancel").addEventListener("click", () => finishConfirm(false));
  $("dlg-confirm").addEventListener("cancel", () => finishConfirm(false));   // tecla Esc
  $("btn-gdb-start").addEventListener("click", startGdb);
  $("btn-gdb-stop").addEventListener("click", stopGdb);
  $("btn-copy-gdb").addEventListener("click", async () => {
    toast((await copyText($("gdb-addr").textContent)) ? "Copiado" : "No se pudo copiar", false);
  });

  // Archivos y subida
  const drop = $("dropzone");
  $("btn-pick").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (ev) => { uploadFiles([...ev.target.files]); ev.target.value = ""; });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => { ev.preventDefault(); uploadFiles([...ev.dataTransfer.files]); });
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
    try { state.serial = await api("POST", "/api/serial/open", { port, baud }); renderSerialStatus(); }
    catch (e) { toast(e.message, true); }
  });
  $("btn-serial-close").addEventListener("click", async () => {
    try { state.serial = await api("POST", "/api/serial/close"); renderSerialStatus(); }
    catch (e) { toast(e.message, true); }
  });
  $("btn-serial-clear").addEventListener("click", () => $("serial-out").replaceChildren());
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
  for (const btn of document.querySelectorAll(".tabs button")) {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
  }
  $("btn-poweroff").addEventListener("click", () => powerAction("poweroff"));
  $("btn-reboot").addEventListener("click", () => powerAction("reboot"));
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
  showTab(prefs.get("tab", "files"));

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
  setInterval(() => { if (!document.hidden) loadSystem(); }, 15000);
}

init();
