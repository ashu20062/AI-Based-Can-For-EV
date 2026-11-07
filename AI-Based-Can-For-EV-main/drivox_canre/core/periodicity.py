"""Inter-arrival timing analysis: periodic / bursty / event-driven."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence

import numpy as np


@dataclass(slots=True)
class PeriodicityReport:
    kind: str           # periodic | bursty | event_driven | silent | startup_only
    rate_hz: float
    period_ms: float
    jitter_ms: float
    cv: float
    notes: str = ""


def analyze(intervals: Sequence[float], frame_count: int,
            duration_s: float, first_t: float, last_t: float) -> PeriodicityReport:
    if frame_count == 0:
        return PeriodicityReport("silent", 0, 0, 0, 0.0, "no frames")
    if frame_count == 1:
        return PeriodicityReport("event_driven", 0, 0, 0, 0.0,
                                 notes=f"single frame at t={first_t:.2f}")
    if duration_s <= 0:
        return PeriodicityReport("event_driven", 0, 0, 0, 0.0)

    rate = frame_count / duration_s
    arr = np.asarray(intervals, dtype=float)
    if arr.size == 0:
        return PeriodicityReport("event_driven", rate, 0, 0, 0.0)

    med = float(np.median(arr))
    std = float(np.std(arr))
    cv = std / med if med > 0 else float("inf")
    period_ms = med * 1000.0
    jitter_ms = std * 1000.0

    if cv < 0.10:
        kind = "periodic"
        notes = f"clean schedule, jitter {jitter_ms:.1f}ms"
    elif cv < 0.5:
        kind = "periodic"
        notes = f"loose schedule, jitter {jitter_ms:.1f}ms (cv={cv:.2f})"
    elif frame_count < 20 and last_t - first_t < duration_s * 0.5:
        kind = "startup_only" if first_t < duration_s * 0.2 else "event_driven"
        notes = f"{frame_count} frames concentrated in {last_t-first_t:.1f}s"
    else:
        kind = "bursty"
        notes = f"high jitter (cv={cv:.2f})"

    return PeriodicityReport(kind, rate, period_ms, jitter_ms, cv, notes)
