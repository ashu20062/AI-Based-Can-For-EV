"""Synthetic Xpres-T-like CAN stream for offline development and demos.

Patterns are based on the master-prompt observations from real captures —
realistic enough that the analysis pipeline produces meaningful classifications.
"""
from __future__ import annotations

import math
import random
import time
from typing import Iterator

from .base import Frame


class MockBackend:
    name = "mock"

    def __init__(self, duration_s: float = 120.0, speed: float = 1.0, seed: int = 0):
        self.duration_s = duration_s
        self.speed = speed
        self._rng = random.Random(seed)
        self._stop = False

    def close(self) -> None:
        self._stop = True

    def frames(self) -> Iterator[Frame]:
        t = 0.0
        wall_start = time.monotonic()
        # ID schedule: (id, period_s, generator)
        schedule = [
            (0x247, 1 / 4.4, self._gen_0x247),
            (0x111, 1 / 14.4, self._gen_0x111),
            (0x249, 1 / 4.4, self._gen_0x249),
            (0x398, 1 / 1.7, self._gen_0x398),
            (0x38F, 1 / 1.25, self._gen_0x38f),
            (0x380, 1 / 0.7, self._gen_0x380),
            (0x38A, 1.0, self._gen_0x38a),
            (0x387, 1 / 0.9, self._gen_0x387),
            (0x386, 1 / 0.9, self._gen_0x386),
            (0x394, 1 / 0.7, self._gen_0x394),
            (0x396, 1 / 0.7, self._gen_0x396),
            (0x38E, 1.0, self._gen_0x38e),
        ]
        next_t = {cid: 0.0 + self._rng.uniform(0, p) for cid, p, _ in schedule}
        counters = {cid: 0 for cid, _, _ in schedule}

        while t < self.duration_s and not self._stop:
            soonest_cid, soonest_t = min(next_t.items(), key=lambda kv: kv[1])
            t = soonest_t
            if t >= self.duration_s:
                break
            for cid, period, gen in schedule:
                if cid == soonest_cid:
                    counters[cid] += 1
                    data = gen(t, counters[cid])
                    jitter = self._rng.uniform(-period * 0.05, period * 0.05)
                    next_t[cid] = t + period + jitter
                    if data is not None:
                        yield Frame(t=t, can_id=cid, dlc=len(data), data=data)
                    break
            if self.speed > 0:
                target_wall = wall_start + (t / self.speed)
                now_wall = time.monotonic()
                if target_wall > now_wall:
                    time.sleep(min(0.05, target_wall - now_wall))

    def _speed_profile(self, t: float) -> float:
        if t < 6.7:
            return 46 - (46 - 41) * (t / 6.7)
        if t < 13.9:
            return 41 + (60.6 - 41) * ((t - 6.7) / (13.9 - 6.7))
        if t < 25:
            return 36 + 19 * (0.5 + 0.5 * math.sin(t))
        if t < 40:
            return 35 + 1 * math.sin(t * 0.5)
        if t < 48.5:
            return 50.5 + 0.5 * math.sin(t)
        return max(0, 50 - (t - 48.5) * 1.2)

    def _gen_0x247(self, t: float, c: int) -> bytes:
        v_kmh = self._speed_profile(t)
        word = int(v_kmh * 100)
        b5, b6 = (word >> 8) & 0xFF, word & 0xFF
        b7 = 0x8D if c % 2 == 0 else 0x8E
        return bytes([0x10, 0x00, 0x00, 0x00, 0x00, b5, b6, b7])

    def _gen_0x111(self, t: float, c: int) -> bytes:
        if t < 8.47:
            torque = 0
        else:
            v = self._speed_profile(t)
            v_next = self._speed_profile(min(self.duration_s, t + 0.1))
            accel = (v_next - v) / 0.1
            torque = max(0, min(178, int(accel * 6 + 40)))
            if 28 < t < 30:
                torque = 178
        return bytes([0x00, torque, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    def _gen_0x249(self, t: float, c: int) -> bytes:
        # Strict alternation 0x91 / 0xD1 (counter, not regen — matches observed data)
        b5 = 0x91 if c % 2 == 0 else 0xD1
        b6 = (c % 4) * 0x40
        return bytes([0x00, 0x00, 0x00, 0x00, 0x00, b5, b6, 0x00])

    def _gen_0x398(self, t: float, c: int) -> bytes:
        wh = int((100.5 + (103.9 - 100.5) * (t / 121.3)) * 10)
        b0, b1 = (wh >> 8) & 0xFF, wh & 0xFF
        temp = 13 + int((24 - 13) * (t / 121.3))
        b7 = 0xE0 + ((c % 4) * 4)
        return bytes([b0, b1, 0x00, temp, 0x7B, 0x00, 0x01, b7])

    def _gen_0x38f(self, t: float, c: int) -> bytes:
        if c % 2 == 0:
            return bytes([0x7F, 0x40, 0xF1, 0x19, 0x19, 0x2C, 0x00, 0x00])
        return bytes([0x7F, 0x80, 0xF9, 0x19, 0x19, 0x2C, 0x00, 0x00])

    def _gen_0x380(self, t: float, c: int) -> bytes:
        v = self._speed_profile(t)
        state = 0x4A if v < 30 else (0x62 if v < 50 else 0x72)
        return bytes([0x00, state, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    def _gen_0x38a(self, t: float, c: int) -> bytes:
        b2 = 0x45 if c % 2 == 0 else 0x46
        b5 = 0x20 if c % 2 == 0 else 0x24
        return bytes([0x00, 0x00, b2, 0x00, 0x00, b5, 0x00, 0x00])

    def _gen_0x387(self, t: float, c: int) -> bytes:
        # Active 3.2 -> 114.7s
        if t < 3.2 or t > 114.7:
            return None
        return bytes([0x10, 0x20 + (c % 4), 0x30, 0x40, 0x50, 0x60, 0x00, 0x00])

    def _gen_0x386(self, t: float, c: int) -> bytes | None:
        # Active 3.2 -> 80.2s only; otherwise silent (gen returns None).
        if t < 3.2 or t > 80.2:
            return None
        return bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])

    def _gen_0x394(self, t: float, c: int) -> bytes | None:
        if t < 0.7 or t > 113:
            return None
        return bytes([0x10, c % 256, 0x20, 0x30, 0x00, 0x00, 0x00, 0x00])

    def _gen_0x396(self, t: float, c: int) -> bytes | None:
        if t < 5.7:
            return None
        return bytes([0x20, c % 256, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    def _gen_0x38e(self, t: float, c: int) -> bytes | None:
        if t < 0.7 or t > 119:
            return None
        return bytes([0x00, c % 4, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
