// Banco remoto — funciones puras de la interfaz (sin DOM ni red).
// En el navegador quedan en `window.UI`; en Node se prueban con:
//   node --test "tests/js/*.test.js"
(function (root) {
  "use strict";

  const STAGE_STEP = { connect: "Conectar", erase: "Borrar", write: "Escribir", verify: "Verificar", reset: "Reiniciar",
    read: "Leer" };
  const STAGE_RUNNING = { connect: "Conectando", erase: "Borrando", write: "Escribiendo", verify: "Verificando",
    reset: "Reiniciando", read: "Leyendo" };

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

  // Tamaño legible: 2.4 KB, 128 KB, 1.5 MB (sin ".0").
  function fmtBytes(n) {
    const trim = (x) => x.toFixed(1).replace(/\.0$/, "");
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${trim(n / 1024)} KB`;
    return `${trim(n / 1024 / 1024)} MB`;
  }

  // "2.4 KB de 128 KB (1.8 %)": cuánto de la flash está usado.
  function usedText(used, total) {
    const pct = total ? (used * 100) / total : 0;
    return `${fmtBytes(used)} de ${fmtBytes(total)} (${pct.toFixed(1).replace(/\.0$/, "")} %)`;
  }

  // Titular de una lectura del micro: tono, texto e ícono.
  function readSummary(report) {
    if (!report || !report.summary) return null;
    const look = { match: ["ok", "check"], partial: ["ok", "check"], unknown: ["warn", "alert"],
      empty: ["muted", "info"] }[report.summary.state] || ["muted", "info"];
    return { tone: look[0], title: report.summary.text, icon: look[1] };
  }

  // ---------------------------------------------------------------- gráficos

  // Marcas "redondas" (1, 2 o 5 por potencia de 10) para un eje de min a max: unas `count`.
  function niceTicks(min, max, count) {
    if (!(isFinite(min) && isFinite(max))) return [];
    if (max - min < 1e-12) {
      const pad = Math.abs(min) > 1e-12 ? Math.abs(min) * 0.1 : 1;
      min -= pad;
      max += pad;
    }
    const raw = (max - min) / Math.max(1, count);
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw * 0.999);
    for (;;) {
      const ticks = [];
      for (let v = Math.ceil(min / step - 1e-9) * step; v <= max + step * 1e-9; v += step) {
        ticks.push(Number((Math.abs(v) < step * 1e-9 ? 0 : v).toPrecision(12)));
      }
      if (ticks.length) return ticks;
      step /= 2;              // ningún múltiplo del paso cae en el rango: uno más fino
    }
  }

  const NUM = "[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?";
  const PAIR_RE = new RegExp(`([A-Za-z_][\\w.\\[\\]]*)\\s*[=:]\\s*(${NUM})`, "g");
  const ONLY_NUMBERS_RE = new RegExp(`^\\s*${NUM}(?:\\s*[,;\\s]\\s*${NUM})*\\s*[,;]?\\s*$`);

  // Números de una línea del monitor serie: "temp=25.1 hum=40" (con nombres) o "1.5, 3, 7".
  function parseSerialValues(line) {
    if (!line) return null;
    const names = [], values = [];
    for (const m of line.matchAll(PAIR_RE)) {
      names.push(m[1]);
      values.push(Number(m[2]));
    }
    if (names.length) return { names, values };
    if (!ONLY_NUMBERS_RE.test(line)) return null;
    return { names: null, values: line.match(new RegExp(NUM, "g")).map(Number) };
  }

  // Valor compacto para leyendas y tooltips.
  function fmtValue(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    if (Number.isInteger(v) && Math.abs(v) < 1e7) return String(v);
    if (Math.abs(v) >= 1e7 || (Math.abs(v) < 1e-3 && v !== 0)) return v.toExponential(3).replace(/\.?0+e/, "e");
    return String(Number(v.toPrecision(4)));
  }

  // CSV de las muestras ([t, v1, v2...]) para abrir en Excel o Python.
  function samplesCsv(names, samples) {
    const rows = [["t_s"].concat(names).join(",")];
    for (const s of samples) rows.push(s.map((v) => (v === null || v === undefined ? "" : String(v))).join(","));
    return rows.join("\n") + "\n";
  }

  const api = { fmtAgo, shortChipName, chipLabels, fmtElapsed, stageLabel, progressText, stepStates, filterProjects,
    fmtBytes, usedText, readSummary, niceTicks, parseSerialValues, fmtValue, samplesCsv };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.UI = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
