"""Shared on-disk session format.

One definition of the frame CSV schema, used by both the interactive TUI and
the headless recorder, so a session directory means the same thing whichever
produced it and `analyze` never has to care.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import TextIO

from .capture.base import Frame

# `channel` is last so readers that look columns up by name — including the
# original can-analysis collector.py tooling — keep working unchanged.
FRAME_CSV_HEADER = [
    "timestamp_s", "can_id_hex", "dlc",
    "b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7",
    "data_hex", "label", "channel",
]


def open_frame_csv(path: Path) -> tuple[TextIO, "csv._writer"]:
    fp = path.open("w", newline="")
    w = csv.writer(fp)
    w.writerow(FRAME_CSV_HEADER)
    return fp, w


def frame_row(f: Frame) -> list:
    padded = list(f.data) + [""] * (8 - len(f.data))
    return [f"{f.t:.6f}", f"0x{f.can_id:03X}", f.dlc, *padded,
            f.data.hex(), "", f.channel]


def write_meta(path: Path, *, backend_spec: str, start_wall: float,
               duration_s: float, frames: int, unique_ids: int,
               markers: int = 0, backend=None, sinks=None,
               extra: dict | None = None) -> None:
    with path.open("w") as f:
        f.write(f"backend: {backend_spec}\n")
        f.write(f"start_wall_time: {start_wall}\n")
        f.write(f"start_iso: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(start_wall))}\n")
        f.write(f"duration_s: {duration_s:.2f}\n")
        f.write(f"total_frames: {frames}\n")
        f.write(f"unique_ids: {unique_ids}\n")
        f.write(f"marker_count: {markers}\n")
        chans = getattr(backend, "channels", None)
        if chans is not None:
            f.write(f"channels: {','.join(str(c) for c in chans)}\n")
        f.write(f"reconnects: {getattr(backend, 'reconnect_count', 0)}\n")
        for span in getattr(backend, "dropped_spans", []) or []:
            f.write(f"gap_s: {span[0]:.2f} -> {span[1]:.2f}\n")
        for sink in (sinks or []):
            try:
                f.write(f"sink: {sink.name} {sink.stats()}\n")
            except Exception:
                pass
        for k, v in (extra or {}).items():
            f.write(f"{k}: {v}\n")
