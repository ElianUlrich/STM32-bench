"""Instalador del Banco remoto: un asistente que instala la web en esta PC o en una
placa (BeagleBoard, BeagleBone, Raspberry Pi...) por SSH.

En la placa detecta el modelo y el sistema de la microSD (se pueden corregir),
muestra los pasos con su tiempo estimado y lanza la instalación, que sigue sola
en la placa aunque se cierre esta ventana.

    python -m installer.gui            (desde el repositorio)
    InstaladorBancoRemoto.exe          (armado con installer/build.ps1)
"""
from __future__ import annotations

import os
import queue
import re
import sys
import tempfile
import threading
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import Callable, Dict, Optional

from . import icono, paquete, pc, perfil, remoto

TITLE = "Instalar Banco remoto"
TEAL = "#0f766e"
TEAL_DARK = "#0b5c55"
MIN_PASSWORD = 10


def fmt_minutes(m: int) -> str:
    if m < 60:
        return f"{m} min"
    h, rest = divmod(m, 60)
    return f"{h} h {rest:02d} min" if rest else f"{h} h"


class Wizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(TITLE)
        # Alto según la pantalla (en una de 768 px quedan ~680 útiles).
        height = min(660, self.winfo_screenheight() - 100)
        self.geometry(f"800x{height}")
        self.minsize(720, min(560, height))
        self._icon_bytes = icono.ico()
        try:
            ico = Path(tempfile.gettempdir()) / "banco-remoto-instalador.ico"
            ico.write_bytes(self._icon_bytes)
            self.iconbitmap(default=str(ico))
        except (OSError, tk.TclError):
            pass
        self._events: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.after(100, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Estado
        self.target = tk.StringVar(value="placa")
        self.remote: Optional[remoto.Remote] = None
        self.info: Dict[str, str] = {}
        self.analysis: Optional[perfil.Analysis] = None
        self.busy = False
        self.remote_running = False

        self._style()
        self._layout()
        self.page_inicio()

    # ------------------------------------------------------------------ marco
    def _style(self) -> None:
        st = ttk.Style(self)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        # Letra por defecto con nombre (option_add("*Font") pisaría la de los estilos).
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family="Segoe UI", size=10)
        self.f_title = tkfont.Font(family="Segoe UI Semibold", size=15)
        st.configure("Title.TLabel", font=self.f_title)
        st.configure("Sub.TLabel", foreground="#5d6879")
        st.configure("Key.TLabel", foreground="#5d6879")
        st.configure("Warn.TLabel", foreground="#8a5300")
        st.configure("Err.TLabel", foreground="#b42318")
        st.configure("Ok.TLabel", foreground="#15803d")
        st.configure("Big.TRadiobutton", font=("Segoe UI Semibold", 11))

    def _layout(self) -> None:
        head = tk.Frame(self, bg=TEAL, height=64)
        head.pack(fill="x")
        head.pack_propagate(False)
        self._logo = tk.PhotoImage(data=icono.png(40))
        tk.Label(head, image=self._logo, bg=TEAL).pack(side="left", padx=(16, 10))
        box = tk.Frame(head, bg=TEAL)
        box.pack(side="left", fill="y", pady=10)
        tk.Label(box, text="Banco remoto", bg=TEAL, fg="white", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        self.head_sub = tk.Label(box, text="Programación y depuración de microcontroladores", bg=TEAL,
                                 fg="#cdeeea", font=("Segoe UI", 9))
        self.head_sub.pack(anchor="w")

        foot = ttk.Frame(self, padding=(16, 10))
        foot.pack(side="bottom", fill="x")
        ttk.Separator(self).pack(side="bottom", fill="x")
        self.btn_cancel = ttk.Button(foot, text="Cancelar", command=self._on_close)
        self.btn_cancel.pack(side="right")
        self.btn_next = ttk.Button(foot, text="Siguiente", command=lambda: None, default="active")
        self.btn_next.pack(side="right", padx=(0, 8))
        self.btn_back = ttk.Button(foot, text="Atrás", command=lambda: None)
        self.btn_back.pack(side="right", padx=(0, 8))
        self.status = ttk.Label(foot, text="", style="Sub.TLabel")
        self.status.pack(side="left")

        self.body = ttk.Frame(self, padding=(24, 18))
        self.body.pack(fill="both", expand=True)

    def _page(self, title: str, subtitle: str = "") -> ttk.Frame:
        for w in self.body.winfo_children():
            w.destroy()
        ttk.Label(self.body, text=title, style="Title.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(self.body, text=subtitle, style="Sub.TLabel", wraplength=730, justify="left").pack(
                anchor="w", pady=(2, 12))
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True)
        self.status.configure(text="")
        return frame

    def _nav(self, back: Optional[Callable] = None, next_: Optional[Callable] = None, next_text: str = "Siguiente",
             cancel_text: str = "Cancelar") -> None:
        self.btn_back.configure(command=back or (lambda: None), state="normal" if back else "disabled")
        self.btn_next.configure(command=next_ or (lambda: None), text=next_text,
                                state="normal" if next_ else "disabled")
        self.btn_cancel.configure(text=cancel_text)

    # --------------------------------------------------------------- hilos
    def _pump(self) -> None:
        try:
            while True:
                self._events.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def ui(self, fn: Callable[[], None]) -> None:
        """Ejecutar en el hilo de la ventana (desde un hilo de trabajo)."""
        self._events.put(fn)

    def work(self, label: str, fn: Callable[[], object], done: Callable[[object], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.configure(text=label)
        self.btn_next.configure(state="disabled")
        self.btn_back.configure(state="disabled")

        def run() -> None:
            try:
                result = fn()
            except Exception as exc:                       # se muestra en la ventana
                msg = str(exc) or exc.__class__.__name__
                if not isinstance(exc, (remoto.RemoteError, pc.PcInstallError)):
                    traceback.print_exc()
                self.ui(lambda: self._failed(msg))
                return
            self.ui(lambda: self._succeeded(done, result))
        threading.Thread(target=run, daemon=True).start()

    def _failed(self, msg: str) -> None:
        self.busy = False
        self.status.configure(text="")
        if getattr(self, "progress", None) is not None and self.progress.winfo_exists():
            self.progress.stop()
        self.btn_next.configure(state="normal")
        self.btn_back.configure(state="normal")
        messagebox.showerror(TITLE, msg, parent=self)

    def _succeeded(self, done: Callable[[object], None], result: object) -> None:
        self.busy = False
        self.status.configure(text="")
        done(result)

    def _on_close(self) -> None:
        if self.remote_running:
            if not messagebox.askyesno(TITLE, "La instalación sigue en la placa aunque cierres esta ventana "
                                              "(el registro queda en ~/banco-instalar.log).\n\n¿Cerrar?",
                                       parent=self):
                return
        elif self.busy and not messagebox.askyesno(TITLE, "Hay una instalación en curso. ¿Cerrar igual?",
                                                   parent=self):
            return
        if self.remote:
            self.remote.close()
        self.destroy()

    # ----------------------------------------------------------- 1. ¿dónde?
    def page_inicio(self) -> None:
        f = self._page("¿Dónde querés instalar el Banco remoto?",
                       "La misma web funciona en las dos opciones. Se puede instalar en ambos lugares.")
        for value, title, desc in (
                ("placa", "En una placa, por la red",
                 "BeagleBoard-xM, BeagleBone, Raspberry Pi u otra placa con Linux. Queda encendida en el "
                 "laboratorio y la usás desde cualquier lado por Tailscale. Hace falta que la placa tenga su "
                 "microSD con el sistema y esté conectada a la red (SSH)."),
                ("pc", "En esta PC",
                 "Usa los USB de esta computadora: conectás el ST-Link, el ESP32 o el Arduino acá. No hace "
                 "falta ninguna placa. Necesita Python 3.9 o más nuevo, y STM32CubeIDE para los STM32.")):
            card = ttk.Frame(f, padding=(4, 8))
            card.pack(fill="x", pady=4)
            ttk.Radiobutton(card, text=title, value=value, variable=self.target, style="Big.TRadiobutton").pack(
                anchor="w")
            ttk.Label(card, text=desc, style="Sub.TLabel", wraplength=690, justify="left").pack(anchor="w",
                                                                                               padx=(24, 0))
        self._nav(next_=lambda: self.page_pc() if self.target.get() == "pc" else self.page_conexion())

    # ------------------------------------------------------------ 2a. esta PC
    def page_pc(self) -> None:
        f = self._page("Instalar en esta PC",
                       "Se instala todo lo necesario para tu usuario de Windows: no hace falta tener Python ni "
                       "buscar programas.")
        self.pc_dir = tk.StringVar(value=str(pc.default_base()))
        self.pc_user = tk.StringVar(value=os.environ.get("USERNAME") or "admin")
        self.pc_pass = tk.StringVar()
        self.pc_pass2 = tk.StringVar()
        self.pc_shortcut = tk.BooleanVar(value=True)
        self.pc_tailscale = tk.BooleanVar(value=True)
        g = ttk.Frame(f)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        ttk.Label(g, text="Carpeta").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(g, textvariable=self.pc_dir).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(g, text="Elegir…", command=self._pick_dir).grid(row=0, column=2)
        ttk.Label(g, text="Usuario de la web").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(g, textvariable=self.pc_user, width=28).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(g, text="Contraseña").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(g, textvariable=self.pc_pass, show="•", width=28).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(g, text="Repetila").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(g, textvariable=self.pc_pass2, show="•", width=28).grid(row=3, column=1, sticky="w", padx=8)
        ttk.Label(g, text=f"Mínimo {MIN_PASSWORD} caracteres. Se guarda sólo su hash.", style="Sub.TLabel").grid(
            row=4, column=1, sticky="w", padx=8)
        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=(10, 4))
        ttk.Checkbutton(opts, text="Acceso directo \"Banco remoto\" en el escritorio",
                        variable=self.pc_shortcut).pack(anchor="w")
        self.pc_ts_check = ttk.Checkbutton(opts, text="Acceso remoto con Tailscale (entrar desde otras PC)",
                                           variable=self.pc_tailscale)
        self.pc_ts_check.pack(anchor="w")
        ttk.Label(f, text="Lo que va a tener el banco", style="Key.TLabel").pack(anchor="w", pady=(8, 2))
        self.pc_checks = ttk.Frame(f)
        self.pc_checks.pack(fill="x")
        ttk.Label(self.pc_checks, text="Revisando esta PC…", style="Sub.TLabel").pack(anchor="w")
        self._nav(back=self.page_inicio, next_=self._start_pc, next_text="Instalar")
        threading.Thread(target=self._scan_pc, daemon=True).start()

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(parent=self, initialdir=self.pc_dir.get())
        if d:
            self.pc_dir.set(str(Path(d)))

    def _scan_pc(self) -> None:
        own = "se instala con el banco"
        rows = [("Python 3.12", own + " (privado, no toca otros Python)"),
                ("Bibliotecas", own + " (FastAPI, pyserial, esptool…)")]
        try:
            from app import tools
            found = tools.detect()
        except Exception:                                          # pragma: no cover
            found = {}
        for key, label in (("openocd", "OpenOCD de ST"), ("gdb", "GDB (Depurar)")):
            item = found.get(key)
            from_ide = item is not None and item.source.startswith("STM32CubeIDE")
            rows.append((label, f"el de {item.source}" if from_ide else own))
        rows += [("avrdude (Arduino)", own), ("dfu-util (USB DFU)", own)]
        ts_ip = pc.tailscale_ip()
        ts = pc.tailscale_exe()
        ts_text = (f"ya instalado ({ts_ip})" if ts_ip else "ya instalado: hay que iniciar sesión") if ts \
            else "se baja el instalador oficial y Windows pide permiso"

        def show() -> None:
            if not self.pc_checks.winfo_exists():
                return
            for w in self.pc_checks.winfo_children():
                w.destroy()
            for label, text in rows + [("Tailscale", ts_text)]:
                line = ttk.Frame(self.pc_checks)
                line.pack(fill="x")
                ttk.Label(line, text="✓ " + label, width=22, style="Ok.TLabel").pack(side="left")
                ttk.Label(line, text=text, style="Sub.TLabel").pack(side="left")
        self.ui(show)

    def _start_pc(self) -> None:
        user, pw, pw2 = self.pc_user.get().strip(), self.pc_pass.get(), self.pc_pass2.get()
        if not user or len(pw) < MIN_PASSWORD:
            messagebox.showwarning(TITLE, f"Elegí un usuario y una contraseña de al menos {MIN_PASSWORD} "
                                          "caracteres.", parent=self)
            return
        if pw != pw2:
            messagebox.showwarning(TITLE, "Las contraseñas no coinciden.", parent=self)
            return
        base = Path(self.pc_dir.get())
        tailscale = self.pc_tailscale.get()
        self._pc_remote = tailscale
        log = self.page_progreso("Instalando en esta PC",
                                 "Copia la aplicación, su Python, las bibliotecas y las herramientas. Tarda menos "
                                 "de un minuto" + (" (más la descarga de Tailscale)." if tailscale else "."))
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        def job():
            return pc.install(paquete.package_path(), paquete.runtime_path(), base, user, pw,
                              self.pc_shortcut.get(), self._icon_bytes, tailscale=tailscale,
                              remote_access=tailscale, log=lambda line: self.ui(lambda: log(line)))

        def done(bat):
            self.progress.stop()
            self.page_fin_pc(bat)
        self.work("Instalando…", job, done)
        self._nav(back=self.page_pc, next_=self._start_pc, next_text="Reintentar")
        self.btn_next.configure(state="disabled")
        self.btn_back.configure(state="disabled")

    def page_fin_pc(self, bat: Path) -> None:
        remote = getattr(self, "_pc_remote", False)
        f = self._page("Listo: el Banco remoto quedó instalado en esta PC",
                       "Para abrirlo usá el acceso directo \"Banco remoto\" del escritorio. Se abre en el "
                       "navegador.")
        ttk.Label(f, text=str(bat.parent.parent), style="Sub.TLabel").pack(anchor="w")
        if remote:
            ip = pc.tailscale_ip()
            text = (f"Desde otras PC de tu Tailscale: http://{ip}:8080" if ip else
                    "Cuando termines de iniciar sesión en Tailscale, las otras PC de tu red van a poder entrar "
                    "con la IP de Tailscale de esta PC, puerto 8080.")
            ttk.Label(f, text=text, style="Sub.TLabel", wraplength=720).pack(anchor="w", pady=(10, 0))
        ttk.Label(f, text="Lo que instala está en LICENCIAS.txt (programas libres de terceros).",
                  style="Sub.TLabel").pack(anchor="w", pady=(10, 0))
        self._nav(next_=lambda: (pc.launch(bat, "--tailscale" if remote else ""), self.destroy()),
                  next_text="Abrir el banco", cancel_text="Cerrar")

    # --------------------------------------------------- 2b. placa: conexión
    def page_conexion(self) -> None:
        f = self._page("Conectar con la placa",
                       "La placa tiene que estar encendida, con su microSD, y en la misma red que esta PC (o en "
                       "Tailscale). Se entra por SSH con el usuario del sistema de la placa.")
        if not hasattr(self, "ssh_host"):
            self.ssh_host = tk.StringVar()
            self.ssh_port = tk.StringVar(value="22")
            self.ssh_user = tk.StringVar(value="ubuntu")
            self.ssh_pass = tk.StringVar()
        g = ttk.Frame(f)
        g.pack(anchor="w")
        rows = (("IP o nombre de la placa", self.ssh_host, 30, None), ("Puerto SSH", self.ssh_port, 8, None),
                ("Usuario", self.ssh_user, 20, None), ("Contraseña de SSH", self.ssh_pass, 24, "•"))
        for i, (label, var, width, show) in enumerate(rows):
            ttk.Label(g, text=label).grid(row=i, column=0, sticky="w", pady=5)
            e = ttk.Entry(g, textvariable=var, width=width, show=show or "")
            e.grid(row=i, column=1, sticky="w", padx=10)
            if i == 0:
                e.focus_set()
        hints = ("Ejemplos: 192.168.0.27 (red local) o 100.99.235.81 (Tailscale).",
                 "Usuarios típicos: debian (imágenes de BeagleBoard.org), ubuntu (rcn-ee), pi (Raspberry Pi OS).",
                 "Si ya entrás con una clave SSH sin contraseña, dejá la contraseña vacía.",
                 "Sólo se lee información de la placa: no se cambia nada hasta que confirmes.")
        for h in hints:
            ttk.Label(f, text=h, style="Sub.TLabel").pack(anchor="w", pady=(6 if h is hints[0] else 0, 0))
        self._nav(back=self.page_inicio, next_=self._connect, next_text="Conectar")
        self.bind("<Return>", lambda _e: self._connect() if not self.busy else None)

    def _connect(self) -> None:
        host, user = self.ssh_host.get().strip(), self.ssh_user.get().strip()
        if not host or not user:
            messagebox.showwarning(TITLE, "Completá la IP de la placa y el usuario.", parent=self)
            return
        try:
            port = int(self.ssh_port.get())
        except ValueError:
            messagebox.showwarning(TITLE, "El puerto tiene que ser un número (normalmente 22).", parent=self)
            return
        self.unbind("<Return>")

        def job():
            if self.remote:
                self.remote.close()
            self.remote = remoto.Remote(host, user, self.ssh_pass.get(), port)
            return self.remote.probe()

        def done(info):
            self.info = info
            self.board_choice = tk.StringVar(value=perfil.detect_board(info).name)
            self.os_choice = tk.StringVar(value=perfil.detect_os(info).label)
            self.opt_prebuilt = tk.BooleanVar(value=True)
            self.opt_gdb = tk.BooleanVar(value=True)
            self.opt_config = tk.BooleanVar(value=False)
            self.opt_mode = tk.StringVar(value="")
            self.page_plan()
        self.work(f"Conectando con {host}…", job, done)

    # ------------------------------------------------------ 2c. placa: plan
    def _analyze(self) -> perfil.Analysis:
        board = next(b for b in perfil.BOARDS if b.name == self.board_choice.get())
        os_ = next(o for o in perfil.OSES if o.label == self.os_choice.get())
        return perfil.analyze(self.info, board_id=board.id, os_key=os_.key, prebuilt=self.opt_prebuilt.get(),
                              with_gdb=self.opt_gdb.get())

    def page_plan(self) -> None:
        f = self._page("Esto es lo que encontré",
                       "Revisá la placa y el sistema de la microSD; si algo no coincide, corregilo.")
        self.plan_frame = f
        self._nav(back=self.page_conexion, next_=self.page_claves)
        self._render_plan()

    def _render_plan(self) -> None:
        f = self.plan_frame
        for w in f.winfo_children():
            w.destroy()
        a = self.analysis = self._analyze()
        if not self.opt_mode.get():
            self.opt_mode.set(a.mode)
        info = self.info
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Placa", style="Key.TLabel").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(top, textvariable=self.board_choice, values=[b.name for b in perfil.BOARDS],
                          state="readonly", width=32)
        cb.grid(row=0, column=1, sticky="w", padx=(6, 18), pady=2)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._render_plan())
        ttk.Label(top, text="Sistema", style="Key.TLabel").grid(row=0, column=2, sticky="w")
        cb2 = ttk.Combobox(top, textvariable=self.os_choice, values=[o.label for o in perfil.OSES],
                           state="readonly", width=37)
        cb2.grid(row=0, column=3, sticky="w", padx=6, pady=2)
        cb2.bind("<<ComboboxSelected>>", lambda _e: self._render_plan())
        try:
            mem = f"{int(info.get('mem_kb', '0')) // 1024} MB"
            free = f"{int(info.get('disk_free_kb', '0')) / 1024 / 1024:.1f} GB libres"
        except ValueError:
            mem = free = "?"
        bench = {"active": "funcionando", "none": "no instalado"}.get(info.get("bench", ""), info.get("bench", "?"))
        details = (f"{info.get('model') or 'sin modelo'} · {info.get('os_pretty') or '?'} · "
                   f"{perfil.ARCHES.get(a.arch, a.arch)} · RAM {mem} · microSD {free} · "
                   f"Python {info.get('python') or '?'} · Tailscale {info.get('tailscale_ip') or 'no'} · "
                   f"OpenOCD {'sí' if info.get('openocd_h5') == '1' else 'no'} · banco {bench}")
        ttk.Label(top, text=details, style="Sub.TLabel", wraplength=730, justify="left").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        if not a.supported:
            for b in a.blockers:
                ttk.Label(f, text="✗ " + b, style="Err.TLabel", wraplength=720).pack(anchor="w", pady=(10, 0))
            self.btn_next.configure(state="disabled")
            return
        self.btn_next.configure(state="normal")

        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=(8, 2))
        ttk.Radiobutton(opts, text="Instalación completa", value="completo", variable=self.opt_mode,
                        command=self._render_plan).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(opts, text="Sólo la aplicación (actualizar)", value="app", variable=self.opt_mode,
                        command=self._render_plan).grid(row=0, column=1, sticky="w", padx=(24, 0))
        pre = ttk.Checkbutton(opts, text="Binarios precompilados (minutos en vez de horas)",
                              variable=self.opt_prebuilt, command=self._render_plan)
        pre.grid(row=1, column=0, sticky="w", pady=(4, 0))
        if not a.prebuilt_available:
            self.opt_prebuilt.set(False)
            pre.state(["disabled"])
        ttk.Checkbutton(opts, text="Instalar GDB (pestaña Depurar)", variable=self.opt_gdb,
                        command=self._render_plan).grid(row=1, column=1, sticky="w", padx=(24, 0), pady=(4, 0))
        ttk.Checkbutton(opts, text="Actualizar chips.yaml y bench.yaml (quedan .bak)",
                        variable=self.opt_config).grid(row=2, column=0, sticky="w")

        steps = a.steps if self.opt_mode.get() == "completo" else [s for s in a.steps if s.name == "Aplicación"]
        tree = ttk.Treeview(f, columns=("detalle", "tiempo"), show="tree headings", height=len(steps))
        tree.heading("#0", text="Paso")
        tree.heading("detalle", text="")
        tree.heading("tiempo", text="Tiempo aprox.")
        tree.column("#0", width=170)
        tree.column("detalle", width=330)
        tree.column("tiempo", width=110, anchor="e")
        for s in steps:
            tree.insert("", "end", text=s.name, values=(s.detail, fmt_minutes(s.minutes)))
        tree.pack(fill="x", pady=(6, 2))
        ttk.Label(f, text=f"Total aproximado: {fmt_minutes(sum(s.minutes for s in steps))}",
                  style="Key.TLabel").pack(anchor="e")
        for w in a.warnings:
            ttk.Label(f, text="⚠ " + w, style="Warn.TLabel", wraplength=730, justify="left").pack(anchor="w")

    # ---------------------------------------------------- 2d. placa: claves
    def page_claves(self) -> None:
        f = self._page("Contraseñas",
                       "La de sudo hace falta para instalar. Ninguna contraseña queda guardada en esta PC.")
        need_web = self.info.get("bench", "none") == "none"
        self.sudo_same = tk.BooleanVar(value=bool(self.ssh_pass.get()))
        self.sudo_pass = tk.StringVar()
        self.web_user = tk.StringVar(value="admin")
        self.web_pass = tk.StringVar()
        self.web_pass2 = tk.StringVar()
        g = ttk.Frame(f)
        g.pack(anchor="w", fill="x")
        ttk.Label(g, text="Contraseña de sudo de la placa", style="Key.TLabel").grid(row=0, column=0, columnspan=2,
                                                                                    sticky="w")
        sudo_entry = ttk.Entry(g, textvariable=self.sudo_pass, show="•", width=26)

        def sync():
            sudo_entry.state(["disabled"] if self.sudo_same.get() else ["!disabled"])
        ttk.Checkbutton(g, text="Es la misma que la de SSH", variable=self.sudo_same, command=sync).grid(
            row=1, column=0, sticky="w", pady=4)
        sudo_entry.grid(row=1, column=1, sticky="w", padx=10)
        sync()
        if need_web:
            ttk.Label(g, text="Usuario y contraseña para entrar a la web del banco", style="Key.TLabel").grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(16, 2))
            for i, (label, var, show) in enumerate((("Usuario", self.web_user, ""),
                                                     ("Contraseña", self.web_pass, "•"),
                                                     ("Repetila", self.web_pass2, "•"))):
                ttk.Label(g, text=label).grid(row=3 + i, column=0, sticky="w", pady=3)
                ttk.Entry(g, textvariable=var, show=show, width=26).grid(row=3 + i, column=1, sticky="w", padx=10)
            ttk.Label(g, text=f"Mínimo {MIN_PASSWORD} caracteres. En la placa se guarda sólo su hash.",
                      style="Sub.TLabel").grid(row=6, column=1, sticky="w", padx=10)
        else:
            ttk.Label(f, text="La placa ya tiene el banco: se mantiene su usuario y contraseña de la web.",
                      style="Sub.TLabel").pack(anchor="w", pady=(14, 0))
        if self.opt_mode.get() == "completo" and not self.info.get("tailscale_ip"):
            ttk.Label(f, text="Durante la instalación va a aparecer un enlace para autorizar la placa en "
                              "Tailscale: abrilo e iniciá sesión con tu cuenta.", style="Warn.TLabel",
                      wraplength=720).pack(anchor="w", pady=(16, 0))
        self._nav(back=self.page_plan, next_=lambda: self._start_remote(need_web), next_text="Instalar")

    def _start_remote(self, need_web: bool) -> None:
        sudo = self.ssh_pass.get() if self.sudo_same.get() else self.sudo_pass.get()
        if not sudo:
            messagebox.showwarning(TITLE, "Falta la contraseña de sudo.", parent=self)
            return
        web_user = web_pw = None
        if need_web:
            web_user, web_pw = self.web_user.get().strip(), self.web_pass.get()
            if not web_user or len(web_pw) < MIN_PASSWORD:
                messagebox.showwarning(TITLE, f"El usuario de la web y una contraseña de al menos {MIN_PASSWORD} "
                                              "caracteres.", parent=self)
                return
            if web_pw != self.web_pass2.get():
                messagebox.showwarning(TITLE, "Las contraseñas de la web no coinciden.", parent=self)
                return
        a = self._analyze()
        mode = self.opt_mode.get()
        prebuilt = a.prebuilt if mode == "completo" else None
        home = self.info.get("home") or f"/home/{self.ssh_user.get()}"
        log = self.page_progreso("Instalando en la placa",
                                 "Corre en la placa: si cerrás esta ventana o se corta la red, sigue sola. "
                                 "El registro queda en ~/banco-instalar.log.")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._log_text = ""
        self._log_offset = 0

        def job():
            return remoto.start_install(self.remote, home, paquete.package_path(), sudo, mode, prebuilt,
                                        self.opt_gdb.get(), self.opt_config.get(), web_user, web_pw,
                                        log=lambda line: self.ui(lambda: log(line)))

        def done(log_path):
            self.remote_running = True
            self.btn_back.configure(state="disabled")
            self._poll_remote(log_path, log)
        self.work("Preparando…", job, done)

    def _poll_remote(self, log_path: str, log: Callable[[str], None]) -> None:
        def job():
            chunk = self.remote.read_from(log_path, self._log_offset)
            return chunk

        def got(chunk) -> None:
            if not self.winfo_exists():
                return
            if chunk:
                self._log_offset += len(chunk.encode("utf-8"))
                self._log_text += chunk
                for line in remoto._ANSI.sub("", chunk).splitlines():
                    log(line)
            p = remoto.parse_log(self._log_text)
            if p.step:
                self.step_label.configure(text=p.step)
                m = re.match(r"(\d+)/(\d+)", p.step)
                if m:
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=int(m.group(2)),
                                            value=int(m.group(1)) - 1)
            if p.tailscale_url and not getattr(self, "_ts_shown", False):
                self._ts_shown = True
                self.ts_button.configure(text="Autorizar la placa en Tailscale",
                                         command=lambda u=p.tailscale_url: webbrowser.open(u))
                self.ts_button.pack(anchor="w", pady=(6, 0))
            if p.finished is not None:
                self.remote_running = False
                self.progress.stop()
                if p.finished == 0:
                    url = re.findall(r"Abrí (http://\S+)", remoto._ANSI.sub("", self._log_text))
                    self.page_fin_placa(url[-1] if url else f"http://{self.ssh_host.get()}:8080")
                else:
                    messagebox.showerror(TITLE, "La instalación terminó con un error. Mirá las últimas líneas "
                                                "del registro.", parent=self)
                    self._nav(back=self.page_plan, next_=lambda: self._start_remote(
                        self.info.get("bench", "none") == "none"), next_text="Reintentar")
                return
            self.after(2000, lambda: threading.Thread(target=lambda: self._poll_once(job, got), daemon=True).start())
        got("")

    def _poll_once(self, job, got) -> None:
        try:
            chunk = job()
        except Exception:                      # corte de red: se reintenta
            chunk = ""
        self.ui(lambda: got(chunk))

    def page_fin_placa(self, url: str) -> None:
        f = self._page("Listo: el Banco remoto quedó instalado en la placa",
                       "Entrá desde cualquier PC de tu red de Tailscale con el usuario y la contraseña de la web.")
        ttk.Label(f, text=url, font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(4, 8))
        ttk.Label(f, text="Recargá la página con Ctrl+F5 si ya la tenías abierta.", style="Sub.TLabel").pack(anchor="w")
        self._nav(next_=lambda: (webbrowser.open(url), self.destroy()), next_text="Abrir en el navegador",
                  cancel_text="Cerrar")

    # ---------------------------------------------------------- progreso
    def page_progreso(self, title: str, subtitle: str) -> Callable[[str], None]:
        f = self._page(title, subtitle)
        self.step_label = ttk.Label(f, text="", font=("Segoe UI Semibold", 10))
        self.step_label.pack(anchor="w")
        self.progress = ttk.Progressbar(f, mode="indeterminate")
        self.progress.pack(fill="x", pady=(4, 8))
        self.ts_button = ttk.Button(f, text="")
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        text = tk.Text(box, height=14, wrap="word", bg="#0d1117", fg="#d6dee8", insertbackground="#d6dee8",
                       font=("Cascadia Mono", 9), relief="flat", padx=8, pady=6)
        sb = ttk.Scrollbar(box, command=text.yview)
        text.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        self._nav(cancel_text="Cerrar")

        def log(line: str) -> None:
            if not text.winfo_exists():
                return
            text.configure(state="normal")
            text.insert("end", line + "\n")
            if int(text.index("end-1c").split(".")[0]) > 3000:
                text.delete("1.0", "500.0")
            text.see("end")
            text.configure(state="disabled")
        return log


