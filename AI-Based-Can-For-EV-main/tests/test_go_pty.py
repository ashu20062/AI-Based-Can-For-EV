"""End-to-end `drivox-canre go` through a pseudo-terminal.

The live TUI needs a real tty — raw-mode keyboard reader plus a Rich
full-screen Live — so a pipe cannot exercise it. This allocates a pty, lets
the capture run, presses a marker hotkey, then presses 'q' to quit cleanly and
checks what landed on disk.

Heavy and side-effecting: it launches SavvyCAN and rewrites its ini (backed up
first). Opt in with DRIVOX_INTEGRATION=1.
"""
import os
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _harness import Checker, REPO_ROOT, adapter_present, iface_exists, skip

if os.environ.get("DRIVOX_INTEGRATION") != "1":
    sys.exit(skip("set DRIVOX_INTEGRATION=1 to run (launches SavvyCAN)"))
if not adapter_present():
    sys.exit(skip("CANalyst-II not connected"))
if not (iface_exists("vcan0") and iface_exists("vcan1")):
    sys.exit(skip("vcan0/vcan1 missing — run: drivox-canre setup-vcan"))

CAPTURE_S = float(os.environ.get("DRIVOX_GO_SECONDS", "14"))
workdir = Path(tempfile.mkdtemp(prefix="drivox_go_"))
session = workdir / "session"

c = Checker()
c.note(f"session -> {session}")

master, slave = pty.openpty()
os.set_blocking(master, False)
proc = subprocess.Popen(
    [sys.executable, "-m", "drivox_canre.cli", "go",
     "--channels", "0,1", "--session-dir", str(session)],
    cwd=str(REPO_ROOT), stdin=slave, stdout=slave, stderr=slave,
    env={**os.environ, "TERM": "xterm-256color", "COLUMNS": "160", "LINES": "50"},
    start_new_session=True,
)
os.close(slave)

chunks: list[str] = []
t0 = time.monotonic()
marked = quit_sent = False
while True:
    elapsed = time.monotonic() - t0
    r, _, _ = select.select([master], [], [], 0.2)
    if r:
        try:
            data = os.read(master, 65536)
            if data:
                chunks.append(data.decode("utf-8", "replace"))
        except OSError:
            break
    if not marked and elapsed > CAPTURE_S * 0.5:
        os.write(master, b"R")          # regen_on marker
        marked = True
    if not quit_sent and elapsed > CAPTURE_S:
        os.write(master, b"q")
        quit_sent = True
    if proc.poll() is not None:
        break
    if elapsed > CAPTURE_S + 30:
        proc.send_signal(signal.SIGINT)
        time.sleep(2)
        if proc.poll() is None:
            proc.kill()
        break

time.sleep(0.5)
try:
    while True:
        data = os.read(master, 65536)
        if not data:
            break
        chunks.append(data.decode("utf-8", "replace"))
except OSError:
    pass
os.close(master)
proc.wait(timeout=10)

out = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", "".join(chunks))

print("== startup phases ==")
for n in range(1, 5):
    c.check(f"phase [{n}/4] ran", f"[{n}/4]" in out, True)

print("== session on disk ==")
frames_csv = session / "can_frames.csv"
meta_txt = session / "session_meta.txt"
markers_csv = session / "markers.csv"
c.check("can_frames.csv written", frames_csv.exists(), True)
c.check("session_meta.txt written", meta_txt.exists(), True)
c.check("markers.csv written", markers_csv.exists(), True)
c.check("quit was clean", "Session saved to" in out, True)

if frames_csv.exists():
    rows = frames_csv.read_text().splitlines()
    per_ch: dict[str, int] = {}
    for line in rows[1:]:
        ch = line.rsplit(",", 1)[-1].strip()
        per_ch[ch] = per_ch.get(ch, 0) + 1
    c.note(f"{len(rows) - 1} frames; per channel: {per_ch}")
    c.check("frames captured", len(rows) - 1 > 0, True)
    c.check("both channels present", sorted(per_ch), ["0", "1"])

if meta_txt.exists():
    meta = meta_txt.read_text()
    c.note(next((l for l in meta.splitlines() if l.startswith("sink:")), "no sink line"))
    c.check("channels recorded in meta", "channels: 0,1" in meta, True)
    c.check("vcan sink recorded in meta", "sink: vcan" in meta, True)
    c.check("no reconnects during a short run", "reconnects: 0" in meta, True)
    c.check("no capture error recorded", "capture_error" in meta, False)

if markers_csv.exists():
    mk = markers_csv.read_text()
    c.check("marker hotkey captured", "regen_on" in mk, True)

print("== SavvyCAN ==")
from drivox_canre.savvycan import is_running
c.check("SavvyCAN was launched", is_running(), True)
rcv = Path("/proc/net/can/rcvlist_all")
if rcv.exists():
    text = rcv.read_text()
    # A connected SavvyCAN registers a raw receiver on each interface.
    for iface in ("vcan0", "vcan1"):
        line = [l for l in text.splitlines()
                if l.split()[:1] == [iface] and "raw" in l]
        c.check(f"{iface} has a raw receiver (SavvyCAN connected)",
                bool(line), True)

sys.exit(c.finish())
