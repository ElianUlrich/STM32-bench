"""Selección de método (swd/dfu) según el chip y capacidades por método."""
import pytest

from app.chips import load_chips
from app.programmers import base
from app.settings import REPO_ROOT

CHIPS = {c.id: c for c in load_chips(REPO_ROOT / "config" / "chips.yaml")}


def test_default_method():
    assert base.select_method(CHIPS["stm32h723vg"], None) == "swd"
    assert base.select_method(CHIPS["stm32h723vg"], "") == "swd"


def test_requested_method():
    assert base.select_method(CHIPS["stm32h503cb"], "dfu") == "dfu"
    assert base.select_method(CHIPS["stm32h503cb"], "swd") == "swd"


def test_method_not_supported_by_chip():
    with pytest.raises(base.MethodError, match="no tiene configurado"):
        base.select_method(CHIPS["stm32f103c8"], "dfu")


def test_unknown_method():
    with pytest.raises(base.MethodError, match="desconocido"):
        base.select_method(CHIPS["stm32h723vg"], "jtag")


@pytest.mark.parametrize("action", ["flash", "verify", "erase", "reset", "halt", "resume", "gdb"])
def test_swd_supports_everything(action):
    base.check_action("swd", action)


@pytest.mark.parametrize("action", ["flash", "verify", "erase"])
def test_dfu_supported(action):
    base.check_action("dfu", action)


@pytest.mark.parametrize("action", ["reset", "halt", "resume", "gdb"])
def test_dfu_unsupported_with_reason(action):
    with pytest.raises(base.MethodError) as exc:
        base.check_action("dfu", action)
    assert "USB DFU" in str(exc.value) and "SWD" in str(exc.value)


def test_unknown_action():
    with pytest.raises(base.MethodError):
        base.check_action("swd", "format_c")
