"""Replay a previously captured CSV. Format-compatible with the existing
can-analysis collector.py / capture.py outputs.

Auto-detects column layout:
  - Long schema (collector.py): timestamp_s, can_id_hex, dlc, b0..b7, data_hex, label
  - Short schema (capture.py):  timestamp, arbitration_id, dlc, data_hex, data_dec
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Iterator

from .base import Frame


class ReplayBackend:
    name = "replay"

    def __init__(self, path: str | Path, speed: float = 1.0, loop: bool = False):
        self.path = Path(path)
        self.speed = speed
        self.loop = loop
        self._stop = False

    def close(self) -> None:
        self._stop = True

    def _row_to_frame(self, row: dict, t0_ref: float | None) -> tuple[Frame, float]:
        # Long schema
        if "timestamp_s" in row:
            t = float(row["timestamp_s"])
            cid = int(row["can_id_hex"], 16)
        else:
            t = float(row["timestamp"])
            cid_field = row.get("arbitration_id") or row.get("can_id_hex") or row["can_id"]
            cid = int(cid_field, 16) if cid_field.startswith("0x") else int(cid_field)
        dlc = int(row.get("dlc", 8))
        data_hex = row.get("data_hex") or ""
        if not data_hex:
            data = bytes(int(row[f"b{i}"]) for i in range(8)
                        if row.get(f"b{i}", "") != "")
        else:
            data = bytes.fromhex(data_hex)
        if t0_ref is None:
            t0_ref = t
        channel = int(row.get("channel") or 0)
        return (Frame(t=t - t0_ref, can_id=cid, dlc=dlc, data=data,
                      channel=channel),
                t0_ref)

    def frames(self) -> Iterator[Frame]:
        while True:
            with self.path.open() as f:
                reader = csv.DictReader(f)
                wall0 = time.monotonic()
                t0_ref: float | None = None
                first_rel: float | None = None
                for row in reader:
                    if self._stop:
                        return
                    frame, t0_ref = self._row_to_frame(row, t0_ref)
                    if first_rel is None:
                        first_rel = frame.t
                    rel = frame.t - first_rel
                    if self.speed > 0:
                        target = wall0 + rel / self.speed
                        slack = target - time.monotonic()
                        if slack > 0:
                            time.sleep(min(slack, 0.05))
                    yield Frame(t=rel, can_id=frame.can_id, dlc=frame.dlc,
                                data=frame.data, channel=frame.channel)
            if not self.loop:
                return
