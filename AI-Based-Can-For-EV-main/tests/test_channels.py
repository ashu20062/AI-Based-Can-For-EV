"""Channel-aware state: the same arbitration ID on two buses must not merge.

This is the regression guard for the bug where `StateStore` keyed purely on
`can_id`. On the Xpres-T, 0x103 and 0x501 are live on *both* CAN buses with
different rates and unrelated payloads; merging them produced a single bogus
ID at roughly the sum of the two rates, with a payload that appeared to
alternate every frame — the exact fingerprint that fakes a "toggle" byte
classification and would put a wrong signal into a shipped DBC.

Also asserts single-bus captures are unchanged, since every prior session and
every earlier analysis run is single-bus.
"""
import csv
import io
import os
import sys
import tempfile

from _harness import Checker, REPO_ROOT
from rich.console import Console

from drivox_canre.capture.base import Frame
from drivox_canre.core.markers import Marker, MarkerLog
from drivox_canre.core.state import StateStore, format_id
from drivox_canre.core.windows import diff_around_marker
from drivox_canre.reports.analyze import build_state_from_csv, render_report

# A real 30s single-bus capture, if it is still around. Override with
# DRIVOX_REF_CSV; the test falls back to a synthetic equivalent.
REF_CSV = os.environ.get("DRIVOX_REF_CSV", "/home/joai/can-analysis/tigor_30s.csv")
REF_FRAMES, REF_IDS = 6657, 39

c = Checker()

print("== same ID on two buses stays separate ==")
st = StateStore()
for i in range(10):
    st.observe(Frame(t=i * 0.1, can_id=0x100, dlc=8,
                     data=bytes([i, 0, 0, 0, 0, 0, 0, 0]), channel=0))
for i in range(50):
    st.observe(Frame(t=i * 0.02, can_id=0x100, dlc=8,
                     data=bytes([0xFF, i, 0, 0, 0, 0, 0, 0]), channel=1))

c.check("distinct IdState objects", len(st.snapshot_ids()), 2)
c.check("channels seen", sorted(st.channels_seen), [0, 1])
c.check("is_multichannel", st.is_multichannel, True)

s0, s1 = st.get(0x100, 0), st.get(0x100, 1)
c.check("ch0 frame_count", s0.frame_count, 10)
c.check("ch1 frame_count", s1.frame_count, 50)
c.check("ch0 payload not polluted by ch1", s0.last_data[0], 9)
c.check("ch1 payload not polluted by ch0", s1.last_data[0], 0xFF)

r0 = s0.frame_count / (s0.last_seen_t - s0.first_seen_t)
r1 = s1.frame_count / (s1.last_seen_t - s1.first_seen_t)
c.note(f"ch0 rate={r0:.1f} Hz   ch1 rate={r1:.1f} Hz  (must differ)")
c.check("rates are independent", round(r0) != round(r1), True)

print("== ByteChange carries its channel ==")
c.check("change channels", sorted({x.channel for x in st.recent_changes(500)}),
        [0, 1])

print("== format_id ==")
c.check("single-bus label", format_id(0x249, 0, with_channel=False), "0x249")
c.check("multi-bus label", format_id(0x249, 1, with_channel=True), "ch1 0x249")

print("== marker windows carry channel ==")
# The synthetic data spans 0..0.98s, so windows must sit inside that.
deltas = diff_around_marker(st.snapshot_ids(), Marker(t=0.5, label="test"),
                            pre_s=0.5, post_s=0.5, guard_s=0.0)
c.note(f"{len(deltas)} deltas: "
       + ", ".join(f"ch{d.channel} 0x{d.can_id:03X} b{d.byte_idx}"
                   for d in deltas[:6]))
c.check("delta channels present", sorted({d.channel for d in deltas}), [0, 1])

print("== single-bus capture unchanged (regression) ==")
if os.path.exists(REF_CSV):
    st1 = build_state_from_csv(REF_CSV)
    c.check("frames_seen", st1.frames_seen, REF_FRAMES)
    c.check("unique ids", len(st1.snapshot_ids()), REF_IDS)
else:
    c.note(f"{REF_CSV} not found — using a synthetic single-bus capture")
    tmp = tempfile.mktemp(suffix=".csv")
    with open(tmp, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["timestamp_s", "can_id_hex", "dlc", *[f"b{i}" for i in range(8)],
                    "data_hex", "label"])
        for i in range(300):
            w.writerow([f"{i*0.01:.6f}", f"0x{0x100 + (i % 5):03X}", 8,
                        *([i % 256] * 8), bytes([i % 256] * 8).hex(), ""])
    st1 = build_state_from_csv(tmp)
    os.unlink(tmp)
    c.check("frames_seen", st1.frames_seen, 300)
    c.check("unique ids", len(st1.snapshot_ids()), 5)

c.check("is_multichannel false", st1.is_multichannel, False)
c.check("all on channel 0", sorted({s.channel for s in st1.snapshot_ids()}), [0])
c.check("legacy get(can_id) works", st1.get(next(iter(st1.snapshot_ids())).can_id)
        is not None, True)

print("== render_report in both modes ==")
buf = Console(file=io.StringIO(), width=200)
render_report(st1, None, console=buf)
c.check("single-bus report has no ch prefix", "ch0 0x" in buf.file.getvalue(), False)

buf2 = Console(file=io.StringIO(), width=200)
render_report(st, None, console=buf2)
out2 = buf2.file.getvalue()
c.check("multi-bus report shows ch prefix", "ch1 0x100" in out2, True)
c.check("multi-bus report shows per-channel summary", "ch0:" in out2, True)

print("== marker-diff render path ==")
ml = MarkerLog()
ml.add(0.5, "regen_on")
render_report(st, ml, console=Console(file=io.StringIO(), width=200))
c.check("marker diff rendered without NameError", True, True)

sys.exit(c.finish())
