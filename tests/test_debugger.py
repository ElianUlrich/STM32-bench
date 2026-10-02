"""Depurador en la web: protocolo MI, validaciones, fuentes del .elf y flujo completo con mock_gdb."""
import time
from pathlib import Path

import pytest

from app.debugger.mi import parse_line, quote
from app.debugger.session import DebugError, check_assignment, check_expression
from app.elfinfo import DebugInfoError, load_debug_info, read_line_info
from app.settings import REPO_ROOT

SERIAL = "066DFF485550755187121832"
ELF = "h503-contador/build/03_contador_binario.elf"
REAL_ELF = REPO_ROOT / "firmware" / "h503_leds" / "build" / "03_contador_binario.elf"


# --------------------------------------------------------------------- GDB/MI

def test_mi_result_with_nested_values():
    rec = parse_line('12^done,stack=[frame={level="0",func="main",line="29"},frame={level="1",func="Reset_Handler"}]')
    assert rec.kind == "result" and rec.token == 12 and rec.cls == "done"
    assert [f["func"] for f in rec.results["stack"]] == ["main", "Reset_Handler"]


def test_mi_async_stream_and_octal_escapes():
    rec = parse_line('*stopped,reason="breakpoint-hit",bkptno="1",frame={file="src/placa.c",args=[]}')
    assert rec.kind == "exec" and rec.cls == "stopped" and rec.results["frame"]["args"] == []
    assert parse_line('~"Remote debugging using x\\n"').text == "Remote debugging using x\n"
    assert parse_line('~"Configuraci\\303\\263n \\"ok\\""').text == 'Configuración "ok"'
    assert parse_line("(gdb)").kind == "prompt" and parse_line("") is None


def test_mi_quote():
    assert quote('placa.c:56') == '"placa.c:56"'
    assert quote('a "b" \\c') == '"a \\"b\\" \\\\c"'
    with pytest.raises(ValueError):
        quote("x\n-gdb-exit")


# ------------------------------------------------------------------ validación

@pytest.mark.parametrize("expr", ["contador", "contador & 0xFF", "$pc + 4", "buf[3]", "p->campo", "(int)x * 2"])
def test_expression_ok(expr):
    assert check_expression(expr) == expr


@pytest.mark.parametrize("expr", ["", "$_shell(\"id\")", "$_as_string(x)", "$1", "x\ny", "a" * 201, "$"])
def test_expression_rejected(expr):
    with pytest.raises(DebugError):
        check_expression(expr)


def test_assignment():
    assert check_assignment("contador", "0xA9") == "contador=0xA9"
    assert check_assignment("s.campo", "-3") == "s.campo=-3"
    for name, value in (("contador", "x+1"), ("f()", "1"), ("a;b", "1"), ("contador", "1; kill"), ("x", "")):
        with pytest.raises(DebugError):
            check_assignment(name, value)


# ---------------------------------------------------------------- fuentes .elf

def test_real_elf_sources(tmp_path):
    proj = tmp_path / "p" / "h503"
    (proj / "build").mkdir(parents=True)
    (proj / "src").mkdir()
    (proj / "build" / REAL_ELF.name).write_bytes(REAL_ELF.read_bytes())
    for f in (REAL_ELF.parent.parent / "src").glob("*.c"):
        (proj / "src" / f.name).write_bytes(f.read_bytes())
    info = load_debug_info(tmp_path / "p", proj / "build" / REAL_ELF.name)
    names = {s.base: s for s in info.sources}
    assert {"03_contador_binario.c", "placa.c", "startup_stm32h503.c"} <= set(names)
    assert 19 in names["03_contador_binario.c"].lines and 56 in names["placa.c"].lines
    assert info.has_main and info.missing == []
    src = names["placa.c"]
    assert info.location(src, 56) == "placa.c:56"
    # GDB en Linux informa la ruta de Windows con la que se compiló.
    assert info.find("src/placa.c", "C:/Users/x/h503_leds/src/placa.c") is src
    assert info.find(None, "C:\\otra\\pc\\src\\placa.c") is src


