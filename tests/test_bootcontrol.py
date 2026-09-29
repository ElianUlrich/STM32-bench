"""Flujo de BootControl (manual, gpio, serial_command) y DFU completo en modo mock."""
import dataclasses

import pytest

from app import mock_state
from app.bench import Bench, JobRequest
from app.bootcontrol import wait_for_dfu
from app.bootcontrol.gpio import GpioBootControl
from app.bootcontrol.manual import ManualBootControl
from app.bootcontrol.mock import MockBootControl
from app.bootcontrol.serial_cmd import SerialCommandBootControl
from app.events import EventBus
from app.history import History
from app.programmers.dfu import DfuDevice
from app.programmers.runner import JobCancelled, JobContext, JobFailed, JobRunner, new_job
from app.sample_firmware import demo_images
from app.settings import BootControlConfig, GpioLineConfig

from .conftest import run


class RecordingBus(EventBus):
    def __init__(self):
        super().__init__()
        self.events = []

    def publish(self, event):
        self.events.append(event)
        super().publish(event)


def make_ctx(tmp_path):
    bus = RecordingBus()
    runner = JobRunner(bus, History(tmp_path / "h.jsonl"))
    return JobContext(runner, new_job("flash", "dfu", "test")), bus


GPIO_CFG = BootControlConfig(
    backend="gpio",
    boot0=GpioLineConfig("gpiochip4", 11, active_low=False),
    nrst=GpioLineConfig("gpiochip4", 12, active_low=True),
    reset_pulse_ms=5, settle_ms=5,
)


class FakeDriver:
    instances = []

    def __init__(self, lines):
        self.lines = lines
        self.calls = []
        self.released = False
        FakeDriver.instances.append(self)

    def set(self, name, active):
        self.calls.append((name, active))

    def release(self):
        self.released = True


def test_gpio_sequence(tmp_path):
    FakeDriver.instances.clear()
    boot = GpioBootControl(GPIO_CFG, driver_factory=FakeDriver)
    ctx, _ = make_ctx(tmp_path)

    async def flow():
        await boot.enter(ctx)
        await boot.exit(ctx, left_bootloader=True)

    run(flow())
    driver = FakeDriver.instances[0]
    # entrar: BOOT0=1, pulso de NRST; salir: BOOT0=0, pulso de NRST
    assert driver.calls == [("boot0", True), ("nrst", True), ("nrst", False),
                            ("boot0", False), ("nrst", True), ("nrst", False)]
    assert driver.released
    assert driver.lines["nrst"].active_low is True   # la polaridad llega al driver


def test_gpio_exit_without_enter_still_restores(tmp_path):
    """Si algo falla antes de entrar, exit igual deja BOOT0=0 y resetea."""
    FakeDriver.instances.clear()
    boot = GpioBootControl(GPIO_CFG, driver_factory=FakeDriver)
    ctx, _ = make_ctx(tmp_path)
    run(boot.exit(ctx, left_bootloader=False))
    assert FakeDriver.instances[0].calls[0] == ("boot0", False)
    assert FakeDriver.instances[0].released


def test_manual_prompts_user(tmp_path):
    boot = ManualBootControl(timeout_s=30)
    ctx, bus = make_ctx(tmp_path)
    run(boot.enter(ctx))
    prompts = [e for e in bus.events if e["type"] == "prompt"]
    assert prompts and "BOOT0" in prompts[0]["message"] and prompts[0]["timeout_s"] == 30
    run(boot.exit(ctx, left_bootloader=True))
    assert any("BOOT0" in w for w in ctx.job.warnings)


def test_serial_command_sends_configured_bytes(tmp_path):
    class FakeSerial:
        sent = []

        def send_to_port(self, port, baud, data):
            self.sent.append((port, baud, data))

    cfg = BootControlConfig(backend="serial_command", serial_port="/dev/ttyACM0", serial_baud=115200,
                            serial_command="BOOTLOADER\n")
    serial = FakeSerial()
    ctx, _ = make_ctx(tmp_path)
    run(SerialCommandBootControl(cfg, serial).enter(ctx))
    assert serial.sent == [("/dev/ttyACM0", 115200, b"BOOTLOADER\n")]


DEV = DfuDevice(vid_pid="0483:df11", serial="ABC", path="1-1", devnum=3)


