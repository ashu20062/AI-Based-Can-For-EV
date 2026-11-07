# Drivox-Canre

A CAN bus reverse-engineering tool for the Waveshare/Chuangxin **CANalyst-II**
USB adapter on Linux. It records both CAN channels to CSV, shows a live
terminal dashboard, lets a passenger tag events with hotkeys while the vehicle
is moving, and turns the result into a byte-level analysis and a DBC file.

It also republishes the live capture onto virtual SocketCAN interfaces so
[SavvyCAN](https://github.com/collin80/SavvyCAN) can watch the same session,
which is otherwise not possible with this adapter.

Built for reverse-engineering a 2026 Tata Xpres-T EV, but nothing in the
analysis is vehicle-specific.

## Why this exists

The CANalyst-II is a proprietary ControlCAN USB device (`04d8:0053`), not a
serial or slcan adapter. SavvyCAN has no driver for it, so you cannot point
SavvyCAN at it directly. The usual workaround is a separate bridge process,
but then two programs are fighting over one USB handle and you end up with two
unsynchronised recordings.

This tool takes the other approach: one process owns the adapter, and
everything else — the dashboard, the CSV, the SavvyCAN feed — is a consumer of
that single stream.

```
CANalyst-II ──> capture backend (one USB handle, auto-reconnect)
                        │
                        ├──> live TUI + event markers
                        ├──> can_frames.csv
                        └──> vcan0 / vcan1 ──> SavvyCAN
```

## What it does

**Capture**

- Both CAN channels from one device handle, at a configurable bitrate.
- Reconnects automatically when the adapter drops off the USB bus (it does
  this on cable movement and on USB autosuspend), and records the gap in the
  session metadata rather than ending the run.
- Frames are tagged with their source channel end to end. State is keyed on
  `(channel, can_id)`, so the same arbitration ID appearing on both buses is
  never conflated — on the test vehicle, `0x103` and `0x501` are live on both
  buses with different rates and unrelated payloads.

**Live dashboard** (terminal, works over SSH)

- Per-ID frame count, rate, periodicity class, and last payload with changed
  bytes highlighted.
- A feed of recent byte changes and a marker log.
- Single-key event markers — brake, regen, sport mode, headlights, turn
  signals, numbered drive phases — timestamped against the capture so a
  passenger can tag events without looking away from the road.

**Analysis** (offline, on a recorded CSV)

- Byte classifier: constant / toggle / counter / low-entropy / high-entropy.
- 16-bit word detection by adjacent-byte correlation, with endianness choice.
- Periodicity classification: periodic / bursty / event-driven / startup-only.
- Marker-window diffs: for each tagged event, which bytes changed in the
  seconds before versus after, ranked by how many values were new.

**Output**

- `can_frames.csv` — every frame, with channel. Column layout is compatible
  with readers that look columns up by name.
- `markers.csv`, `session_meta.txt` — events and run metadata including
  reconnect count and dropout spans.
- `analysis_report.txt`, `evidence.md` — the analysis and per-marker evidence.
- A DBC file exported from a YAML hypothesis registry, filtered by confidence
  level so low-confidence guesses stay out of a shipped file.

## Requirements

- Linux. Uses SocketCAN, udev and systemd.
- Python 3.10+.
- A CANalyst-II adapter for live capture. Recorded CSVs can be replayed and
  analysed on any machine.
- SavvyCAN is optional — only needed if you want its GUI on the live stream.

## Install

```bash
git clone https://github.com/Sherin-SEF-AI/Drivox-CAN-RE.git
cd Drivox-CAN-RE
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pip install canalystii pyusb        # hardware backend
```

One-time machine setup. This needs sudo: it installs a udev rule so the
adapter is usable without root and does not get suspended, plus a systemd unit
that creates the virtual CAN interfaces at boot.

```bash
drivox-canre install-service --channels 0,1
drivox-canre doctor --channels 0,1
```

`doctor` checks the adapter, autosuspend state, virtual interfaces and Python
dependencies, and tells you which command fixes anything missing.

## Usage

```bash
# Everything at once: interfaces up, SavvyCAN configured and launched,
# dual-channel capture recording. Press q to stop.
drivox-canre go

# Capture only, no SavvyCAN.
drivox-canre live canalystii --channels 0,1

# Headless, for unattended or fleet recording. Rotates into a new session
# directory every 10 minutes; stops cleanly on SIGTERM.
drivox-canre record --root ~/captures --channels 0,1 --rotate 600

# Offline analysis of a finished session.
drivox-canre pipeline ~/captures/session_20260812_184203 \
    --registry data/xprest_seed.yaml --dbc xprest.dbc

# Replay a recorded CSV through the live dashboard.
drivox-canre replay captures/session_x/can_frames.csv --speed 2.0

# Synthetic data, no hardware needed.
drivox-canre live mock --mock-duration 120
```

### Marker hotkeys

| key | event | | key | event |
|---|---|---|---|---|
| `S` / `D` | sport / drive mode | | `A` / `a` | AC on / off |
| `R` / `r` | regen on / off | | `H` / `h` | headlights on / off |
| `B` / `b` | brake hard / release | | `T` / `t` | turn left / right |
| `0`–`5` | drive phase markers | | space | unlabelled marker |
| `M` | custom | | `q` | quit and save |

## SavvyCAN integration

SavvyCAN has no command-line interface, so connections are configured by
writing its settings file at `~/.config/EVTV/SavvyCAN.ini`. Two conditions
must both hold for it to reconnect on startup:

1. `Main/SaveRestoreConnections` must be true.
2. `loadConnections()` reads seven parallel arrays — `portNames`,
   `driverNames`, `types`, `busSpeeds_0`, `DataRates_0`, `isCanFds_0`,
   `serialSpeeds` — and returns without doing anything unless they are all the
   same length. A settings file written by an older build often has only some
   of them, which is why saved connections can appear to be stored but never
   restore, with no error shown.

`drivox_canre/savvycan.py` writes those arrays in Qt's `@Variant` format with
QSettings ini escaping. `savvycan.selftest()` checks the encoder against
byte-for-byte output captured from SavvyCAN itself. The existing settings file
is backed up to `SavvyCAN.ini.drivox-bak` before the first write, and only
connection keys are modified — window geometry and other settings are left
untouched.

SavvyCAN needs a few seconds to start and open its sockets, so if you launch
it and the capture together, the first moments of the drive reach the CSV but
not the SavvyCAN window. The CSV is always the complete record.

## Layout

```
drivox_canre/
├── capture/          backends: canalystii, panda, replay, mock
├── sinks/            secondary consumers of the live stream (vcan)
├── core/             state, byte classifier, periodicity, words, markers, windows
├── tui/              live dashboard and raw keyboard input
├── reports/          offline analysis
├── session.py        on-disk session format
├── recorder.py       headless capture with rotation
├── pipeline.py       session -> analysis -> evidence -> DBC
├── savvycan.py       SavvyCAN settings generation
├── provision.py      udev rule and systemd units
├── hypotheses.py     YAML signal registry with confidence levels
└── dbc_export.py     DBC output via cantools
```

## Hypotheses and DBC export

Signal guesses live in a YAML registry. Each carries a confidence level
(`low` / `medium` / `high` / `confirmed`), free-text notes, and a
`confirming_test` describing what would actually settle it. DBC export filters
on confidence, defaulting to `medium` and above, so unresolved guesses do not
end up in a file other tools will treat as authoritative.

`pipeline` correlates marker evidence against the registry and writes what it
found to `evidence.md`. **It does not change confidence levels on its own.**
Promoting a signal is a claim about the vehicle, and correlation around a
marker is not proof. `--promote` exists for when you have read the evidence
and want the clear cases applied, and it only acts where a marker label
matches the signal's recorded `confirming_test`.

The included `data/xprest_seed.yaml` is a starting point for one specific
vehicle. Every entry in it is unconfirmed.

## Tests

```bash
python3 tests/run_all.py                          # everything available
DRIVOX_INTEGRATION=1 python3 tests/run_all.py     # plus the full end-to-end test
```

Standalone scripts, no test framework dependency. Tests that need the adapter
or virtual interfaces skip and say so rather than passing without checking
anything. See `tests/README.md`.

## Known limitations

- **Linux only.** SocketCAN, udev and systemd are all assumed.
- **Only the CANalyst-II backend is exercised in practice.** A Comma.ai Panda
  backend exists but has had far less use.
- **Reconnect gaps skew periodicity.** The interval statistics treat a USB
  dropout as one very long gap, so a steady 100 Hz ID in a run with dropouts
  can be classified `bursty` with a high coefficient of variation.
- **`--promote` does not weigh evidence strength.** It gates on the marker
  matching `confirming_test`, not on how much actually changed, so it can
  promote on a marker where nothing moved. Read `evidence.md` first.
- **No cross-bus correlation.** The two buses are analysed independently;
  relating a signal on one to a signal on the other is manual.
- **Byte-span matching for hypotheses is approximate.** DBC bit numbering
  differs between big- and little-endian conventions, so evidence is ranked
  for a human rather than matched exactly.
- **The analysis is heuristic.** The byte classifier, word detector and
  periodicity analyser are there to narrow the search, not to be trusted
  without a confirming test.
- **Creating virtual CAN interfaces requires root**, which is why the systemd
  unit exists.

## License

MIT. See `LICENSE`.
