// Funciones puras de la interfaz (sin DOM). Correr con:  node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const UI = require("../../web/ui-utils.js");

const NOW = Date.UTC(2026, 9, 11, 15, 0, 0);   // 11/10/2026 15:00 UTC
const ago = (s) => NOW / 1000 - s;

test("fmtAgo: fechas relativas cortas", () => {
  assert.equal(UI.fmtAgo(ago(20), NOW), "recién");
  assert.equal(UI.fmtAgo(ago(5 * 60), NOW), "hace 5 min");
  assert.equal(UI.fmtAgo(ago(59 * 60), NOW), "hace 59 min");
  assert.equal(UI.fmtAgo(ago(3 * 3600), NOW), "hace 3 h");
  assert.equal(UI.fmtAgo(ago(30 * 3600), NOW), "ayer");
  assert.match(UI.fmtAgo(ago(5 * 86400), NOW), /^\d{2}\/\d{2}$/);
  // Reloj de la placa adelantado: no "hace -3 min".
  assert.equal(UI.fmtAgo(ago(-180), NOW), "recién");
});

test("shortChipName: el nombre sin la aclaración entre paréntesis", () => {
  assert.equal(UI.shortChipName("STM32H503CB (WeAct Core Board)"), "STM32H503CB");
  assert.equal(UI.shortChipName("ESP32 (DevKitC / WROOM-32 / WROVER)"), "ESP32");
  assert.equal(UI.shortChipName("Arduino Uno (ATmega328P)"), "Arduino Uno");
  assert.equal(UI.shortChipName("ESP32-P4"), "ESP32-P4");
});

test("fmtElapsed: cronómetro m:ss", () => {
  assert.equal(UI.fmtElapsed(0), "0:00");
  assert.equal(UI.fmtElapsed(4.7), "0:04");
  assert.equal(UI.fmtElapsed(65), "1:05");
  assert.equal(UI.fmtElapsed(3723), "1:02:03");
  assert.equal(UI.fmtElapsed(-2), "0:00");
});

test("progressText: etapa, porcentaje y detalle", () => {
  assert.equal(UI.progressText({ stage: "write", pct: 45.3, detail: "archivo 3 de 3" }),
    "Escribiendo 45 % · archivo 3 de 3");
  assert.equal(UI.progressText({ stage: "verify", pct: null, detail: null }), "Verificando…");
  assert.equal(UI.progressText({ stage: "connect", pct: null }), "Conectando…");
  assert.equal(UI.progressText(null), "");
  assert.equal(UI.stageLabel("erase"), "Borrar");
  assert.equal(UI.stageLabel("reset"), "Reiniciar");
});

test("stepStates: hechas, en curso y pendientes", () => {
  assert.deepEqual(UI.stepStates({ stages: ["connect", "write", "verify"], stage: "write" }),
    [["connect", "done"], ["write", "current"], ["verify", "todo"]]);
  assert.deepEqual(UI.stepStates(null), []);
});

const PROJECTS = [
  { key: "h503", name: "h503", files: [
    { path: "h503/a.elf", name: "a.elf", target: "stm32h503cb" },
    { path: "h503/b.bin", name: "b.bin", target: null }] },
  { key: "pedal", name: "pedal", files: [
    { path: "pedal/build/pedal.bin", name: "pedal.bin", target: "esp32-p4" }] },
];
const NAMES = { "stm32h503cb": "STM32H503CB (WeAct Core Board)", "esp32-p4": "ESP32-P4" };

test("filterProjects: por texto (ruta o placa) y sólo la placa elegida", () => {
  const all = UI.filterProjects(PROJECTS, { text: "", chip: "stm32h503cb", onlyChip: false, names: NAMES });
  assert.equal(all.length, 2);
  const only = UI.filterProjects(PROJECTS, { text: "", chip: "stm32h503cb", onlyChip: true, names: NAMES });
  assert.deepEqual(only.map((p) => p.files.map((f) => f.path)), [["h503/a.elf"]]);
  const byText = UI.filterProjects(PROJECTS, { text: "PEDAL", chip: null, onlyChip: false, names: NAMES });
  assert.deepEqual(byText.map((p) => p.key), ["pedal"]);
  const byChip = UI.filterProjects(PROJECTS, { text: "esp32-p4", chip: null, onlyChip: false, names: NAMES });
  assert.deepEqual(byChip.map((p) => p.key), ["pedal"]);
  const byChipName = UI.filterProjects(PROJECTS, { text: "weact", chip: null, onlyChip: false, names: NAMES });
  assert.deepEqual(byChipName.map((p) => p.files.length), [1]);
  // El original no se toca.
  assert.equal(PROJECTS[0].files.length, 2);
});

