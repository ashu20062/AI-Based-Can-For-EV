"""CANalyst-II backend: reconnect contract and argument handling.

The adapter drops off the USB bus on cable jostle and on autosuspend. Losing
the remainder of a drive to that is the failure this backend exists to
prevent, so the contracts here are:

  1. reconnect=True  -> frames() keeps retrying, never raises
  2. close()         -> the retry loop exits promptly, no hung thread
  3. reconnect=False -> the error propagates, as asked

Runs with or without the adapter plugged in; case 3 asserts the appropriate
behaviour for each.
"""
import sys
import threading
import time

from _harness import Checker, adapter_present, have_module, skip

if not have_module("canalystii"):
    sys.exit(skip("canalystii package not installed"))

from drivox_canre.capture.canalystii import CanalystBackend

c = Checker()
present = adapter_present()
c.note(f"adapter {'present' if present else 'absent'}")

print("== reconnect=True keeps retrying and never raises ==")
b = CanalystBackend(channels="0,1", reconnect=True)
err: list = []


def pump():
    try:
        for _ in b.frames():
            pass
    except Exception as e:
        err.append(e)


t = threading.Thread(target=pump, daemon=True)
t.start()
t_start = time.monotonic()
time.sleep(3.0)
c.check("thread still alive after 3s", t.is_alive(), True)
c.check("no exception escaped frames()", err, [])

print("== close() stops it promptly ==")
b.close()
t.join(timeout=5.0)
c.check("thread exited after close()", t.is_alive(), False)
c.check("no exception escaped during teardown", err, [])
c.note(f"shutdown took {time.monotonic() - t_start:.1f}s")

print("== reconnect=False ==")
b2 = CanalystBackend(channels="0", reconnect=False)
raised = None
got_frame = False
try:
    for _ in b2.frames():
        got_frame = True
        break
except Exception as e:
    raised = e
finally:
    b2.close()

if present:
    # Either it opened and delivered, or another process holds the adapter and
    # the error propagated. Both are correct for reconnect=False.
    c.check("opened and delivered, or propagated",
            got_frame or raised is not None, True)
else:
    c.check("error propagated with no adapter", raised is not None, True)

print("== channel argument handling ==")
c.check("channels='0'", CanalystBackend(channels="0").channels, (0,))
c.check("channels='1'", CanalystBackend(channels="1").channels, (1,))
c.check("channels='0,1'", CanalystBackend(channels="0,1").channels, (0, 1))
c.check("duplicates collapse", CanalystBackend(channels="1,1,0").channels, (0, 1))
c.check("legacy channel=1 still works",
        CanalystBackend(channel=1).channels, (1,))

bad = False
try:
    CanalystBackend(channels="0,2")
    bad = True
except ValueError:
    pass
c.check("invalid channel rejected", bad, False)

sys.exit(c.finish())
