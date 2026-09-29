"""Historial de operaciones recientes (JSON Lines en el directorio de estado)."""
from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List


class History:
    def __init__(self, path: Path, max_entries: int = 200) -> None:
        self.path = path
        self.max_entries = max_entries
        self._entries: Deque[Dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._lines_in_file = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._entries.append(json.loads(line))
                        self._lines_in_file += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    def add(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._entries.append(entry)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self._lines_in_file >= 2 * self.max_entries:
                    # Compacta el archivo para que no crezca sin límite en la SD.
                    tmp = self.path.with_suffix(".tmp")
                    with open(tmp, "w", encoding="utf-8") as fh:
                        for item in self._entries:
                            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
                    tmp.replace(self.path)
                    self._lines_in_file = len(self._entries)
                else:
                    with open(self.path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    self._lines_in_file += 1
            except OSError:
                pass  # el historial no debe romper una operación

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._entries))
