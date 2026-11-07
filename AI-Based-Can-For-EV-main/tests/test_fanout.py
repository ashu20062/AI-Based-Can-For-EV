"""Fan-out: frames pushed to a sink actually arrive on the vcan interface.

Uses the mock backend so this runs without the adapter — it is the sink and
the socket plumbing under test, not the hardware. Needs vcan0 to exist.
"""
import sys
import threading
import time

from _harness import Checker, iface_exists, skip

if not iface_exists("vcan0"):
    sys.exit(skip("vcan0 does not exist — run: drivox-canre setup-vcan"))

import can

from drivox_canre.capture import build_backend
from drivox_canre.sinks.vcan import VCanSink

c = Checker()
received: list = []
stop = threading.Event()


def reader():
    bus = can.interface.Bus(channel="vcan0", interface="socketcan")
    while not stop.is_set():
        m = bus.recv(timeout=0.2)
        if m:
            received.append(m)
    bus.shutdown()


t = threading.Thread(target=reader, daemon=True)
t.start()
time.sleep(0.3)

backend = build_backend("mock", duration_s=6.0, speed=4.0)
sink = VCanSink({0: "vcan0"})

sent = 0
for f in backend.frames():
    sink.write(f)
    sent += 1
    if sent >= 1500:
        break

time.sleep(0.5)
stop.set()
t.join(timeout=2)

print("== delivery ==")
c.note(f"pushed {sent}, sink counted {sink.sent}, read back {len(received)}")
c.check("sink counted everything pushed", sink.sent, sent)
c.check("no sink errors", sink.errors, 0)
c.check("all frames arrived on vcan0", len(received), sent)
c.check("distinct IDs round-tripped",
        len({m.arbitration_id for m in received}) > 1, True)

print("== stats survive close() ==")
# close() drops the bus handles, but session_meta.txt calls stats() afterwards.
sink.close()
c.check("stats still names the interface", "vcan0" in sink.stats(), True)
c.check("stats still reports the count", f"tx={sent}" in sink.stats(), True)

print("== unmapped channels are counted, not crashed on ==")
sink2 = VCanSink({0: "vcan0"})
from drivox_canre.capture.base import Frame
sink2.write(Frame(t=0.0, can_id=0x123, dlc=1, data=b"\x01", channel=7))
c.check("frame on unmapped channel not sent", sink2.sent, 0)
c.check("unmapped channel recorded", "unmapped_ch=[7]" in sink2.stats(), True)
sink2.close()

sys.exit(c.finish())
