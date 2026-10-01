#!/usr/bin/env python3
"""GDB simulado (modo MI) para el modo mock (BENCH_MOCK=1).

Simula el programa firmware/h503_leds/03_contador_binario corriendo en un
STM32H503, con sus archivos y números de línea reales, para probar la pestaña
"Depurar" sin placa: breakpoints, paso a paso (next/step/finish), continuar y
pausar, variables locales y globales, expresiones, registros, reset y load.

Responde sólo a los comandos MI que usa la app (app/debugger/session.py).
BENCH_MOCK_STM32_ID cambia el ID de chip que se lee (para probar el rechazo).
"""
import ast
import os
import re
import sys
import threading
import time

COMP_DIR = "C:/Users/elian/Desktop/Debugger/firmware/h503_leds"
MAIN_C = "src/03_contador_binario.c"
PLACA_C = "src/placa.c"
START_C = "src/startup_stm32h503.c"

# función: (archivo, dirección base, [(línea, tipo, dato)], índice al que vuelve el bucle o None)
#   tipo: "s" sentencia simple · "call" llamada (dato = función, argumentos) · "wait" espera activa
PROGRAM = {
    "Reset_Handler": (START_C, 0x08000254, [(52, "s", None), (53, "s", None), (56, "s", None),
                                             (59, "call", ("main", {}))], None),
    "main": (MAIN_C, 0x080003C4, [(25, "call", ("placa_init", {})), (26, "call", ("leds_mostrar", {"patron": 0})),
                                  (29, "call", ("esperar_ms", {"ms": 250})), (30, "call", ("contador_paso", {}))], 2),
    "placa_init": (PLACA_C, 0x080002E8, [(22, "s", None), (26, "call", ("leds_mostrar", {"patron": 0})),
                                         (30, "s", "moder"), (33, "s", None), (39, "call", ("reloj_hz", {})),
                                         (40, "s", None), (41, "s", None)], None),
    "reloj_hz": (PLACA_C, 0x080002C0, [(15, "s", "hsidiv"), (16, "s", None)], None),
    "leds_mostrar": (PLACA_C, 0x08000340, [(50, "s", None)], None),
    "contador_paso": (MAIN_C, 0x080003A0, [(19, "s", "contador++"), (20, "call", ("leds_mostrar", None))], None),
    "esperar_ms": (PLACA_C, 0x08000360, [(55, "s", "inicio"), (56, "wait", None)], None),
}
TYPES = {"ms": "uint32_t", "inicio": "uint32_t", "patron": "uint8_t", "moder": "uint32_t", "hsidiv": "uint32_t"}
ARGS = {"esperar_ms": ["ms"], "leds_mostrar": ["patron"]}
LOCALS = {"esperar_ms": ["inicio"], "placa_init": ["moder"], "reloj_hz": ["hsidiv"]}
REG_NAMES = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12",
             "sp", "lr", "pc", "xpsr", "msp", "psp", "primask", "basepri", "faultmask", "control"]
IDCODE_ADDRS = {0x44024000, 0xE0042000, 0x5C001000, 0x40015800}

out_lock = threading.Lock()


