"""Sink protocol — anything that wants a copy of the live frame stream.

The capture backend is the single owner of the hardware. Everything else
(TUI state, CSV writer, SavvyCAN feed) is a sink hanging off that one stream,
so a session never opens the adapter twice.

Sinks must not raise: a broken sink degrades that one output, it never takes
down the capture. ``LiveSession`` wraps every call, but sinks should swallow
their own expected errors and surface them via ``stats()``.
"""
from __future__ import annotations

from typing import Protocol

from ..capture.base import Frame


class Sink(Protocol):
    name: str

    def write(self, frame: Frame) -> None:
        """Consume one frame. Must be cheap and must not raise."""
        ...

    def close(self) -> None:
        ...

    def stats(self) -> str:
        """One-line status for the TUI header."""
        ...
