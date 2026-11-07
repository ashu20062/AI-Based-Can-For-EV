"""Per-ID state: last value, change events, periodicity stats. Thread-safe-ish.

Owns the running view of every CAN ID seen on the bus. The TUI reads from
this, the writers write to this, and analyzers walk it at the end of a run.

State is keyed on ``(channel, can_id)``, not on ``can_id`` alone. On a
multi-bus adapter the same arbitration ID is routinely live on both buses
carrying entirely unrelated signals; merging them silently corrupts rate,
periodicity and byte-classification stats for every such ID. Single-bus
captures are unaffected — everything lands on channel 0 as before.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Deque

from ..capture.base import Frame

MAX_HISTORY = 4096
MAX_BYTE_HISTORY = 1024

IdKey = tuple[int, int]   # (channel, can_id)


def format_id(can_id: int, channel: int = 0, *, with_channel: bool = False) -> str:
    """Render an ID for display. Channel prefix only when it disambiguates."""
    base = f"0x{can_id:03X}"
    return f"ch{channel} {base}" if with_channel else base


@dataclass(slots=True)
class ByteChange:
    t: float
    can_id: int
    byte_idx: int
    old: int
    new: int
    channel: int = 0


@dataclass
class IdState:
    can_id: int
    channel: int = 0
    dlc: int = 0
    frame_count: int = 0
    first_seen_t: float = -1.0
    last_seen_t: float = -1.0
    last_data: bytes = b""
    arrival_intervals: Deque[float] = field(default_factory=lambda: deque(maxlen=512))
    byte_history: list[Deque[int]] = field(default_factory=list)
    byte_change_counts: list[int] = field(default_factory=list)
    recent_changes: Deque[ByteChange] = field(default_factory=lambda: deque(maxlen=64))

    def _ensure_byte_lanes(self, n: int) -> None:
        while len(self.byte_history) < n:
            self.byte_history.append(deque(maxlen=MAX_BYTE_HISTORY))
            self.byte_change_counts.append(0)

    def observe(self, f: Frame) -> list[ByteChange]:
        changes: list[ByteChange] = []
        self._ensure_byte_lanes(len(f.data))
        if self.frame_count == 0:
            self.first_seen_t = f.t
        else:
            self.arrival_intervals.append(f.t - self.last_seen_t)
        self.last_seen_t = f.t
        self.dlc = f.dlc
        self.frame_count += 1
        prev = self.last_data
        for i, b in enumerate(f.data):
            if i >= len(self.byte_history):
                self._ensure_byte_lanes(i + 1)
            self.byte_history[i].append(b)
            if i < len(prev) and prev[i] != b:
                self.byte_change_counts[i] += 1
                ch = ByteChange(f.t, f.can_id, i, prev[i], b, self.channel)
                self.recent_changes.append(ch)
                changes.append(ch)
            elif i >= len(prev):
                self.byte_change_counts[i] += 1
        self.last_data = bytes(f.data)
        return changes


class StateStore:
    """All known IDs; locked for cross-thread reads from the TUI."""

    def __init__(self) -> None:
        self._ids: dict[IdKey, IdState] = {}
        self._lock = Lock()
        self._all_changes: Deque[ByteChange] = deque(maxlen=MAX_HISTORY)
        self.frames_seen = 0
        self.t0: float | None = None
        self.last_t: float = 0.0
        self.channels_seen: set[int] = set()

    def observe(self, f: Frame) -> list[ByteChange]:
        with self._lock:
            if self.t0 is None:
                self.t0 = f.t
            self.last_t = f.t
            self.frames_seen += 1
            self.channels_seen.add(f.channel)
            key = (f.channel, f.can_id)
            s = self._ids.get(key)
            if s is None:
                s = IdState(can_id=f.can_id, channel=f.channel)
                self._ids[key] = s
            changes = s.observe(f)
            self._all_changes.extend(changes)
            return changes

    @property
    def is_multichannel(self) -> bool:
        """True once frames from more than one bus have been seen.

        Drives whether displays bother printing a channel prefix, so
        single-bus captures render exactly as they always did.
        """
        return len(self.channels_seen) > 1

    def snapshot_ids(self) -> list[IdState]:
        with self._lock:
            return list(self._ids.values())

    def get(self, can_id: int, channel: int = 0) -> IdState | None:
        with self._lock:
            return self._ids.get((channel, can_id))

    def recent_changes(self, n: int = 32) -> list[ByteChange]:
        with self._lock:
            return list(self._all_changes)[-n:]
