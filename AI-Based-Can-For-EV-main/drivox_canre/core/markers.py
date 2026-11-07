"""Stimulus markers: hotkey-driven labels in the capture stream."""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


# Default hotkey -> label map. Customisable via the registry.
DEFAULT_MARKERS: dict[str, str] = {
    "S": "sport_on",
    "D": "drive_on",
    "R": "regen_on",
    "r": "regen_off",
    "B": "brake_hard",
    "b": "brake_release",
    "A": "ac_on",
    "a": "ac_off",
    "H": "headlights_on",
    "h": "headlights_off",
    "T": "turn_signal_left",
    "t": "turn_signal_right",
    "P": "park_brake",
    "G": "gear_change",
    "M": "custom",   # opens a prompt
    "0": "baseline_start",
    "1": "phase_1_start",
    "2": "phase_2_start",
    "3": "phase_3_start",
    "4": "phase_4_start",
    "5": "phase_5_start",
}


@dataclass(slots=True)
class Marker:
    t: float
    label: str
    note: str = ""


@dataclass
class MarkerLog:
    markers: list[Marker] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def add(self, t: float, label: str, note: str = "") -> Marker:
        m = Marker(t=t, label=label, note=note)
        with self._lock:
            self.markers.append(m)
        return m

    def all(self) -> list[Marker]:
        with self._lock:
            return list(self.markers)

    def latest(self) -> Optional[Marker]:
        with self._lock:
            return self.markers[-1] if self.markers else None

    def save(self, path) -> None:
        import csv
        from pathlib import Path
        p = Path(path)
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "label", "note"])
            for m in self.all():
                w.writerow([f"{m.t:.6f}", m.label, m.note])
