"""Non-blocking single-char keyboard reader. POSIX-only (raw mode)."""
from __future__ import annotations

import select
import sys
import termios
import tty


class RawKeyReader:
    def __init__(self):
        self._fd = sys.stdin.fileno()
        self._saved: list | None = None

    def __enter__(self) -> "RawKeyReader":
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def poll(self, timeout: float = 0.0) -> str | None:
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            return sys.stdin.read(1)
        return None
