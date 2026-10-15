"""Dispositivos USB conectados en Windows (modo PC), sin dependencias.

En Linux el banco lee /sys/bus/usb/devices. En Windows se usa SetupAPI (la misma
lista que el Administrador de dispositivos) por ctypes: es instantáneo, a
diferencia de consultar WMI, que tarda segundos.

Cada dispositivo trae VID, PID y número de serie dentro de su "instance id":
    USB\\VID_0483&PID_374B\\066DFF485550755187121832
Si el dispositivo no informa una serie, Windows pone algo como 5&2A3B1C0&0&2 (con
"&"), que no sirve para identificarlo.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import List, Optional

_ID_RE = re.compile(r"^USB\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})\\(.+)$")


@dataclass(frozen=True)
class WinUsbDevice:
    instance_id: str
    vid: str                     # "0483" (minúsculas)
    pid: str
    serial: Optional[str]
    name: Optional[str] = None
    location: Optional[str] = None   # "Port_#0002.Hub_#0003"
    problem: int = 0                 # código de problema del Administrador de dispositivos (28 = sin driver)

    @property
    def has_driver(self) -> bool:
        return self.problem == 0


def parse_instance_id(instance_id: str) -> Optional[WinUsbDevice]:
    """VID, PID y serie de un instance id de USB; None si no es un dispositivo USB (o es una interfaz)."""
    m = _ID_RE.match(instance_id)
    if not m:
        return None
    tail = m.group(3)
    serial = None if "&" in tail else tail
    return WinUsbDevice(instance_id=instance_id, vid=m.group(1).lower(), pid=m.group(2).lower(), serial=serial)


def present_devices() -> List[WinUsbDevice]:
    """Dispositivos USB conectados ahora ([] fuera de Windows o si SetupAPI falla)."""
    if sys.platform != "win32":
        return []
    try:
        return _setupapi_devices()
    except OSError:
        return []


def _setupapi_devices() -> List[WinUsbDevice]:
    import ctypes
    from ctypes import wintypes

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    cfgmgr = ctypes.WinDLL("cfgmgr32")

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", ctypes.c_byte * 16),
                    ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

    DIGCF_PRESENT, DIGCF_ALLCLASSES = 0x2, 0x4
    SPDRP_DEVICEDESC, SPDRP_FRIENDLYNAME, SPDRP_LOCATION_INFORMATION = 0x0, 0xC, 0xD
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA),
                                                     wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    cfgmgr.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
                                             wintypes.DWORD, wintypes.ULONG]

    handle = setupapi.SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if handle in (None, INVALID_HANDLE):
        raise ctypes.WinError(ctypes.get_last_error())

    def prop(info, which) -> Optional[str]:
        buf = ctypes.create_unicode_buffer(512)
        ok = setupapi.SetupDiGetDeviceRegistryPropertyW(handle, ctypes.byref(info), which, None,
                                                         buf, ctypes.sizeof(buf), None)
        return buf.value or None if ok else None

    out: List[WinUsbDevice] = []
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                break
            index += 1
            buf = ctypes.create_unicode_buffer(512)
            if not setupapi.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(info), buf, len(buf), None):
                continue
            dev = parse_instance_id(buf.value)
            if dev is None:
                continue
            status, problem = wintypes.ULONG(), wintypes.ULONG()
            cfgmgr.CM_Get_DevNode_Status(ctypes.byref(status), ctypes.byref(problem), info.DevInst, 0)
            out.append(WinUsbDevice(
                instance_id=dev.instance_id, vid=dev.vid, pid=dev.pid, serial=dev.serial,
                name=prop(info, SPDRP_FRIENDLYNAME) or prop(info, SPDRP_DEVICEDESC),
                location=prop(info, SPDRP_LOCATION_INFORMATION), problem=int(problem.value)))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)
    return out
