// Gráfico en el tiempo sobre <canvas>, sin dependencias: varias series, ventana de
// tiempo deslizante, escala automática, pausa y cursor con los valores.
//
//   const plot = new LivePlot(canvas, { onHover })
//   plot.setSeries(["seno", "cuadrada"])     // borra los datos
//   plot.push([[t, v1, v2], ...])            // t en segundos
//   plot.setWindow(10) / setWindow(null)     // últimos 10 s / todo
//   plot.setStacked(true)                    // una franja por serie, cada una con su escala
//   plot.setPaused(true)                     // congela la vista (los datos siguen llegando)
//   plot.setVisible(i, false)
"use strict";

(function (root) {
  const MAX_POINTS = 60000;        // por serie: a 100 por segundo, 10 minutos

  class LivePlot {
    constructor(canvas, opts) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.opts = opts || {};
      this.names = [];
      this.t = [];
      this.v = [];                 // v[i] = valores de la serie i
      this.visible = [];
      this.windowS = 10;
      this.stacked = false;
      this.paused = false;
      this.frozenEnd = null;       // fin de la vista al pausar
      this.hoverX = null;
      this._queued = false;
      this._resize = new ResizeObserver(() => this.draw());
      this._resize.observe(canvas);
      canvas.addEventListener("pointermove", (e) => {
        const r = canvas.getBoundingClientRect();
        this.hoverX = e.clientX - r.left;
        this.draw();
      });
      canvas.addEventListener("pointerleave", () => {
        this.hoverX = null;
        this.draw();
        if (this.opts.onHover) this.opts.onHover(null);
      });
    }

    setSeries(names) {
      this.names = names.slice();
      this.t = [];
      this.v = names.map(() => []);
      this.visible = names.map(() => true);
      this.frozenEnd = null;
      this.draw();
    }

    push(samples) {
      if (!samples || !samples.length) return;
      for (const s of samples) {
        this.t.push(s[0]);
        for (let i = 0; i < this.v.length; i++) {
          const x = s[i + 1];
          this.v[i].push(x === null || x === undefined ? NaN : x);
        }
      }
      if (this.t.length > MAX_POINTS) {
        const drop = this.t.length - MAX_POINTS;
        this.t.splice(0, drop);
        for (const arr of this.v) arr.splice(0, drop);
      }
      if (!this.paused) this.draw();
    }

    clear() { this.setSeries(this.names); }

    setWindow(seconds) { this.windowS = seconds; this.draw(); }

    setStacked(on) { this.stacked = on; this.draw(); }

    setPaused(p) {
      this.paused = p;
      this.frozenEnd = p && this.t.length ? this.t[this.t.length - 1] : null;
      this.draw();
    }

    setVisible(i, on) { this.visible[i] = on; this.draw(); }

    // Último valor y mínimo/máximo de cada serie en la vista actual.
    stats() {
      const [t0, t1] = this._range();
      const lo = this._index(t0), hi = this._index(t1, true);
      return this.v.map((arr) => {
        const [min, max] = this._minmax(arr, lo, hi);
        let last = null;
        for (let k = hi - 1; k >= lo; k--) if (!Number.isNaN(arr[k])) { last = arr[k]; break; }
        return { last, min: isFinite(min) ? min : null, max: isFinite(max) ? max : null };
      });
    }

    allSamples() {
      return this.t.map((t, k) => [t].concat(this.v.map((arr) => (Number.isNaN(arr[k]) ? null : arr[k]))));
    }

    // [inicio, fin] de la vista. Con ventana fija, mientras los datos no la llenan
    // se muestra desde el primero (no tiempos negativos).
    _range() {
      const n = this.t.length;
      const first = n ? this.t[0] : 0;
      const end = this.frozenEnd !== null ? this.frozenEnd : (n ? this.t[n - 1] : 0);
      if (!this.windowS) return [first, Math.max(end, first + 1)];
      const start = Math.max(first, end - this.windowS);
      return [start, start + this.windowS];
    }

    // Primer índice con t >= x (o > x con `after`), por búsqueda binaria.
    _index(x, after) {
      let a = 0, b = this.t.length;
      while (a < b) {
        const m = (a + b) >> 1;
        if (after ? this.t[m] <= x : this.t[m] < x) a = m + 1; else b = m;
      }
      return a;
    }

    _minmax(arr, lo, hi) {
      let min = Infinity, max = -Infinity;
      for (let k = lo; k < hi; k++) {
        const x = arr[k];
        if (x < min) min = x;
        if (x > max) max = x;
      }
      return [min, max];
    }

    draw() {
      if (this._queued) return;
      this._queued = true;
      requestAnimationFrame(() => { this._queued = false; this._paint(); });
    }

    _paint() {
      const canvas = this.canvas, ctx = this.ctx;
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const css = getComputedStyle(canvas);
      const color = (name) => css.getPropertyValue(name).trim();
      const seriesColor = (i) => color(`--plot-${(i % 8) + 1}`);
      ctx.font = `12px ${color("--mono") || "monospace"}`;

      const [t0, t1] = this._range();
      const lo = this._index(t0), hi = this._index(t1, true);
      const shown = this.v.map((_, i) => i).filter((i) => this.visible[i]);
      const T = 10, B = 24, R = 10, GAP = 8;

      // Franjas: una con todas las series o una por serie (cada una con su escala).
      const groups = this.stacked && shown.length > 1 ? shown.map((i) => [i]) : [shown];
      const laneH = (h - T - B - GAP * (groups.length - 1)) / groups.length;
      let hasData = false;
      const lanes = groups.map((idx, g) => {
        let min = Infinity, max = -Infinity;
        for (const i of idx) {
          const [a, b] = this._minmax(this.v[i], lo, hi);
          if (a < min) min = a;
          if (b > max) max = b;
        }
        if (isFinite(min)) hasData = true; else { min = -1; max = 1; }
        const ticks = UI.niceTicks(min, max, Math.max(2, Math.min(6, Math.floor(laneH / 36))));
        const yMin = Math.min(min, ticks[0]), yMax = Math.max(max, ticks[ticks.length - 1]);
        return { idx, top: T + g * (laneH + GAP), height: Math.max(10, laneH), ticks, yMin, yMax };
      });
      const allTicks = lanes.flatMap((l) => l.ticks);
      const L = Math.ceil(Math.max(30, ...allTicks.map((v) => ctx.measureText(UI.fmtValue(v)).width)) + 12);
      const pw = Math.max(10, w - L - R);
      const X = (t) => L + ((t - t0) / (t1 - t0 || 1)) * pw;
      for (const lane of lanes) {
        lane.Y = (v) => lane.top + lane.height - ((v - lane.yMin) / (lane.yMax - lane.yMin || 1)) * lane.height;
      }

      // Grilla, escalas y eje de tiempo.
      ctx.lineWidth = 1;
      for (const lane of lanes) {
        ctx.strokeStyle = color("--border");
        ctx.fillStyle = color("--muted");
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (const v of lane.ticks) {
          const y = Math.round(lane.Y(v)) + 0.5;
          ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
          ctx.fillText(UI.fmtValue(v), L - 6, y);
        }
        if (lane.yMin < 0 && lane.yMax > 0) {
          ctx.strokeStyle = color("--border-strong");
          const y = Math.round(lane.Y(0)) + 0.5;
          ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
        }
        if (groups.length > 1) {                         // nombre de la serie en su franja
          lane.label = lane.idx[0];
        }
      }
      ctx.strokeStyle = color("--border");
      ctx.fillStyle = color("--muted");
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      const bottom = h - B;
      for (const t of UI.niceTicks(t0, t1, Math.max(2, Math.floor(pw / 90)))) {
        if (t < t0 || t > t1) continue;
        const x = Math.round(X(t)) + 0.5;
        for (const lane of lanes) {
          ctx.beginPath(); ctx.moveTo(x, lane.top); ctx.lineTo(x, lane.top + lane.height); ctx.stroke();
        }
        ctx.fillText(`${UI.fmtValue(t)} s`, x, bottom + 6);
      }

      // Series. Con muchos puntos por píxel se dibuja el mínimo y el máximo de cada columna.
      ctx.lineWidth = 1.6;
      ctx.lineJoin = "round";
      const dense = hi - lo > pw * 2;
      for (const lane of lanes) {
        ctx.save();
        ctx.beginPath(); ctx.rect(L, lane.top - 1, pw, lane.height + 2); ctx.clip();
        for (const i of lane.idx) {
          const arr = this.v[i];
          ctx.strokeStyle = seriesColor(i);
          ctx.beginPath();
          let pen = false;
          if (dense) {
            let col = -1, cmin = 0, cmax = 0;
            const flush = () => {
              if (col < 0) return;
              if (!pen) { ctx.moveTo(col, lane.Y(cmax)); pen = true; } else ctx.lineTo(col, lane.Y(cmax));
              ctx.lineTo(col, lane.Y(cmin));
            };
            for (let k = lo; k < hi; k++) {
              const x = arr[k];
              if (Number.isNaN(x)) continue;
              const c = Math.floor(X(this.t[k]));
              if (c !== col) { flush(); col = c; cmin = cmax = x; }
              else { if (x < cmin) cmin = x; if (x > cmax) cmax = x; }
            }
            flush();
          } else {
            for (let k = Math.max(0, lo - 1); k < Math.min(arr.length, hi + 1); k++) {
              const x = arr[k];
              if (Number.isNaN(x)) continue;       // un hueco (serie que no vino en esa línea): se une
              if (!pen) { ctx.moveTo(X(this.t[k]), lane.Y(x)); pen = true; } else ctx.lineTo(X(this.t[k]), lane.Y(x));
            }
          }
          ctx.stroke();
        }
        ctx.restore();
      }
      // Nombres de las franjas encima de las curvas, con fondo para que se lean.
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      for (const lane of lanes) {
        if (lane.label === undefined) continue;
        const name = this.names[lane.label];
        const tw = ctx.measureText(name).width;
        ctx.globalAlpha = 0.85;
        ctx.fillStyle = color("--surface");
        ctx.fillRect(L + 3, lane.top + 1, tw + 8, 16);
        ctx.globalAlpha = 1;
        ctx.fillStyle = seriesColor(lane.label);
        ctx.fillText(name, L + 7, lane.top + 3);
      }

      if (!hasData) {
        ctx.fillStyle = color("--muted");
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.font = `13px ${color("--sans") || "sans-serif"}`;
        ctx.fillText(this.opts.emptyText || "Sin datos todavía", L + pw / 2, T + (h - T - B) / 2);
      }

      // Cursor: línea vertical y valores en ese instante.
      if (this.hoverX !== null && this.hoverX >= L && this.hoverX <= L + pw && hi > lo) {
        const t = t0 + ((this.hoverX - L) / pw) * (t1 - t0);
        let k = Math.min(this._index(t), hi - 1);
        if (k > lo && Math.abs(this.t[k - 1] - t) < Math.abs(this.t[k] - t)) k--;
        const x = Math.round(X(this.t[k])) + 0.5;
        ctx.strokeStyle = color("--text-2");
        ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, bottom); ctx.stroke();
        ctx.setLineDash([]);
        for (const lane of lanes) {
          for (const i of lane.idx) {
            if (Number.isNaN(this.v[i][k])) continue;
            ctx.fillStyle = seriesColor(i);
            ctx.beginPath(); ctx.arc(x, lane.Y(this.v[i][k]), 3.5, 0, Math.PI * 2); ctx.fill();
          }
        }
        if (this.opts.onHover) {
          this.opts.onHover({ t: this.t[k], values: this.v.map((arr) => (Number.isNaN(arr[k]) ? null : arr[k])) });
        }
      }
    }
  }

  root.LivePlot = LivePlot;
})(typeof globalThis !== "undefined" ? globalThis : this);