def emit(line):
    with out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def c(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def mi(value):
    """dict -> {a="b"}, list -> [..], str/int -> "x"."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={mi(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(mi(v) for v in value) + "]"
    return c(value)


class Frame:
    def __init__(self, func, args=None):
        self.func = func
        self.idx = 0
        self.vars = dict(args or {})
        for name in LOCALS.get(func, []):
            self.vars.setdefault(name, 0)

    @property
    def stmt(self):
        return PROGRAM[self.func][2][self.idx]

    @property
    def file(self):
        return PROGRAM[self.func][0]

    @property
    def line(self):
        return self.stmt[0]

    @property
    def addr(self):
        return PROGRAM[self.func][1] + self.idx * 6


class Target:
    def __init__(self):
        self.globals = {"contador": 5439, "ms_transcurridos": 1359877}
        self.stack = []
        self.bps = {}            # número -> dict(file, line, func, temp)
        self.next_bp = 1
        self.running = False
        self.interrupt = threading.Event()
        self.lock = threading.Lock()
        self.attach_state()

    # --------------------------------------------------------------- estados
    def attach_state(self):
        rh = Frame("Reset_Handler"); rh.idx = 3
        mn = Frame("main"); mn.idx = 2
        es = Frame("esperar_ms", {"ms": 250}); es.idx = 1
        es.vars["inicio"] = self.globals["ms_transcurridos"] - 117
        self.stack = [rh, mn, es]

    def reset_state(self):
        self.globals["ms_transcurridos"] = 0
        self.globals["contador"] = 0          # .bss en cero tras el reset
        self.stack = [Frame("Reset_Handler")]

    @property
    def top(self):
        return self.stack[-1]

    # ------------------------------------------------------------- ejecución
    def _advance(self):
        while True:
            fr = self.top
            fr.idx += 1
            body, loop = PROGRAM[fr.func][2], PROGRAM[fr.func][3]
            if fr.idx < len(body):
                return
            if loop is not None:
                fr.idx = loop
                return
            if len(self.stack) == 1:          # Reset_Handler no retorna: queda en el for(;;)
                fr.idx = len(body) - 1
                return
            self.stack.pop()

    def exec_one(self, realtime=False):
        """Ejecuta la sentencia actual. Devuelve False si un "pausar" la cortó a la mitad."""
        fr = self.top
        _, kind, data = fr.stmt
        if kind == "call":
            func, args = data
            if args is None:                  # leds_mostrar(contador & 0xFF)
                args = {"patron": self.globals["contador"] & 0xFF}
            self.stack.append(Frame(func, args))
            return True
        if kind == "wait":
            target = fr.vars["inicio"] + fr.vars["ms"]
            if realtime:
                while self.globals["ms_transcurridos"] < target:
                    if self.interrupt.is_set():
                        return False
                    time.sleep(0.01)
                    self.globals["ms_transcurridos"] += 10
            self.globals["ms_transcurridos"] = max(self.globals["ms_transcurridos"], target)
        elif data == "contador++":
            self.globals["contador"] = (self.globals["contador"] + 1) & 0xFFFFFFFF
        elif data == "inicio":
            fr.vars["inicio"] = self.globals["ms_transcurridos"]
        elif data == "moder":
            fr.vars["moder"] = 0xABFF5555
        elif data == "hsidiv":
            fr.vars["hsidiv"] = 1
        self._advance()
        return True

    def bp_here(self):
        fr = self.top
        for num, bp in self.bps.items():
            if bp["file"] == fr.file and bp["line"] == fr.line:
                return num
        return None

    # ---------------------------------------------------------- formato MI
    def frame_mi(self, fr, level):
        args = [{"name": a, "value": str(fr.vars.get(a, 0))} for a in ARGS.get(fr.func, [])]
        return {"level": str(level), "addr": f"0x{fr.addr:08x}", "func": fr.func, "args": args,
                "file": fr.file, "fullname": f"{COMP_DIR}/{fr.file}", "line": str(fr.line), "arch": "armv8-m.main"}

    def stopped(self, reason, extra=""):
        fr = self.frame_mi(self.top, 0)
        fr.pop("level")
        prefix = f'reason="{reason}",' if reason else ""
        emit(f'*stopped,{prefix}{extra}frame={mi(fr)},thread-id="1",stopped-threads="all"')

    # ----------------------------------------------------------- expresiones
    def lookup(self, name):
        if name.startswith("__reg_"):
            return self.registers()[name[6:]]
        if name in self.top.vars:
            return self.top.vars[name]
        if name in self.globals:
            return self.globals[name]
        raise KeyError(name)

    def registers(self):
        fr = self.top
        regs = {n: 0 for n in REG_NAMES}
        regs.update({"r0": fr.vars.get("ms", fr.vars.get("patron", 0)), "r3": self.globals["contador"],
                     "sp": 0x20003FF0 - 16 * len(self.stack), "lr": 0x080003D9, "pc": fr.addr,
                     "xpsr": 0x01000000, "msp": 0x20003FF0 - 16 * len(self.stack), "psp": 0})
        return regs

    def evaluate(self, expr):
        expr = expr.strip()
        m = re.match(r"^([A-Za-z_]\w*)\s*=(?!=)\s*(.+)$", expr)
        if m:
            name, value = m.group(1), self.evaluate(m.group(2))
            if name in self.top.vars:
                self.top.vars[name] = value & 0xFFFFFFFF
            elif name in self.globals:
                self.globals[name] = value & 0xFFFFFFFF
            else:
                raise KeyError(name)
            return value & 0xFFFFFFFF
        py = re.sub(r"\$([A-Za-z]\w*)", r"__reg_\1", expr)
        py = re.sub(r"\b(0[xX][0-9A-Fa-f]+|\d+)[uUlL]+\b", r"\1", py)
        py = py.replace("&&", " and ").replace("||", " or ")
        tree = ast.parse(py, mode="eval")
        return int(self._eval(tree.body))

    def _eval(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            return self.lookup(node.id)
        if isinstance(node, ast.UnaryOp):
            v = self._eval(node.operand)
            return {ast.USub: lambda: -v, ast.UAdd: lambda: v, ast.Invert: lambda: ~v & 0xFFFFFFFF,
                    ast.Not: lambda: int(not v)}[type(node.op)]()
        if isinstance(node, ast.BinOp):
            a, b = self._eval(node.left), self._eval(node.right)
            ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b,
                   ast.Div: lambda: a // b, ast.Mod: lambda: a % b, ast.BitAnd: lambda: a & b,
                   ast.BitOr: lambda: a | b, ast.BitXor: lambda: a ^ b, ast.LShift: lambda: a << b,
                   ast.RShift: lambda: a >> b}
            return ops[type(node.op)]()
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            a, b = self._eval(node.left), self._eval(node.comparators[0])
            return int({ast.Eq: a == b, ast.NotEq: a != b, ast.Lt: a < b, ast.LtE: a <= b,
                        ast.Gt: a > b, ast.GtE: a >= b}[type(node.ops[0])])
        if isinstance(node, ast.BoolOp):
            vals = [self._eval(v) for v in node.values]
            return int(all(vals) if isinstance(node.op, ast.And) else any(vals))
        raise ValueError("expresión no soportada por el simulador")


T = Target()


def find_location(loc):
    """'main', 'contador_paso', 'placa.c:56', 'src/placa.c:56' -> (func, idx)."""
    m = re.match(r"^(.+):(\d+)$", loc)
    if not m:
        if loc not in PROGRAM:
            raise ValueError(f'Function "{loc}" not defined.')
        return loc, 0
    fname, line = m.group(1), int(m.group(2))
    best = None
    for func, (file, _, body, _) in PROGRAM.items():
        if not (file == fname or file.endswith("/" + fname)):
            continue
        for idx, (ln, _, _) in enumerate(body):
            if ln >= line and (best is None or ln < best[2]):
                best = (func, idx, ln)
    if best is None:
        raise ValueError(f'No line {line} in file "{fname}".')
    return best[0], best[1]


def run_until(mode, depth):
    """Hilo de ejecución de continue/next/step/finish."""
    reason, extra = None, ""
    while True:
        if T.interrupt.is_set():
            reason, extra = "signal-received", 'signal-name="SIGINT",signal-meaning="Interrupt",'
            break
        if not T.exec_one(realtime=(mode == "continue")):
            reason, extra = "signal-received", 'signal-name="SIGINT",signal-meaning="Interrupt",'
            break
        num = T.bp_here()
        if num is not None and (mode == "continue" or len(T.stack) > depth or mode == "finish"):
            bp = T.bps[num]
            reason = "breakpoint-hit"
            extra = f'disp="{"del" if bp["temp"] else "keep"}",bkptno="{num}",'
            if bp["temp"]:
                del T.bps[num]
            break
        if mode == "step":
            reason = "end-stepping-range"
            break
        if mode == "next" and len(T.stack) <= depth:
            reason = "end-stepping-range"
            break
        if mode == "finish" and len(T.stack) < depth:
            reason = "function-finished"
            break
    T.running = False
    T.interrupt.clear()
    T.stopped(reason, extra)


def handle(token, cmd):
    def done(rest=""):
        emit(f"{token}^done" + ("," + rest if rest else ""))

    def error(msg):
        emit(f"{token}^error,msg={c(msg)}")

    name = cmd.split(" ", 1)[0]
    arg = cmd[len(name):].strip()
    busy = T.running and name not in ("-exec-interrupt", "-gdb-exit", "-target-detach", "-gdb-set")
    if busy:
        return error("Cannot execute this command while the target is running.\nUse the \"interrupt\" command to stop the target\nand then try again.")

    if name == "-gdb-set":
        return done()
    if name == "-target-select":
        emit('=thread-group-started,id="i1",pid="42000"')
        emit('=thread-created,id="1",group-id="i1"')
        fr = T.top
        emit(f'~{c(f"{fr.func} () at {fr.file}:{fr.line}")}')
        T.stopped(None)
        return emit(f"{token}^connected")
    if name == "-data-read-memory-bytes":
        addr, count = arg.split()
        addr = int(addr, 0)
        value = int(os.environ.get("BENCH_MOCK_STM32_ID", "0") or "0", 0) or 0x474
        data = value.to_bytes(4, "little") if addr in IDCODE_ADDRS else bytes(4)
        data = (data * ((int(count) + 3) // 4))[:int(count)]
        return done(f'memory=[{{begin="0x{addr:08x}",offset="0x00000000",end="0x{addr + len(data):08x}",'
                    f'contents="{data.hex()}"}}]')
    if name == "-interpreter-exec":
        text = ast.literal_eval(arg.split(" ", 1)[1]) if '"' in arg else ""
        if text == "monitor reset halt":
            T.reset_state()
            emit('@"[stm32h5x.cpu] halted due to debug-request, current mode: Thread \\n"')
            emit('@"xPSR: 0x01000000 pc: 0x08000254 msp: 0x20004000\\n"')
            return done()
        if text in ("maintenance flush register-cache", "flushregs") or text.startswith("monitor rtt "):
            return done()
        return error(f'Undefined command: "{text}".  Try "help".')
    if name == "-target-download":
        for sec, size in ((".isr_vector", 600), (".text", 308), (".data", 16)):
            emit(f'+download,{{section="{sec}",section-size="{size}",total-size="1848"}}')
            time.sleep(0.15)
        T.reset_state()
        return done('address="0x08000254",load-size="924",transfer-rate="7392",write-rate="231"')
    if name == "-data-list-register-names":
        return done("register-names=" + mi(REG_NAMES))
    if name == "-data-list-register-values":
        nums = [int(x) for x in arg.split() if x.isdigit()]
        regs = T.registers()
        vals = [{"number": str(n), "value": f"0x{regs[REG_NAMES[n]] & 0xFFFFFFFF:x}"} for n in nums if n < len(REG_NAMES)]
        return done("register-values=" + mi(vals))
    if name == "-symbol-info-variables":
        groups = [{"filename": MAIN_C, "fullname": f"{COMP_DIR}/{MAIN_C}", "symbols": [
                      {"line": "14", "name": "contador", "type": "volatile uint32_t",
                       "description": "volatile uint32_t contador;"}]},
                  {"filename": PLACA_C, "fullname": f"{COMP_DIR}/{PLACA_C}", "symbols": [
                      {"line": "6", "name": "ms_transcurridos", "type": "volatile uint32_t",
                       "description": "volatile uint32_t ms_transcurridos;"}]}]
        return done("symbols={debug=" + mi(groups) + "}")
    if name == "-break-insert":
        temp = arg.startswith("-t ")
        loc = arg[3:].strip() if temp else arg
        if loc.startswith('"'):
            loc = ast.literal_eval(loc)
        try:
            func, idx = find_location(loc)
        except ValueError as exc:
            return error(str(exc))
        file, base, body, _ = PROGRAM[func]
        num = T.next_bp
        T.next_bp += 1
        T.bps[num] = {"file": file, "line": body[idx][0], "func": func, "temp": temp}
        return done("bkpt=" + mi({"number": str(num), "type": "breakpoint", "disp": "del" if temp else "keep",
                                  "enabled": "y", "addr": f"0x{base + idx * 6:08x}", "func": func, "file": file,
                                  "fullname": f"{COMP_DIR}/{file}", "line": str(body[idx][0]),
                                  "times": "0", "original-location": loc}))
    if name == "-break-delete":
        for n in arg.split():
            T.bps.pop(int(n), None)
        return done()
    if name in ("-exec-continue", "-exec-next", "-exec-step", "-exec-finish"):
        mode = name.split("-")[-1]
        if mode == "finish" and len(T.stack) <= 2:
            return error('"finish" not meaningful in the outermost frame.')
        emit(f"{token}^running")
        start_run_quiet(mode)
        return None
    if name == "-exec-interrupt":
        if T.running:
            T.interrupt.set()
        return done()
    if name == "-stack-list-frames":
        frames = [T.frame_mi(fr, lvl) for lvl, fr in enumerate(reversed(T.stack))]
        return done("stack=[" + ",".join("frame=" + mi(f) for f in frames) + "]")
    if name == "-stack-list-variables":
        fr = T.top
        out = [{"name": a, "arg": "1", "type": TYPES[a], "value": str(fr.vars[a])} for a in ARGS.get(fr.func, [])]
        out += [{"name": v, "type": TYPES[v], "value": str(fr.vars[v])} for v in LOCALS.get(fr.func, [])]
        return done("variables=" + mi(out))
    if name == "-data-evaluate-expression":
        expr = ast.literal_eval(arg) if arg.startswith('"') else arg
        try:
            return done(f"value={c(T.evaluate(expr))}")
        except KeyError as exc:
            return error(f'No symbol "{exc.args[0]}" in current context.')
        except (ValueError, SyntaxError, ZeroDivisionError, KeyError, TypeError):
            return error("A syntax error in expression, near `'.")
    if name == "-target-detach":
        emit('=thread-exited,id="1",group-id="i1"')
        emit('=thread-group-exited,id="i1"')
        return done()
    if name == "-gdb-exit":
        emit(f"{token}^exit")
        sys.exit(0)
    return error(f"Undefined MI command: {name.lstrip('-')}")


def start_run_quiet(mode):
    T.running = True
    T.interrupt.clear()
    depth = len(T.stack)
    emit('*running,thread-id="all"')
    threading.Thread(target=run_until, args=(mode, depth), daemon=True).start()


def main(argv):
    if "--version" in argv:
        print("GNU gdb (simulado para stm32-bench) 12.1")
        return 0
    emit('=thread-group-added,id="i1"')
    emit("(gdb)")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(\d*)(-\S+.*)$", line)
        if not m:
            emit(f'^error,msg={c("Sólo comandos MI")}')
            continue
        try:
            handle(m.group(1), m.group(2))
        except SystemExit:
            raise
        except Exception as exc:     # el simulador no se cae por un comando raro
            emit(f"{m.group(1)}^error,msg={c(f'simulador: {exc}')}")
        emit("(gdb)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
