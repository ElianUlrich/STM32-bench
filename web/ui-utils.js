// Banco remoto — funciones puras de la interfaz (sin DOM ni red).
// En el navegador quedan en `window.UI`; en Node se prueban con:
//   node --test "tests/js/*.test.js"
(function (root) {
  "use strict";

  const STAGE_STEP = { connect: "Conectar", erase: "Borrar", write: "Escribir", verify: "Verificar", reset: "Reiniciar" };
  const STAGE_RUNNING = { connect: "Conectando", erase: "Borrando", write: "Escribiendo", verify: "Verificando",
    reset: "Reiniciando" };

  const pad = (n) => String(n).padStart(2, "0");

  // Fecha relativa corta para listas ("hace 5 min", "ayer", "04/10"). `ts` en segundos.
  function fmtAgo(ts, nowMs) {
    const now = nowMs === undefined ? Date.now() : nowMs;
    const diff = now / 1000 - ts;
    if (diff < 60) return "recién";                 // incluye relojes adelantados
    if (diff < 3600) return `hace ${Math.floor(diff / 60)} min`;
    if (diff < 86400) return `hace ${Math.floor(diff / 3600)} h`;
    if (diff < 2 * 86400) return "ayer";
    const d = new Date(ts * 1000);
    const year = new Date(now).getFullYear() !== d.getFullYear() ? "/" + String(d.getFullYear()).slice(2) : "";
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}${year}`;
  }

  // "STM32H503CB (WeAct Core Board)" -> "STM32H503CB".
  function shortChipName(name) {
    return String(name || "").replace(/\s*\(.*\)\s*$/, "").trim();
  }

  // Etiqueta corta de cada placa ({id: texto}): el nombre sin paréntesis, salvo que
  // dos placas queden iguales (p. ej. las dos variantes del Arduino Nano).
  function chipLabels(chips) {
    const counts = {};
    for (const c of chips || []) {
      const short = shortChipName(c.name);
      counts[short] = (counts[short] || 0) + 1;
    }
    const out = {};
    for (const c of chips || []) {
      const short = shortChipName(c.name);
      out[c.id] = counts[short] > 1 ? c.name : short;
    }
    return out;
  }

  // Cronómetro: 0:04, 1:05, 1:02:03.
  function fmtElapsed(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  }

  // Nombre de la etapa como paso ("Escribir") o en curso ("Escribiendo").
  function stageLabel(stage, running) {
    return (running ? STAGE_RUNNING : STAGE_STEP)[stage] || stage || "";
  }

  // "Escribiendo 45 % · archivo 3 de 3" / "Verificando…".
  function progressText(progress) {
    if (!progress || !progress.stage) return "";
    const base = stageLabel(progress.stage, true);
    const pct = progress.pct === null || progress.pct === undefined ? "…" : ` ${Math.floor(progress.pct)} %`;
    return base + pct + (progress.detail ? ` · ${progress.detail}` : "");
  }

  // [[etapa, "done" | "current" | "todo"], ...] para dibujar los pasos.
  function stepStates(progress) {
    if (!progress || !progress.stages) return [];
    const current = progress.stages.indexOf(progress.stage);
    return progress.stages.map((s, i) => [s, i < current ? "done" : (i === current ? "current" : "todo")]);
  }

  // Proyectos con sus archivos filtrados (sin modificar el original). El texto se busca
  // en la ruta y en el nombre de la placa asignada; onlyChip deja sólo los de `chip`.
  function filterProjects(projects, opts) {
    const text = String(opts.text || "").trim().toLowerCase();
    const names = opts.names || {};
    const keep = (f) => {
      if (opts.onlyChip && f.target !== opts.chip) return false;
      if (!text) return true;
      if (f.path.toLowerCase().includes(text)) return true;
      if (!f.target) return false;
      return f.target.toLowerCase().includes(text) || String(names[f.target] || "").toLowerCase().includes(text);
    };
    const out = [];
    for (const p of projects || []) {
      const files = p.files.filter(keep);
      if (files.length) out.push(Object.assign({}, p, { files }));
    }
    return out;
  }

  const api = { fmtAgo, shortChipName, chipLabels, fmtElapsed, stageLabel, progressText, stepStates, filterProjects };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.UI = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