def test_sources_missing_and_found_by_name(tmp_path):
    proj = tmp_path / "p" / "h503"
    (proj / "Debug").mkdir(parents=True)
    (proj / "Debug" / "x.elf").write_bytes(REAL_ELF.read_bytes())
    (proj / "otra" / "carpeta").mkdir(parents=True)
    (proj / "otra" / "carpeta" / "placa.c").write_text("// placa\n")
    info = load_debug_info(tmp_path / "p", proj / "Debug" / "x.elf")
    assert [s["base"] for s in info.to_public()["sources"]] == ["placa.c"]
    assert "src/03_contador_binario.c" in info.missing
    ids = {s.name: s.id for s in info.sources}
    # Llegan las fuentes que faltaban: se encuentran y los números no cambian.
    (proj / "src").mkdir()
    (proj / "src" / "03_contador_binario.c").write_text("// main\n")
    import app.elfinfo as elfinfo
    elfinfo._index_cache.clear()
    info2 = load_debug_info(tmp_path / "p", proj / "Debug" / "x.elf")
    assert {s.name: s.id for s in info2.sources} == ids
    assert {s["base"] for s in info2.to_public()["sources"]} == {"placa.c", "03_contador_binario.c"}


def test_elf_without_debug_info(tmp_path):
    from app.sample_firmware import demo_images
    elf = tmp_path / "x.elf"
    elf.write_bytes(demo_images()["elf"])
    with pytest.raises(DebugInfoError, match="-g"):
        read_line_info(elf)


# ------------------------------------------------------------- flujo completo

def wait_debug(client, pred, timeout=15):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = client.get("/api/debug").json()
        if pred(st):
            return st
        time.sleep(0.05)
    raise AssertionError(f"estado inesperado: {st and (st['state'], st.get('frame'))}")


def start(client, mode="load", **extra):
    body = {"chip": "stm32h503cb", "file": ELF, "probe_serial": SERIAL, "mode": mode}
    body.update(extra)
    return client.post("/api/debug/start", json=body)


def control(client, action):
    r = client.post("/api/debug/control", json={"action": action})
    assert r.status_code == 200, r.text
    return wait_debug(client, lambda s: s["state"] == "stopped" and s.get("frame"))


def test_debug_session_flow(client):
    r = start(client)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["state"] == "stopped" and st["frame"]["func"] == "main" and st["frame"]["line"] == 25
    assert client.get("/api/gdb").json()["state"] == "listening"

    sources = client.get("/api/debug/sources", params={"file": ELF}).json()["sources"]
    by_name = {s["base"]: s for s in sources}
    main_c = by_name["03_contador_binario.c"]
    text = client.get("/api/debug/source", params={"file": ELF, "id": main_c["id"]}).json()["text"]
    assert "contador_paso" in text
    assert st["frame"]["source_id"] == main_c["id"]

    # Breakpoint en contador_paso y continuar.
    st = client.post("/api/debug/breakpoint", json={"source_id": main_c["id"], "line": 19}).json()
    assert [(b["file"], b["line"]) for b in st["breakpoints"]] == [("03_contador_binario.c", 19)]
    client.post("/api/debug/control", json={"action": "continue"})
    st = wait_debug(client, lambda s: s["state"] == "stopped" and s["frame"]["func"] == "contador_paso")
    assert st["stop_reason"] == "breakpoint" and st["breakpoints"][0]["hits"] == 1
    assert [g["name"] for g in st["globals"]] == ["contador", "ms_transcurridos"]

    # Variables y expresiones.
    st = client.post("/api/debug/set", json={"name": "contador", "value": "0xA9"}).json()
    assert st["globals"][0]["value"] == "169"
    st = client.post("/api/debug/watch", json={"expr": "contador & 0xFF"}).json()
    assert st["watches"] == [{"expr": "contador & 0xFF", "value": "169"}]
    assert any(r["name"] == "pc" for r in st["registers"])

    # Paso a paso.
    st = control(client, "next")
    assert st["frame"]["line"] == 20
    st = control(client, "step")
    assert st["frame"]["func"] == "leds_mostrar"
    assert st["locals"][0] == {"name": "patron", "type": "uint8_t", "arg": True, "value": str(0xAA)}
    st = control(client, "finish")
    assert st["frame"]["func"] in ("contador_paso", "main")

    # Sacar el breakpoint, correr y pausar.
    client.post("/api/debug/breakpoint", json={"source_id": main_c["id"], "line": 19})
    client.post("/api/debug/control", json={"action": "continue"})
    wait_debug(client, lambda s: s["state"] == "running")
    st = control(client, "pause")
    assert st["stop_reason"] == "pausa"

    st = control(client, "reset")
    assert st["frame"]["func"] == "Reset_Handler"

    st = client.post("/api/debug/stop").json()
    assert st["state"] == "off" and st["watches"] == [{"expr": "contador & 0xFF"}]


