"""Bus de eventos en memoria para Server-Sent Events.

Los productores (lectores de procesos, puerto serie) corren en hilos, por eso
publish() es thread-safe: si se llama desde otro hilo reprograma el envío en
el event loop de asyncio.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

# Tipos de evento que se reenvían a un cliente que se conecta tarde.
REPLAY_TYPES = ("log", "serial")


class EventBus:
    def __init__(self, replay_size: int = 500, queue_size: int = 2000) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[int] = None
        self._replay: Dict[str, Deque[Dict[str, Any]]] = {t: deque(maxlen=replay_size) for t in REPLAY_TYPES}
        self._queue_size = queue_size

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()

    def publish(self, event: Dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        if self._loop is None or threading.get_ident() == self._loop_thread:
            self._dispatch(event)
        else:
            try:
                self._loop.call_soon_threadsafe(self._dispatch, event)
            except RuntimeError:
                pass  # el loop se cerró (apagado)

    def _dispatch(self, event: Dict[str, Any]) -> None:
        if event.get("type") in self._replay:
            self._replay[event["type"]].append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Cliente lento: se descarta el evento para no bloquear al resto.
                pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def replay(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for t in REPLAY_TYPES:
            items.extend(self._replay[t])
        items.sort(key=lambda e: e.get("ts", 0))
        return items

    def clear_replay(self, event_type: str) -> None:
        if event_type in self._replay:
            self._replay[event_type].clear()
