"""Shutdown races between close() and the capture thread.

This test found two real bugs, both of which surfaced as a bogus
"capture thread crashed" message on ordinary, clean session shutdowns:

  1. close() clears self._dev while the capture loop was reading
     `self._dev.receive(...)` inline -> AttributeError on None.
  2. close() stops channel 0 while the loop advances to channel 1 ->
     the canalystii library raises RuntimeError("Channel 1 is stopped").

Neither was caught by the (USBError, OSError) handler, so both escaped
frames(). They only appeared intermittently, which is exactly why this is a
randomised stress test rather than a single-shot check: closing at a fixed
moment misses the window most of the time.

Requires the adapter. Set DRIVOX_RACE_CYCLES to change the iteration count.
"""
import os
import random
import sys
import threading
import time

from _harness import Checker, adapter_present, have_module, skip

if not have_module("canalystii"):
    sys.exit(skip("canalystii package not installed"))
if not adapter_present():
    sys.exit(skip("CANalyst-II not connected"))

from drivox_canre.capture.canalystii import CanalystBackend

CYCLES = int(os.environ.get("DRIVOX_RACE_CYCLES", "25"))

c = Checker()
escaped: list[str] = []
frames_total = 0
hung = 0

print(f"== {CYCLES} randomised close-during-capture cycles ==")
for i in range(CYCLES):
    b = CanalystBackend(channels="0,1", bitrate=500000)
    n = [0]

    def pump():
        try:
            for _ in b.frames():
                n[0] += 1
        except Exception as e:
            escaped.append(repr(e))

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    # Close at an unpredictable point so the race window is actually hit.
    time.sleep(random.uniform(0.02, 0.4))
    b.close()
    t.join(timeout=5.0)
    if t.is_alive():
        hung += 1
    frames_total += n[0]

c.note(f"{frames_total} frames captured across {CYCLES} cycles")
for e in escaped[:5]:
    c.note(f"escaped: {e}")

c.check("no exception escaped frames()", len(escaped), 0)
c.check("no hung capture threads", hung, 0)
c.check("frames actually flowed", frames_total > 0, True)

sys.exit(c.finish())
