#!/usr/bin/env python3
"""Run every test_*.py in this directory and summarise.

    python3 tests/run_all.py                # everything available
    python3 tests/run_all.py channels sink  # only matching names
    DRIVOX_INTEGRATION=1 python3 tests/run_all.py

Each test prints `RESULT: PASS|FAIL|SKIP` and exits 0 / 1 / 77. Tests needing
hardware skip loudly rather than passing vacuously, so a run with nothing
plugged in still tells you what it did not check.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXIT_SKIP = 77


def main(argv: list[str]) -> int:
    patterns = argv[1:]
    tests = sorted(p for p in HERE.glob("test_*.py"))
    if patterns:
        tests = [p for p in tests if any(pat in p.stem for pat in patterns)]
    if not tests:
        print("no tests matched")
        return 1

    results: list[tuple[str, str, float, str]] = []
    for path in tests:
        print(f"\n{'=' * 70}\n>>> {path.name}\n{'=' * 70}")
        t0 = time.monotonic()
        proc = subprocess.run([sys.executable, str(path)],
                              cwd=str(HERE), capture_output=True, text=True)
        dt = time.monotonic() - t0
        # libusb chatters on stderr; it is noise, not a failure signal.
        out = "\n".join(l for l in proc.stdout.splitlines()
                        if "libusb:" not in l)
        print(out)
        if proc.stderr.strip():
            err = "\n".join(l for l in proc.stderr.splitlines()
                            if "libusb:" not in l)
            if err.strip():
                print("--- stderr ---")
                print(err[:2000])

        if proc.returncode == EXIT_SKIP:
            status = "SKIP"
        elif proc.returncode == 0:
            status = "PASS"
        else:
            status = "FAIL"
        reason = ""
        for line in reversed(out.splitlines()):
            if line.startswith("RESULT:"):
                reason = line[len("RESULT:"):].strip()
                break
        results.append((path.name, status, dt, reason))

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    width = max(len(n) for n, _, _, _ in results)
    for name, status, dt, reason in results:
        extra = ""
        if status == "SKIP":
            extra = f"  {reason[4:].strip() if reason.startswith('SKIP') else reason}"
        elif status == "FAIL":
            extra = f"  {reason}"
        print(f"  {status:<5} {name:<{width}}  {dt:5.1f}s{extra}")

    n_fail = sum(1 for _, s, _, _ in results if s == "FAIL")
    n_skip = sum(1 for _, s, _, _ in results if s == "SKIP")
    n_pass = sum(1 for _, s, _, _ in results if s == "PASS")
    print(f"\n{n_pass} passed, {n_fail} failed, {n_skip} skipped")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
