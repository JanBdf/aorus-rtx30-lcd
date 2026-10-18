# Vendored from c-drew/gigabyte-aorus-lcd-linux (commit 3aa594f), MIT License,
# Copyright (c) 2026 Alban Creton, Copyright (c) 2026 c-drew. See LICENSE.
"""Cross-process bus lock, compatible with c-drew's `aorus-lcd` tool (same socket name)."""
import socket
import threading
import time

from .base import TransportError


class BusLock:
    """Cross-process lock (daemon vs CLI) as an abstract Unix socket: works across
    users and private /tmp, and the kernel drops it if the holder dies. Also
    re-entrant and thread-safe within a process (web server + stats loop)."""

    def __init__(self, name="aorus-lcd", timeout=30.0):
        self.name, self.timeout = name, timeout
        self._sock = None
        self._depth = 0
        self._thread_lock = threading.RLock()

    def __enter__(self):
        if not self._thread_lock.acquire(timeout=self.timeout):
            raise TransportError("the bus is busy")
        try:
            if self._depth == 0:
                self._bind()
        except BaseException:
            self._thread_lock.release()
            raise
        self._depth += 1
        return self

    def _bind(self):
        deadline = time.monotonic() + self.timeout
        while True:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind("\0" + self.name)
                self._sock = sock
                return
            except OSError:
                sock.close()
                if time.monotonic() > deadline:
                    raise TransportError("another aorus-lcd process holds the bus") from None
                time.sleep(0.05)

    def __exit__(self, *exc):
        self._depth -= 1
        if self._depth == 0:
            self._sock.close()
            self._sock = None
        self._thread_lock.release()