test("chipLabels: nombre corto sólo si no se repite", () => {
  const labels = UI.chipLabels([
    { id: "h503", name: "STM32H503CB (WeAct Core Board)" },
    { id: "nano", name: "Arduino Nano (ATmega328P, bootloader nuevo)" },
    { id: "nano-old", name: "Arduino Nano (ATmega328P, bootloader viejo / clones)" },
    { id: "p4", name: "ESP32-P4" },
  ]);
  assert.deepEqual(labels, {
    h503: "STM32H503CB",
    nano: "Arduino Nano (ATmega328P, bootloader nuevo)",
    "nano-old": "Arduino Nano (ATmega328P, bootloader viejo / clones)",
    p4: "ESP32-P4",
  });
});

test("lectura: etapa y resumen del informe", () => {
  assert.equal(UI.stageLabel("read"), "Leer");
  assert.equal(UI.stageLabel("read", true), "Leyendo");
  const s = UI.readSummary({ summary: { state: "match", file: "h503/build/a.elf", text: "Tiene grabado a.elf" } });
  assert.deepEqual(s, { tone: "ok", title: "Tiene grabado a.elf", icon: "check" });
  assert.equal(UI.readSummary({ summary: { state: "unknown", text: "Firmware desconocido" } }).tone, "warn");
  assert.equal(UI.readSummary({ summary: { state: "empty", text: "La flash está vacía" } }).tone, "muted");
  assert.equal(UI.readSummary({ summary: { state: "partial", text: "x" } }).tone, "ok");
  assert.equal(UI.readSummary(null), null);
});

test("fmtHexBytes: tamaños legibles", () => {
  assert.equal(UI.usedText(2412, 131072), "2.4 KB de 128 KB (1.8 %)");
  assert.equal(UI.usedText(0, 131072), "0 B de 128 KB (0 %)");
});

// ------------------------------------------------------------------ gráficos

test("niceTicks: marcas redondas que cubren el rango", () => {
  assert.deepEqual(UI.niceTicks(0, 100, 5), [0, 20, 40, 60, 80, 100]);
  assert.deepEqual(UI.niceTicks(-1, 1, 5), [-1, -0.5, 0, 0.5, 1]);
  assert.deepEqual(UI.niceTicks(22.3, 27.9, 6), [23, 24, 25, 26, 27]);
  // Rango nulo (variable constante): igual hay marcas alrededor del valor.
  const flat = UI.niceTicks(250, 250, 5);
  assert.ok(flat.length >= 2 && flat[0] <= 250 && flat[flat.length - 1] >= 250);
  // Con pocas marcas pedidas (una franja baja) igual hay al menos una dentro del rango.
  const few = UI.niceTicks(21.85, 28.19, 1);
  assert.ok(few.length >= 1 && few.every((v) => v >= 21.85 && v <= 28.19), String(few));
});

test("parseSerialValues: números de una línea del monitor serie", () => {
  assert.deepEqual(UI.parseSerialValues("temp=25.1 hum=40"), { names: ["temp", "hum"], values: [25.1, 40] });
  assert.deepEqual(UI.parseSerialValues("temp: -3.5e2, rpm:1200\r"), { names: ["temp", "rpm"], values: [-350, 1200] });
  assert.deepEqual(UI.parseSerialValues("1.5, 3, -7"), { names: null, values: [1.5, 3, -7] });
  assert.deepEqual(UI.parseSerialValues("10\t20 30"), { names: null, values: [10, 20, 30] });
  // La salida de 04_contador_serie: se grafican los pares nombre = valor.
  assert.deepEqual(UI.parseSerialValues("contador = 171   LEDs = 10101011   (0xAB)").names, ["contador", "LEDs"]);
  assert.equal(UI.parseSerialValues("=== 05_contador_rtt - STM32H503 ==="), null);
  assert.equal(UI.parseSerialValues(""), null);
});

test("fmtValue: valores compactos", () => {
  assert.equal(UI.fmtValue(1000), "1000");
  assert.equal(UI.fmtValue(0.123456), "0.1235");
  assert.equal(UI.fmtValue(-2.5), "-2.5");
  assert.equal(UI.fmtValue(123456789), "1.235e+8");
  assert.equal(UI.fmtValue(null), "—");
});

test("samplesCsv: exportar lo graficado", () => {
  const csv = UI.samplesCsv(["seno", "sensor.adc"], [[0, 0.5, 2048], [0.02, -0.25, 2050]]);
  assert.equal(csv, "t_s,seno,sensor.adc\n0,0.5,2048\n0.02,-0.25,2050\n");
});
