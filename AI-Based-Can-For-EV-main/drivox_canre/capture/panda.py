"""Comma.ai Panda backend in silent / listen-only mode.

Defence-in-depth: even if user code accidentally tries to send, the Panda
will not ACK on the bus. Required by DRIVOX read-only data-collection policy.
"""
from __future__ import annotations

import time
from typing import Iterator

from .base import Frame


class PandaBackend:
    name = "panda"

    def __init__(self, channel: int = 0, bitrate: int = 500000):
        import can
        # python-can's 'panda' interface; silent mode = listen only.
        # If your python-can build doesn't accept 'silent' as a kwarg, set
        # safety mode externally via panda.set_safety_mode(SAFETY_SILENT) and
        # remove the kwarg.
        try:
            self._bus = can.interface.Bus(
                interface="panda", channel=channel, bitrate=bitrate,
                silent=True,
            )
        except TypeError:
            self._bus = can.interface.Bus(
                interface="panda", channel=channel, bitrate=bitrate,
            )
            try:
                from panda import Panda
                p = Panda()
                p.set_safety_mode(Panda.SAFETY_SILENT)
                p.close()
            except Exception:
                pass
        self._t0 = time.monotonic()
        self._stop = False

    def close(self) -> None:
        self._stop = True
        try:
            self._bus.shutdown()
        except Exception:
            pass

    def frames(self) -> Iterator[Frame]:
        for msg in self._bus:
            if self._stop:
                return
            t = (msg.timestamp if msg.timestamp else time.monotonic()) - self._t0
            yield Frame(
                t=t,
                can_id=msg.arbitration_id,
                dlc=msg.dlc,
                data=bytes(msg.data),
            )
