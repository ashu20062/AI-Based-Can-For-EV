# tests

Standalone scripts, no pytest dependency. Each prints `RESULT: PASS|FAIL|SKIP`
as its last line and exits 0 / 1 / 77.

```bash
python3 tests/run_all.py                 # everything available
python3 tests/run_all.py channels sink    # only matching names
DRIVOX_INTEGRATION=1 python3 tests/run_all.py
```

Tests that need hardware **skip loudly** rather than passing vacuously, so a
run with nothing plugged in still tells you what went unchecked.

| test | needs | what it guards |
|---|---|---|
| `test_channels.py` | nothing | `StateStore` keyed on `(channel, can_id)`; single-bus output unchanged |
| `test_savvycan_config.py` | nothing | Qt `@Variant` encoding, ini surgery, backups |
| `test_fanout.py` | `vcan0` | frames pushed to a sink arrive on the interface |
| `test_reconnect.py` | `canalystii` pkg | retry / clean-stop / propagate contracts |
| `test_shutdown_race.py` | adapter | races between `close()` and the capture thread |
| `test_go_pty.py` | adapter, vcan0+1, `DRIVOX_INTEGRATION=1` | full `go` through a pty |

Environment knobs: `DRIVOX_REF_CSV` (single-bus reference capture, falls back
to synthetic), `DRIVOX_RACE_CYCLES` (default 25), `DRIVOX_GO_SECONDS`
(default 14), `DRIVOX_INTEGRATION=1` to opt into the heavy test.

## Why these exist

Two of them were written after the bugs they now catch, and both bugs were
invisible in ordinary use:

**`test_shutdown_race.py`** — `close()` runs on a different thread from the
capture loop. Two races escaped `frames()` as exceptions on perfectly normal
shutdowns: an `AttributeError` when `self._dev` was cleared mid-read, and a
`RuntimeError("Channel 1 is stopped")` when channel 0 was stopped while the
loop advanced to channel 1. Both showed up as "capture thread crashed" after a
clean `q`. They appeared intermittently, which is why the test closes at a
*random* point across many cycles — closing at a fixed moment misses the
window almost every time.

**`test_channels.py`** — on the Xpres-T, `0x103` and `0x501` are live on both
CAN buses with different rates and unrelated payloads. Keyed on `can_id`
alone they merged into one ID at roughly the summed rate whose payload
appeared to alternate every frame: the exact fingerprint that fakes a "toggle"
byte classification and would put a wrong signal into a shipped DBC.

`test_savvycan_config.py` guards encoding rules that are easy to break and
fail silently — SavvyCAN simply does not restore connections and says nothing.