def test_breakpoint_while_running(client):
    assert start(client, mode="attach").status_code == 200
    client.post("/api/debug/control", json={"action": "continue"})
    wait_debug(client, lambda s: s["state"] == "running")
    sources = client.get("/api/debug/sources", params={"file": ELF}).json()["sources"]
    main_c = next(s for s in sources if s["base"] == "03_contador_binario.c")
    r = client.post("/api/debug/breakpoint", json={"source_id": main_c["id"], "line": 19})
    assert r.status_code == 200, r.text
    st = wait_debug(client, lambda s: s["state"] == "stopped" and s["frame"]["func"] == "contador_paso")
    assert st["stop_reason"] == "breakpoint"
    client.post("/api/debug/stop")


def test_debug_refuses_wrong_chip(client, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_STM32_ID", "0x483")
    r = start(client)
    assert r.status_code == 400 and "CHIP EQUIVOCADO" in r.json()["detail"]
    assert client.get("/api/debug").json()["state"] == "error"


def test_debug_requires_binding_and_elf(client):
    r = start(client, chip="stm32h723vg")
    assert r.status_code == 400 and "STM32H503" in r.json()["detail"]
    r = start(client, file="demo-blink/Debug/demo-blink.hex")
    assert r.status_code == 400 and ".elf" in r.json()["detail"]
    r = start(client, file="demo-blink/Debug/demo-blink.elf")          # sin información de depuración
    assert r.status_code == 400 and "-g" in r.json()["detail"]
    r = start(client, file="../fuera.elf")
    assert r.status_code == 400
    r = start(client, mode="borrar")
    assert r.status_code == 400


def test_debug_bad_requests(client):
    assert start(client, mode="attach").status_code == 200
    assert client.post("/api/debug/watch", json={"expr": '$_shell("id")'}).status_code == 400
    assert client.post("/api/debug/set", json={"name": "contador", "value": "1; x"}).status_code == 400
    assert client.post("/api/debug/control", json={"action": "shell"}).status_code == 400
    assert client.post("/api/debug/breakpoint", json={"source_id": 99, "line": 1}).status_code == 400
    assert client.get("/api/debug/source", params={"file": ELF, "id": 99}).status_code == 404
    assert start(client).status_code == 400                       # ya hay una sesión
    client.post("/api/debug/stop")


def test_flash_stops_debug_session(client):
    assert start(client, mode="attach").status_code == 200
    r = client.post("/api/jobs", json={"action": "flash", "chip": "stm32h503cb", "probe_serial": SERIAL,
                                       "file": ELF})
    assert r.status_code == 409 and r.json()["code"] == "gdb_active"
    r = client.post("/api/jobs", json={"action": "flash", "chip": "stm32h503cb", "probe_serial": SERIAL,
                                       "file": ELF, "stop_gdb": True})
    assert r.status_code == 202, r.text
    assert client.get("/api/debug").json()["state"] == "off"
    end = time.time() + 20
    while client.get("/api/jobs/current").json()["job"] is not None and time.time() < end:
        time.sleep(0.1)


# ------------------------------------------------------------ proyecto en .zip

def make_zip(entries):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def project_zip(extra=None):
    src = REAL_ELF.parent.parent / "src"
    entries = {"h503/build/03_contador_binario.elf": REAL_ELF.read_bytes(),
               "h503/build/03_contador_binario.o": b"objeto",
               "h503/.settings/lang.prefs": b"x"}
    for f in src.glob("*.[ch]"):
        entries[f"h503/src/{f.name}"] = f.read_bytes()
    entries.update(extra or {})
    return make_zip(entries)


def upload_zip(client, data, target="stm32h503cb", name="h503.zip"):
    return client.post("/api/files/upload", files={"file": (name, data, "application/zip")},
                       data={"project": "zips", "target": target})


def test_upload_project_zip_and_debug_sources(client):
    r = upload_zip(client, project_zip({"../afuera.c": b"x", "h503/src/../../afuera2.c": b"x"}))
    assert r.status_code == 200, r.text
    res = r.json()
    elf = "_subidos/zips/h503/build/03_contador_binario.elf"
    assert res["folder"] == "_subidos/zips/h503" and res["path"] == elf and res["sources"] == len(list((REAL_ELF.parent.parent / "src").glob("*.[ch]")))
    assert any("ignoraron 4" in w for w in res["warnings"])
    files = [f for p in client.get("/api/files").json()["projects"] for f in p["files"]]
    assert next(f for f in files if f["path"] == elf)["target"] == "stm32h503cb"
    sources = client.get("/api/debug/sources", params={"file": elf}).json()
    assert {s["base"] for s in sources["sources"]} >= {"03_contador_binario.c", "placa.c"}
    # Volver a subirlo reemplaza la carpeta.
    assert upload_zip(client, project_zip()).status_code == 200


def test_upload_zip_rejections(client):
    assert upload_zip(client, b"no es un zip").status_code == 400
    r = upload_zip(client, make_zip({"src/main.c": b"int main;"}))
    assert r.status_code == 400 and ".elf" in r.json()["detail"]
    r = upload_zip(client, project_zip(), target="esp32")
    assert r.status_code == 400 and "ESP32" in r.json()["detail"]


def test_zip_size_limit(client, monkeypatch):
    from app import files
    monkeypatch.setattr(files, "ZIP_MAX_TOTAL_BYTES", 1000)
    r = upload_zip(client, project_zip())
    assert r.status_code == 400 and "grande" in r.json()["detail"]


def test_mi_status_record_without_name():
    rec = parse_line('+download,{section=".text",section-size="308",total-size="1848"}')
    assert rec.kind == "status" and rec.cls == "download" and rec.results["_"]["section"] == ".text"


def test_sources_uploaded_during_session(client, settings):
    """Se depura un .elf suelto y después se suben sus fuentes: la sesión las toma sin reiniciar."""
    elf_rel = "_subidos/general/solo.elf"
    r = client.post("/api/files/upload", files={"file": ("solo.elf", REAL_ELF.read_bytes(), "application/octet-stream")},
                    data={"project": "general", "target": "stm32h503cb"})
    assert r.status_code == 200, r.text
    assert start(client, mode="attach", file=elf_rel).status_code == 200
    st = client.get("/api/debug").json()
    assert st["frame"]["source_id"] is None                          # sin fuentes todavía
    assert [g["name"] for g in st["globals"]] == ["contador", "ms_transcurridos"]   # globales igual
    assert upload_zip(client, project_zip(), name="h503.zip").status_code == 200    # proyecto "zips"
    src_dir = settings.projects_dir / "_subidos" / "general" / "h503" / "src"
    src_dir.mkdir(parents=True)
    for f in (REAL_ELF.parent.parent / "src").glob("*.c"):
        (src_dir / f.name).write_bytes(f.read_bytes())
    import app.elfinfo as elfinfo
    elfinfo._index_cache.clear()
    sources = client.get("/api/debug/sources", params={"file": elf_rel}).json()["sources"]
    assert {s["base"] for s in sources} >= {"placa.c", "03_contador_binario.c"}
    st = client.get("/api/debug").json()
    assert st["frame"]["source_id"] is not None and st["frame"]["file"] == "placa.c"
    client.post("/api/debug/stop")


# ------------------------------------------------------- pasos repetidos (×5, ×10...)

def test_repeat_next_n_times(client):
    st = start(client, mode="attach").json()                       # detenido en esperar_ms, línea 56
    assert (st["frame"]["func"], st["frame"]["line"]) == ("esperar_ms", 56)
    r = client.post("/api/debug/control", json={"action": "next", "count": 5})
    assert r.status_code == 200, r.text
    st = wait_debug(client, lambda s: s["state"] == "stopped" and s.get("steps") is None and s["stop_reason"] == "5 pasos")
    # 5 pasos desde la espera: vuelve a main (29), llama a contador_paso (30), vuelve al for (29)...
    assert st["frame"]["func"] == "main"
    assert int(st["globals"][0]["value"]) == 5441                   # pasó dos veces por contador_paso
    client.post("/api/debug/stop")


def test_repeat_stops_at_breakpoint(client):
    st = start(client).json()                                      # grabar y parar en main (línea 25)
    sources = client.get("/api/debug/sources", params={"file": ELF}).json()["sources"]
    main_c = next(s for s in sources if s["base"] == "03_contador_binario.c")
    client.post("/api/debug/breakpoint", json={"source_id": main_c["id"], "line": 19})
    client.post("/api/debug/control", json={"action": "next", "count": 100})
    st = wait_debug(client, lambda s: s["state"] == "stopped" and s["stop_reason"] == "breakpoint")
    assert st["frame"]["func"] == "contador_paso" and st["frame"]["line"] == 19
    client.post("/api/debug/stop")


def test_repeat_count_validation(client):
    start(client, mode="attach")
    for body in ({"action": "next", "count": 0}, {"action": "next", "count": 1001},
                 {"action": "continue", "count": 5}):
        r = client.post("/api/debug/control", json=body)
        assert r.status_code == 400, body
    client.post("/api/debug/stop")
