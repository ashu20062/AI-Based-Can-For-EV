"""Minimal test harness.

The project has no pytest dependency and does not need one — these are
standalone scripts. Each prints `RESULT: PASS|FAIL|SKIP` as its last line and
exits 0 / 1 / 77 respectively, which is what `run_all.py` reads.

SKIP is a first-class outcome here: several of these tests need the CANalyst-II
plugged in, or vcan interfaces, or a display. Skipping loudly is correct;
silently passing when the hardware is absent is not.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_PASS, EXIT_FAIL, EXIT_SKIP = 0, 1, 77

VENDOR_ID, PRODUCT_ID = 0x04D8, 0x0053


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, got, want) -> bool:
        ok = got == want
        line = f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}"
        if not ok:
            line += f"   (want {want!r})"
            self.failures.append(label)
        print(line)
        return ok

    def note(self, msg: str) -> None:
        print(f"       {msg}")

    def finish(self) -> int:
        if self.failures:
            print(f"\nRESULT: FAIL ({len(self.failures)}): {self.failures}")
            return EXIT_FAIL
        print("\nRESULT: PASS")
        return EXIT_PASS


def skip(reason: str) -> int:
    print(f"\nRESULT: SKIP ({reason})")
    return EXIT_SKIP


def adapter_present() -> bool:
    try:
        import usb.core
        return usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID) is not None
    except Exception:
        return False


def iface_exists(name: str) -> bool:
    return Path(f"/sys/class/net/{name}").exists()


def have_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False
