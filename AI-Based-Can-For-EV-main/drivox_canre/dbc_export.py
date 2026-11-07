"""Export a HypothesisRegistry as a DBC file (cantools / opendbc compatible).

Confidence and notes are emitted as DBC CM_ comments so the
artefact is the source of truth, not the YAML alone.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cantools
from cantools.database import conversion
from cantools.database.can.database import Database
from cantools.database.can.message import Message
from cantools.database.can.signal import Signal
from cantools.database.can.node import Node

from .hypotheses import HypothesisRegistry, SignalHypothesis


def _signal_to_cantools(s: SignalHypothesis) -> Signal:
    return Signal(
        name=s.name,
        start=s.start_bit,
        length=s.length_bits,
        byte_order=s.byte_order,
        is_signed=s.is_signed,
        conversion=conversion.BaseConversion.factory(
            scale=s.factor, offset=s.offset,
        ),
        minimum=s.minimum,
        maximum=s.maximum,
        unit=s.unit or None,
        receivers=list(s.receivers) or None,
        comment=_signal_comment(s),
    )


def _signal_comment(s: SignalHypothesis) -> str:
    parts = [f"confidence={s.confidence}"]
    if s.notes:
        parts.append(s.notes)
    if s.confirming_test:
        parts.append(f"test: {s.confirming_test}")
    return " | ".join(parts)


def export(reg: HypothesisRegistry,
           out_path: str | Path,
           version: str = "DRIVOX-Xpres-T-EV v0.1",
           confidences: Iterable[str] = ("medium", "high", "confirmed")) -> Path:
    db = Database(version=version)
    senders: set[str] = set()
    for f in reg.frames.values():
        senders.add(f.sender)
        msg_signals = [_signal_to_cantools(s) for s in f.signals
                       if s.confidence in confidences]
        if not msg_signals:
            continue
        msg = Message(
            frame_id=f.can_id,
            name=f.name,
            length=f.length,
            signals=msg_signals,
            senders=[f.sender] if f.sender else None,
            cycle_time=int(f.cycle_time_ms) if f.cycle_time_ms else None,
            comment=f.notes or None,
        )
        db.messages.append(msg)
    for n in sorted(senders):
        if n:
            db.nodes.append(Node(n))
    db.refresh()
    out = Path(out_path)
    out.write_text(db.as_dbc_string())
    return out
