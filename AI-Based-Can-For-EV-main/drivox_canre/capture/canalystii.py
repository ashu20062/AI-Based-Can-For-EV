"""Waveshare CANalyst-II backend — dual-channel, reconnecting.

Talks to the raw ``canalystii`` device rather than going through python-can,
because we need both channels off a single device handle. The adapter exposes
one USB endpoint for two CAN buses; opening it twice (once per channel via
python-can) fights over the same handle.

Reconnect matters here: the adapter drops off the USB bus on cable jostle and
on autosuspend, which on a moving vehicle means losing the rest of the drive.
On disconnect we deliberately do *not* call ``dev.stop()`` — writing a command
to a device that has already vanished segfaults inside libusb's cleanup path.
We drop the handle and re-open instead.

Run ``drivox-canre setup-udev`` once to pin autosuspend off for this device.
"""
from __future__ import annotations

import time
from typing import Iterable, Iterator

from .base import Frame

VENDOR_ID = 0x04D8
PRODUCT_ID = 0x0053

RECONNECT_DELAY_S = 1.0
POLL_IDLE_S = 0.001


class CanalystBackend:
    name = "canalystii"

    def __init__(self, channel: int | None = None,
                 channels: Iterable[int] | str | None = None,
                 bitrate: int = 500000,
                 reconnect: bool = True):
        # `channel=` is the pre-fan-out spelling; keep it working.
        if channels is None:
            channels = (0,) if channel is None else (channel,)
        if isinstance(channels, str):
            channels = [int(c) for c in channels.split(",") if c.strip() != ""]
        self.channels = tuple(sorted({int(c) for c in channels}))
        if not self.channels or any(c not in (0, 1) for c in self.channels):
            raise ValueError(f"channels must be a subset of 0,1 — got {self.channels}")

        self.bitrate = int(bitrate)
        self.reconnect = reconnect

        self._dev = None
        self._t0 = time.monotonic()
        self._stop = False
        self.reconnect_count = 0
        self.dropped_spans: list[tuple[float, float]] = []

    # -- device lifecycle ------------------------------------------------

    def _open(self):
        import canalystii
        dev = canalystii.CanalystDevice(bitrate=self.bitrate)
        # Start only the channels we were asked for, so an unterminated
        # second bus doesn't spew error frames into the capture.
        for ch in self.channels:
            try:
                dev.start(ch)
            except Exception:
                # Some firmware revisions auto-start on init; a failure to
                # explicitly start is not fatal if receive() still works.
                pass
        return dev

    def _discard(self) -> None:
        """Drop a dead handle without touching the device.

        Calling stop()/shutdown() on a disconnected CANalyst-II triggers a
        bulk write to a vanished endpoint, which segfaults libusb rather than
        raising. Dropping the reference is the only safe teardown here.
        """
        self._dev = None

    def close(self) -> None:
        self._stop = True
        dev = self._dev
        self._dev = None
        if dev is None:
            return
        try:
            for ch in self.channels:
                dev.stop(ch)
        except Exception:
            pass

    # -- capture ---------------------------------------------------------

    def frames(self) -> Iterator[Frame]:
        import usb.core

        while not self._stop:
            if self._dev is None:
                try:
                    self._dev = self._open()
                except Exception:
                    if not self.reconnect:
                        raise
                    if self._wait(RECONNECT_DELAY_S):
                        return
                    continue

            down_since: float | None = None
            try:
                while not self._stop:
                    # close() runs on another thread and clears self._dev, so
                    # bind it once per pass. Reading the attribute inline races
                    # against that and raises AttributeError on None, which
                    # would escape as a bogus "capture thread crashed" on every
                    # clean shutdown.
                    dev = self._dev
                    if dev is None:
                        break
                    got_any = False
                    for ch in self.channels:
                        # close() stops channels one at a time; bail out
                        # between them rather than reading a stopped channel.
                        if self._stop:
                            break
                        for m in dev.receive(ch):
                            got_any = True
                            yield Frame(
                                t=time.monotonic() - self._t0,
                                can_id=m.can_id,
                                dlc=m.data_len,
                                data=bytes(m.data[:m.data_len]),
                                channel=ch,
                            )
                    if not got_any:
                        time.sleep(POLL_IDLE_S)
            except (usb.core.USBError, OSError, RuntimeError):
                # RuntimeError is what the canalystii library raises for
                # "channel is stopped" — during teardown that is an artefact
                # of close(), not a fault, so never report it as a crash.
                if self._stop:
                    return
                if not self.reconnect:
                    self._discard()
                    raise
                down_since = time.monotonic() - self._t0
                self._discard()
                self.reconnect_count += 1
            except GeneratorExit:
                # Consumer stopped iterating; tear down cleanly.
                self.close()
                raise

            if down_since is not None:
                if self._wait(RECONNECT_DELAY_S):
                    return
                self.dropped_spans.append(
                    (down_since, time.monotonic() - self._t0)
                )

    def _wait(self, seconds: float) -> bool:
        """Sleep in small slices. Returns True if we were asked to stop."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop:
                return True
            time.sleep(0.05)
        return self._stop
