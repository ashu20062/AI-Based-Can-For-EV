"""Signal hypothesis registry — YAML-backed, confidence-tracked."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

import yaml


CONFIDENCES = ("low", "medium", "high", "confirmed")


@dataclass
class SignalHypothesis:
    can_id: int
    name: str
    start_bit: int
    length_bits: int
    byte_order: str = "big_endian"   # 'big_endian' or 'little_endian' (DBC convention)
    is_signed: bool = False
    factor: float = 1.0
    offset: float = 0.0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: str = ""
    receivers: list[str] = field(default_factory=lambda: ["DRIVOX"])
    sender: str = "Unknown"
    confidence: str = "medium"
    notes: str = ""
    confirming_test: str = ""

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCES:
            raise ValueError(
                f"confidence must be one of {CONFIDENCES}, got {self.confidence!r}"
            )
        if self.byte_order not in ("big_endian", "little_endian"):
            raise ValueError("byte_order must be 'big_endian' or 'little_endian'")


@dataclass
class FrameHypothesis:
    can_id: int
    name: str
    length: int = 8
    cycle_time_ms: Optional[float] = None
    sender: str = "Unknown"
    signals: list[SignalHypothesis] = field(default_factory=list)
    notes: str = ""


class HypothesisRegistry:
    def __init__(self) -> None:
        self.frames: dict[int, FrameHypothesis] = {}

    @classmethod
    def load(cls, path: str | Path) -> "HypothesisRegistry":
        reg = cls()
        p = Path(path)
        if not p.exists():
            return reg
        raw = yaml.safe_load(p.read_text()) or {}
        for f_entry in raw.get("frames", []):
            sigs_raw = f_entry.pop("signals", [])
            f_entry["can_id"] = _parse_id(f_entry["can_id"])
            frame = FrameHypothesis(**f_entry)
            for s_entry in sigs_raw:
                s_entry["can_id"] = frame.can_id
                frame.signals.append(SignalHypothesis(**s_entry))
            reg.frames[frame.can_id] = frame
        return reg

    def save(self, path: str | Path) -> None:
        out = {"frames": []}
        for fid in sorted(self.frames):
            f = self.frames[fid]
            d = {
                "can_id": f"0x{f.can_id:03X}",
                "name": f.name,
                "length": f.length,
                "cycle_time_ms": f.cycle_time_ms,
                "sender": f.sender,
                "notes": f.notes,
                "signals": [],
            }
            for s in f.signals:
                sd = asdict(s)
                sd.pop("can_id", None)
                d["signals"].append(sd)
            out["frames"].append(d)
        Path(path).write_text(yaml.safe_dump(out, sort_keys=False))

    def add_frame(self, f: FrameHypothesis) -> None:
        self.frames[f.can_id] = f

    def add_signal(self, sig: SignalHypothesis) -> None:
        if sig.can_id not in self.frames:
            self.frames[sig.can_id] = FrameHypothesis(
                can_id=sig.can_id, name=f"FRAME_{sig.can_id:03X}"
            )
        self.frames[sig.can_id].signals.append(sig)

    def by_confidence(self, levels: Iterable[str]) -> list[SignalHypothesis]:
        wanted = set(levels)
        return [s for f in self.frames.values() for s in f.signals
                if s.confidence in wanted]


def _parse_id(v: int | str) -> int:
    if isinstance(v, int):
        return v
    return int(v, 16) if v.lower().startswith("0x") else int(v)
