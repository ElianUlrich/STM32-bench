"""Vínculo entre cada archivo de firmware y la placa a la que corresponde.

Se guarda en el directorio de estado (no junto a los archivos), así no se
escribe nada dentro de las carpetas sincronizadas con Syncthing:
    {"_subidos/general/blink.bin": {"target": "esp32-devkit", "assigned": 1727700000.0}, ...}

La app sólo deja grabar un archivo en la placa que tiene asignada.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


class FirmwareMeta:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        try:
            self._data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def target_of(self, rel_path: str) -> Optional[str]:
        with self._lock:
            entry = self._data.get(rel_path)
            return entry.get("target") if entry else None

    def assign(self, rel_path: str, target: str) -> None:
        with self._lock:
            self._data[rel_path] = {"target": target, "assigned": time.time()}
            self._save()

    def forget(self, rel_path: str) -> None:
        with self._lock:
            if self._data.pop(rel_path, None) is not None:
                self._save()

    def all(self) -> Dict[str, str]:
        with self._lock:
            return {k: v.get("target") for k, v in self._data.items()}