def test_wait_for_dfu_appears(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    calls = {"n": 0}

    async def list_fn():
        calls["n"] += 1
        return [DEV] if calls["n"] >= 3 else []

    dev = run(wait_for_dfu(ctx, list_fn, lambda d: d.serial == "ABC", timeout_s=5, poll_s=0.01))
    assert dev is DEV and calls["n"] == 3


def test_wait_for_dfu_timeout(tmp_path):
    ctx, _ = make_ctx(tmp_path)

    async def list_fn():
        return [DEV]

    with pytest.raises(JobFailed, match="No apareció"):
        run(wait_for_dfu(ctx, list_fn, lambda d: d.serial == "OTRO", timeout_s=0.05, poll_s=0.01))


def test_wait_for_dfu_cancel(tmp_path):
    ctx, _ = make_ctx(tmp_path)

    async def list_fn():
        ctx.cancel()
        return []

    with pytest.raises(JobCancelled):
        run(wait_for_dfu(ctx, list_fn, lambda d: True, timeout_s=5, poll_s=0.01))


def test_mock_boot_control_toggles_device(tmp_path):
    boot = MockBootControl("gpio", tmp_path, delay_s=0)
    ctx, _ = make_ctx(tmp_path)
    assert mock_state.dfu_present(tmp_path) is None
    run(boot.enter(ctx))
    assert mock_state.dfu_present(tmp_path) == mock_state.MOCK_DFU_SERIAL
    run(boot.exit(ctx, True))
    assert mock_state.dfu_present(tmp_path) is None


# --- flujo DFU completo con dfu-util simulado ----------------------------------------

def _bench(settings, never_appears=False, backend="gpio"):
    bench = Bench(settings)
    bench.boot = MockBootControl(backend, settings.state_dir, delay_s=0.2, never_appears=never_appears)
    bench.cfg = dataclasses.replace(
        bench.cfg, boot_control=dataclasses.replace(bench.cfg.boot_control, timeout_s=3))
    return bench


def _write(settings, rel, data):
    path = settings.projects_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _run_job(bench, req):
    async def go():
        import asyncio
        bench.bus.attach_loop(asyncio.get_running_loop())
        job = await bench.start_job(req)
        await bench.runner.wait_idle(60)
        return job
    return run(go())


def test_dfu_flash_hex_then_verify(settings):
    bench = _bench(settings)
    _write(settings, "p/fw.hex", demo_images()["hex"])
    job = _run_job(bench, JobRequest(action="flash", chip="stm32h723vg", method="dfu", file="p/fw.hex"))
    assert job.state == "ok", job.message
    assert mock_state.dfu_present(settings.state_dir) is None   # salió del bootloader
    job = _run_job(bench, JobRequest(action="verify", chip="stm32h723vg", method="dfu", file="p/fw.hex"))
    assert job.state == "ok", job.message
    assert [e["state"] for e in bench.history.list()] == ["ok", "ok"]


def test_dfu_verify_detects_mismatch(settings):
    bench = _bench(settings)
    _write(settings, "p/a.bin", b"\x01" * 64)
    _write(settings, "p/b.bin", b"\x02" * 64)
    assert _run_job(bench, JobRequest(action="flash", chip="stm32h723vg", method="dfu", file="p/a.bin")).state == "ok"
    job = _run_job(bench, JobRequest(action="verify", chip="stm32h723vg", method="dfu", file="p/b.bin"))
    assert job.state == "error" and "0x08000000" in job.message


def test_dfu_gap_warning_and_external_flash_error(settings):
    bench = _bench(settings)
    images = demo_images()
    _write(settings, "c/gap.hex", images["hex_gap"])
    _write(settings, "c/ext.hex", images["hex_ext"])
    job = _run_job(bench, JobRequest(action="flash", chip="stm32h723vg", method="dfu", file="c/gap.hex"))
    assert job.state == "ok" and any("discontinuos" in w for w in job.warnings)
    job = _run_job(bench, JobRequest(action="flash", chip="stm32h723vg", method="dfu", file="c/ext.hex"))
    assert job.state == "error" and "fuera de la flash" in job.message


def test_dfu_timeout_when_device_never_appears(settings):
    bench = _bench(settings, never_appears=True)
    _write(settings, "p/fw.bin", b"\x00" * 16)
    job = _run_job(bench, JobRequest(action="flash", chip="stm32h503cb", method="dfu", file="p/fw.bin"))
    assert job.state == "error" and "No apareció" in job.message


def test_dfu_erase_and_leave_error_is_benign(settings, monkeypatch):
    monkeypatch.setenv("BENCH_MOCK_DFU_LEAVE_ERROR", "1")
    bench = _bench(settings, backend="manual")
    _write(settings, "p/fw.bin", b"\x00" * 16)
    job = _run_job(bench, JobRequest(action="flash", chip="stm32h503cb", method="dfu", file="p/fw.bin"))
    assert job.state == "ok", job.message
    assert any("BOOT0" in w for w in job.warnings)   # manual: recordar bajar BOOT0
    job = _run_job(bench, JobRequest(action="erase", chip="stm32h503cb", method="dfu"))
    assert job.state == "ok", job.message


def test_swd_flash_in_mock(settings):
    bench = _bench(settings)
    _write(settings, "p/fw.elf", demo_images()["elf"])
    job = _run_job(bench, JobRequest(action="flash", chip="stm32h723vg", file="p/fw.elf",
                                     probe_serial="066DFF485550755187121832"))
    assert job.state == "ok", job.message
    _write(settings, "p/fw-falla.elf", demo_images()["elf"])
    job = _run_job(bench, JobRequest(action="flash", chip="stm32h723vg", file="p/fw-falla.elf",
                                     probe_serial="066DFF485550755187121832"))
    assert job.state == "error"