def main() -> int:
    if "--selftest" in sys.argv:              # que la ventana se arme (para el .exe recién compilado)
        import paramiko  # noqa: F401          (SSH a la placa)
        from app import tools  # noqa: F401    (herramientas de la PC)
        if getattr(sys, "frozen", False):
            if not paquete.resource(paquete.NAME).is_file():
                return 2                       # el .exe no trae la aplicación
            try:
                paquete.runtime_path()         # ni Python ni las herramientas
            except FileNotFoundError:
                return 3
        w = Wizard()
        w.update()
        w.page_conexion()
        w.update()
        w.destroy()
        print("ok")
        return 0
    if "--instalar-pc" in sys.argv:           # instalación desatendida (varias PC de un laboratorio)
        return install_pc_unattended(sys.argv)
    Wizard().mainloop()
    return 0


def install_pc_unattended(argv) -> int:
    """InstaladorBancoRemoto.exe --instalar-pc CARPETA --usuario U [--sin-acceso-directo] [--tailscale]

    La contraseña de la web va en la variable de entorno BANCO_CLAVE_WEB (nunca en la línea de
    comandos). El registro queda en CARPETA\\instalacion.log; el código de salida es 0 si salió bien."""
    def arg(name: str) -> Optional[str]:
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
    base = Path(arg("--instalar-pc") or pc.default_base())
    user, password = arg("--usuario") or "admin", os.environ.get("BANCO_CLAVE_WEB", "")
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "instalacion.log", "w", encoding="utf-8") as fh:
        def log(line: str) -> None:
            fh.write(line + "\n")
            fh.flush()
        if len(password) < MIN_PASSWORD:
            log(f"Falta BANCO_CLAVE_WEB (mínimo {MIN_PASSWORD} caracteres).")
            return 1
        try:
            pc.install(paquete.package_path(), paquete.runtime_path(), base, user, password,
                       "--sin-acceso-directo" not in argv, icono.ico(), tailscale="--tailscale" in argv,
                       remote_access="--tailscale" in argv, log=log)
        except Exception as exc:
            log(f"ERROR: {exc}")
            return 1
        log("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
