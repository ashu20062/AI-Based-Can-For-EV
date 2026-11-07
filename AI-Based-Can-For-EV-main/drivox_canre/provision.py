"""Machine provisioning: udev rule and systemd units.

Everything here is idempotent and writes through sudo, so it is safe to
re-run. Unit and rule text is generated with the paths detected at install
time rather than hard-coded, because this has to work both on a dev laptop
and on a vehicle-mounted box.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UDEV_RULE_PATH = Path("/etc/udev/rules.d/99-canalystii.rules")
SYSTEMD_DIR = Path("/etc/systemd/system")
VCAN_UNIT = "drivox-vcan.service"
RECORD_UNIT = "drivox-record.service"

UDEV_RULE = """# CANalyst-II (Chuangxin/Microchip 04d8:0053) — installed by drivox-canre
# MODE=0666: open the adapter without root or plugdev membership.
# power/control=on: USB autosuspend silently drops this adapter mid-capture.
# It surfaces as "USBError: [Errno 19] No such device" and loses the drive.
SUBSYSTEM=="usb", ATTR{idVendor}=="04d8", ATTR{idProduct}=="0053", MODE="0666", TAG+="uaccess"
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="04d8", ATTR{idProduct}=="0053", TEST=="power/control", ATTR{power/control}="on"
"""


def vcan_unit(ifaces: list[str]) -> str:
    # `ip link show || add` keeps this a no-op when the interface already
    # exists, so the unit is safe to restart.
    cmds = "\n".join(
        f"ExecStart=/bin/sh -c 'ip link show {i} >/dev/null 2>&1 || "
        f"{{ ip link add dev {i} type vcan && ip link set up {i}; }}'"
        for i in ifaces
    )
    stops = "\n".join(
        f"ExecStop=-/bin/sh -c 'ip link del {i} 2>/dev/null || true'"
        for i in ifaces
    )
    return f"""[Unit]
Description=DRIVOX virtual CAN interfaces ({', '.join(ifaces)})
Documentation=https://github.com/collin80/SavvyCAN
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c '/sbin/modprobe vcan || /usr/sbin/modprobe vcan || modprobe vcan'
{cmds}
{stops}

[Install]
WantedBy=multi-user.target
"""


def record_unit(*, python: str, workdir: str, user: str, root: str,
                channels: str, rotate_s: int, bitrate: int) -> str:
    return f"""[Unit]
Description=DRIVOX headless CAN recorder
Requires={VCAN_UNIT}
After={VCAN_UNIT}

[Service]
Type=simple
User={user}
WorkingDirectory={workdir}
Environment=PYTHONUNBUFFERED=1
ExecStart={python} -m drivox_canre.cli record canalystii \\
    --channels {channels} --bitrate {bitrate} \\
    --root {root} --rotate {rotate_s} --savvycan
Restart=always
RestartSec=5
# The adapter may not be plugged in yet; keep retrying rather than failing.
StartLimitIntervalSec=0

[Install]
WantedBy=multi-user.target
"""


def _sudo(cmd: list[str], dry_run: bool = False) -> int:
    print("  sudo " + " ".join(cmd))
    if dry_run:
        return 0
    return subprocess.run(["sudo"] + cmd).returncode


def _install_file(content: str, dest: Path, dry_run: bool = False) -> int:
    with tempfile.NamedTemporaryFile("w", delete=False) as fp:
        fp.write(content)
        tmp = fp.name
    rc = _sudo(["cp", tmp, str(dest)], dry_run)
    if rc == 0:
        rc = _sudo(["chmod", "0644", str(dest)], dry_run)
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return rc


def install_udev(dry_run: bool = False) -> int:
    print(f"Installing {UDEV_RULE_PATH}")
    rc = _install_file(UDEV_RULE, UDEV_RULE_PATH, dry_run)
    if rc:
        return rc
    for cmd in (["udevadm", "control", "--reload-rules"],
                ["udevadm", "trigger", "--subsystem-match=usb"]):
        rc = _sudo(cmd, dry_run)
        if rc:
            return rc
    print("  ok — replug the adapter for it to take effect")
    return 0


def install_vcan_service(ifaces: list[str], enable: bool = True,
                         dry_run: bool = False) -> int:
    dest = SYSTEMD_DIR / VCAN_UNIT
    print(f"Installing {dest}")
    rc = _install_file(vcan_unit(ifaces), dest, dry_run)
    if rc:
        return rc
    rc = _sudo(["systemctl", "daemon-reload"], dry_run)
    if rc:
        return rc
    if enable:
        rc = _sudo(["systemctl", "enable", "--now", VCAN_UNIT], dry_run)
        if rc:
            return rc
    print(f"  ok — {', '.join(ifaces)} will exist after every boot")
    return 0


def install_record_service(*, root: str, channels: str = "0,1",
                           rotate_s: int = 600, bitrate: int = 500000,
                           enable: bool = False, dry_run: bool = False) -> int:
    dest = SYSTEMD_DIR / RECORD_UNIT
    workdir = str(Path(__file__).resolve().parent.parent)
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    content = record_unit(python=sys.executable, workdir=workdir, user=user,
                          root=root, channels=channels, rotate_s=rotate_s,
                          bitrate=bitrate)
    print(f"Installing {dest}")
    rc = _install_file(content, dest, dry_run)
    if rc:
        return rc
    rc = _sudo(["systemctl", "daemon-reload"], dry_run)
    if rc:
        return rc
    if enable:
        rc = _sudo(["systemctl", "enable", "--now", RECORD_UNIT], dry_run)
        if rc:
            return rc
        print(f"  ok — recording starts at boot into {root}")
    else:
        print(f"  ok — installed but NOT enabled. Start it with:")
        print(f"       sudo systemctl enable --now {RECORD_UNIT}")
    return 0


def ensure_vcan(ifaces: list[str], quiet: bool = False) -> bool:
    """Create any missing vcan interfaces right now. Returns True if all exist."""
    missing = [i for i in ifaces if not Path(f"/sys/class/net/{i}").exists()]
    if not missing:
        return True
    if not quiet:
        print(f"Creating missing interface(s): {', '.join(missing)} (needs sudo)")
    if _sudo(["/bin/sh", "-c", "modprobe vcan"]) != 0:
        return False
    for i in missing:
        if _sudo(["ip", "link", "add", "dev", i, "type", "vcan"]) != 0:
            return False
        if _sudo(["ip", "link", "set", "up", i]) != 0:
            return False
    return all(Path(f"/sys/class/net/{i}").exists() for i in ifaces)
