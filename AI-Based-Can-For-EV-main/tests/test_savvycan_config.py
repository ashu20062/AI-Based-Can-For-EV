"""SavvyCAN config generation — Qt @Variant encoding and ini surgery.

SavvyCAN has no CLI, so auto-connect depends entirely on writing its QSettings
ini byte-correctly. Two encoding rules are load-bearing and easy to break:

  * escapeNextIfDigit — a hex digit following \\0 or \\xNN must itself be
    escaped, which is why "vcan0" encodes as \\0v\\0\\x63\\0\\x61\\0n.
  * needsQuotes — QSettings reads an unquoted comma as a list separator, so
    any value containing ';', ',' or '=' must be quoted. Whether that happens
    depends on the bitrate: 500000 is safe, 11308 (0x00002C2C) is not.

The expected strings below were captured from a config SavvyCAN itself wrote.
"""
import sys
import tempfile
from pathlib import Path

from _harness import Checker

from drivox_canre.savvycan import (SavvyCanConfig, encode_qvector_int,
                                   encode_qvector_string, selftest)

c = Checker()

print("== encoder matches real SavvyCAN output ==")
failures = selftest()
for f in failures:
    print("  " + f)
c.check("selftest", failures, [])

print("== quoting rule ==")
# 11308 == 0x00002C2C -> two commas -> must be quoted or QSettings splits it.
c.check("comma-containing value is quoted",
        encode_qvector_int([11308]).startswith('"'), True)
c.check("ordinary bitrate is unquoted",
        encode_qvector_int([500000]).startswith('"'), False)

print("== round-trip shape ==")
v = encode_qvector_string(["vcan0", "vcan1"])
c.check("two-element vector declares count 2", "\\0\\0\\0\\x2" in v, True)
c.check("wrapped in @Variant", v.startswith("@Variant("), True)

print("== ini surgery preserves everything else ==")
original = "\n".join([
    "[ConnWindow]", "WindowPos=@Point(539 129)", "",
    "[Main]", "AsciiColumn=190", "SaveRestoreConnections=false",
    "TimeStyle=1", "",
    "[connections]", "portNames=@Variant(oldvalue)", "types=@Variant(oldvalue)",
    "",
]) + "\n"

tmp = Path(tempfile.mktemp(suffix=".ini"))
tmp.write_text(original)
cfg = SavvyCanConfig(tmp)
cfg.set_socketcan_connections(["vcan0", "vcan1"], 500000)
after = tmp.read_text()
lines = {ln.split("=", 1)[0] for ln in after.splitlines() if "=" in ln}

c.check("unrelated key untouched", "WindowPos=@Point(539 129)" in after, True)
c.check("unrelated Main key untouched", "AsciiColumn=190" in after, True)
c.check("TimeStyle untouched", "TimeStyle=1" in after, True)
c.check("SaveRestoreConnections enabled",
        "SaveRestoreConnections=true" in after, True)
c.check("old value replaced", "oldvalue" in after, False)

# loadConnections() bails unless all seven arrays are the same length.
required = {"portNames", "driverNames", "types", "busSpeeds_0",
            "DataRates_0", "isCanFds_0", "serialSpeeds"}
c.check("all seven arrays present", sorted(required - lines), [])

counts = set()
for key in required:
    line = next(ln for ln in after.splitlines() if ln.startswith(key + "="))
    counts.add(line.count("\\0\\0\\0\\x2"))
c.check("arrays are non-empty", all(x > 0 for x in counts), True)

print("== backup is taken and preserved ==")
tmp2 = Path(tempfile.mktemp(suffix=".ini"))
tmp2.write_text(original)
cfg2 = SavvyCanConfig(tmp2)
bak = cfg2.backup()
cfg2.set_socketcan_connections(["vcan0"], 500000)
c.check("backup created", bak is not None and bak.exists(), True)
c.check("backup holds the original bytes", bak.read_text(), original)
# A second write must not clobber the pristine original.
cfg2.backup()
cfg2.set_socketcan_connections(["vcan0", "vcan1"], 500000)
c.check("backup still pristine after re-write", bak.read_text(), original)

print("== creates missing sections ==")
tmp3 = Path(tempfile.mktemp(suffix=".ini"))
tmp3.write_text("[Main]\nAsciiColumn=190\n")
SavvyCanConfig(tmp3).set_socketcan_connections(["vcan0"], 500000)
t3 = tmp3.read_text()
c.check("[connections] section created", "[connections]" in t3, True)
c.check("portNames written", "portNames=@Variant(" in t3, True)

for p in (tmp, tmp2, tmp3, bak):
    try:
        Path(p).unlink()
    except OSError:
        pass

sys.exit(c.finish())
