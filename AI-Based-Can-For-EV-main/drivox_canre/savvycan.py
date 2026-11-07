"""Drive SavvyCAN without touching its GUI.

SavvyCAN has no command-line interface (there is no QCommandLineParser in its
main.cpp), so the only way to pre-wire connections is through its QSettings
ini at ~/.config/EVTV/SavvyCAN.ini.

Two things have to be true for SavvyCAN to auto-connect at startup:

1. ``Main/SaveRestoreConnections`` must be true, which gates the
   ``loadConnections()`` call in ConnectionWindow's constructor.
2. ``loadConnections()`` reads SEVEN parallel arrays and bails out silently
   unless every one of them is the same length:
       portNames, driverNames, types, busSpeeds_0, DataRates_0,
       isCanFds_0, serialSpeeds

A hand-made config that only sets portNames and types — which is the usual
result of connecting through the GUI on an older build — trips that length
check and restores nothing, with no error shown.

Values are stored in Qt's ``@Variant(...)`` serialisation, escaped with
QSettings' ini escaping. Both are reimplemented here; `selftest()` verifies
the encoder against known-good values captured from a real SavvyCAN config.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

DEFAULT_INI = Path.home() / ".config" / "EVTV" / "SavvyCAN.ini"

# CANCon::type in connections/canconconst.h
TYPE_GVRET_SERIAL = 0
TYPE_KVASER = 1
TYPE_SERIALBUS = 2
TYPE_REMOTE = 3

QVARIANT_USER_TYPE = 0x7F

# Hex digits trigger QSettings' escapeNextIfDigit rule after an escape.
_HEX_DIGITS = set("0123456789abcdefABCDEF")
_SIMPLE_ESCAPES = {
    0x07: "\\a", 0x08: "\\b", 0x0C: "\\f", 0x0A: "\\n",
    0x0D: "\\r", 0x09: "\\t", 0x0B: "\\v",
    0x22: '\\"', 0x5C: "\\\\",
}


def _ini_escape(data: bytes) -> str:
    """Reimplementation of Qt's iniEscapedString for a latin-1 byte string.

    Two subtleties, both load-bearing:

    * escapeNextIfDigit — after emitting \\0 or \\xNN, a following hex digit
      must itself be escaped, or Qt's parser reads it as more digits of the
      preceding hex escape. ("vcan0" encodes as \\0v\\0\\x63\\0\\x61\\0n
      precisely because of this.)
    * needsQuotes — QSettings treats an unquoted comma as a list separator, so
      any value containing ';', ',' or '=' must be wrapped in double quotes.
      Whether these bytes appear depends on the bitrate: 500000 encodes as
      00 07 A1 20 and is safe, but other values are not.
    """
    out: list[str] = []
    needs_quotes = False
    escape_next_if_digit = False
    for byte in data:
        ch = chr(byte)
        if ch in ";,=":
            needs_quotes = True

        if escape_next_if_digit and ch in _HEX_DIGITS:
            out.append(f"\\x{byte:x}")
            escape_next_if_digit = True
            continue
        escape_next_if_digit = False

        if byte == 0:
            out.append("\\0")
            escape_next_if_digit = True
        elif byte in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[byte])
        elif byte <= 0x1F or byte >= 0x7F:
            out.append(f"\\x{byte:x}")
            escape_next_if_digit = True
        else:
            out.append(ch)

    escaped = "".join(out)
    if needs_quotes or escaped.startswith(" ") or escaped.endswith(" "):
        return f'"{escaped}"'
    return escaped


def _encode_variant(body: bytes) -> str:
    """Wrap a serialised QVariant the way QSettings writes it to an ini line.

    Qt builds the literal string ``@Variant(<raw bytes>)`` first and escapes
    that whole string, so the escaping (and any quoting) spans the parens too.
    """
    return _ini_escape(b"@Variant(" + body + b")")


def _variant_header(type_name: str) -> bytes:
    """QVariant user-type header: type id, then the length-prefixed type name."""
    name = type_name.encode("latin-1") + b"\x00"
    return struct.pack(">I", QVARIANT_USER_TYPE) + struct.pack(">I", len(name)) + name


def encode_qvector_string(items: list[str]) -> str:
    """QVector<QString> as SavvyCAN stores it: UTF-16BE, byte-length prefixed."""
    body = _variant_header("QVector<QString>") + struct.pack(">I", len(items))
    for s in items:
        raw = s.encode("utf-16-be")
        body += struct.pack(">I", len(raw)) + raw
    return _encode_variant(body)


def encode_qvector_int(items: list[int]) -> str:
    body = _variant_header("QVector<int>") + struct.pack(">I", len(items))
    for i in items:
        body += struct.pack(">i", i)
    return _encode_variant(body)


def selftest() -> list[str]:
    """Check the encoder against values taken from a real SavvyCAN.ini.

    Returns a list of failure descriptions; empty means the encoding matches
    what SavvyCAN itself wrote.
    """
    failures = []
    cases = [
        (
            "QVector<int> [0, 2]",
            encode_qvector_int([0, 2]),
            "@Variant(\\0\\0\\0\\x7f\\0\\0\\0\\rQVector<int>\\0\\0\\0\\0\\x2\\0\\0\\0\\0\\0\\0\\0\\x2)",
        ),
        (
            "QVector<QString> ['127.0.0.1', 'vcan0']",
            encode_qvector_string(["127.0.0.1", "vcan0"]),
            "@Variant(\\0\\0\\0\\x7f\\0\\0\\0\\x11QVector<QString>\\0\\0\\0\\0\\x2\\0\\0\\0"
            "\\x12\\0\\x31\\0\\x32\\0\\x37\\0.\\0\\x30\\0.\\0\\x30\\0.\\0\\x31\\0\\0\\0"
            "\\n\\0v\\0\\x63\\0\\x61\\0n\\0\\x30)",
        ),
    ]
    for label, got, want in cases:
        if got != want:
            failures.append(f"{label}\n     got: {got}\n    want: {want}")
    return failures


class SavvyCanConfig:
    """Targeted editor for SavvyCAN's ini.

    Rewrites only the keys we care about and leaves every other line byte
    identical, so window positions, column widths and DBC paths survive.
    """

    def __init__(self, path: Path | str = DEFAULT_INI):
        self.path = Path(path)

    def backup(self) -> Path | None:
        if not self.path.exists():
            return None
        bak = self.path.with_suffix(".ini.drivox-bak")
        if not bak.exists():           # keep the pristine original
            shutil.copy2(self.path, bak)
        return bak

    def set_socketcan_connections(self, ifaces: list[str],
                                  bus_speed: int = 500000) -> None:
        """Replace the saved connection list with one SocketCAN entry per iface."""
        n = len(ifaces)
        updates = {
            "connections": {
                "portNames": encode_qvector_string(ifaces),
                "driverNames": encode_qvector_string(["socketcan"] * n),
                "types": encode_qvector_int([TYPE_SERIALBUS] * n),
                "busSpeeds_0": encode_qvector_int([bus_speed] * n),
                "DataRates_0": encode_qvector_int([0] * n),
                "isCanFds_0": encode_qvector_int([0] * n),
                "serialSpeeds": encode_qvector_int([0] * n),
            },
            "Main": {
                "SaveRestoreConnections": "true",
            },
        }
        self._apply(updates)

    # -- ini surgery -----------------------------------------------------

    def _apply(self, updates: dict[str, dict[str, str]]) -> None:
        lines = (self.path.read_text().splitlines()
                 if self.path.exists() else [])

        # Which section does each line belong to?
        section_of: list[str] = []
        current = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1]
            section_of.append(current)

        remaining = {s: dict(kv) for s, kv in updates.items()}
        out: list[str] = []
        for idx, line in enumerate(lines):
            sec = section_of[idx]
            key = line.split("=", 1)[0].strip() if "=" in line else None
            if sec in remaining and key in remaining[sec]:
                out.append(f"{key}={remaining[sec].pop(key)}")
            else:
                out.append(line)

        # Append keys that had no existing line, creating sections as needed.
        for sec, kv in remaining.items():
            if not kv:
                continue
            if any(s == sec for s in section_of):
                insert_at = max(i for i, s in enumerate(section_of) if s == sec) + 1
                block = [f"{k}={v}" for k, v in kv.items()]
                out[insert_at:insert_at] = block
                # Keep section_of aligned for any later insertions.
                section_of[insert_at:insert_at] = [sec] * len(block)
            else:
                if out and out[-1].strip():
                    out.append("")
                out.append(f"[{sec}]")
                section_of.append(sec)
                for k, v in kv.items():
                    out.append(f"{k}={v}")
                    section_of.append(sec)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(out) + "\n")


def find_savvycan() -> Path | None:
    """Locate a SavvyCAN binary: PATH first, then the usual download spots."""
    on_path = shutil.which("SavvyCAN") or shutil.which("savvycan")
    if on_path:
        return Path(on_path)
    candidates = [
        Path.home() / "Downloads" / "SavvyCAN-x86_64.AppImage",
        Path.home() / "SavvyCAN.AppImage",
        Path.home() / "Downloads" / "SavvyCAN.AppImage",
        Path("/opt/SavvyCAN/SavvyCAN"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def is_running() -> bool:
    """True if a SavvyCAN process is actually running.

    Deliberately matches /proc/<pid>/comm — the executable name — rather than
    the full command line. `pgrep -f SavvyCAN` also matches any shell or
    script whose arguments merely mention SavvyCAN (including this tool's own
    wrapper when it touches SavvyCAN.ini), and a false positive here makes
    `go` skip both the config write and the launch.
    """
    me = os.getpid()
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == me:
                continue
            try:
                comm = (entry / "comm").read_text().strip()
            except OSError:
                continue        # process exited, or not ours to read
            if comm.lower().startswith("savvycan"):
                return True
    except OSError:
        pass
    return False


def launch(binary: Path | None = None) -> subprocess.Popen | None:
    """Start SavvyCAN detached, so it outlives the capture process."""
    exe = binary or find_savvycan()
    if exe is None:
        return None
    if not os.access(exe, os.X_OK):
        try:
            exe.chmod(exe.stat().st_mode | 0o111)
        except Exception:
            pass
    return subprocess.Popen(
        [str(exe)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
