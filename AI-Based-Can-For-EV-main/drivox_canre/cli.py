"""drivox-canre CLI entry point."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console

from . import __version__


VCAN_FOR_CHANNEL = {0: "vcan0", 1: "vcan1"}


def _parse_channels(spec: str) -> list[int]:
    return [int(c) for c in spec.split(",") if c.strip() != ""]


def _cmd_live(args: argparse.Namespace) -> int:
    from .tui.live import LiveSession
    session = Path(args.session_dir or
                   f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    backend_kwargs = {}
    if args.backend == "mock":
        backend_kwargs["duration_s"] = args.mock_duration
        backend_kwargs["speed"] = args.mock_speed
    elif args.backend.startswith("replay:"):
        backend_kwargs["speed"] = args.replay_speed
    elif args.backend == "canalystii":
        backend_kwargs["channels"] = args.channels
        backend_kwargs["bitrate"] = args.bitrate
        backend_kwargs["reconnect"] = not args.no_reconnect

    sinks = []
    if args.savvycan:
        from .sinks.vcan import VCanSink
        chans = _parse_channels(args.channels)
        try:
            sink = VCanSink({c: VCAN_FOR_CHANNEL[c] for c in chans})
        except (RuntimeError, KeyError) as e:
            print(f"error: could not start the SavvyCAN feed:\n{e}",
                  file=sys.stderr)
            return 1
        sinks.append(sink)
        ifaces = ", ".join(VCAN_FOR_CHANNEL[c] for c in chans)
        print(f"SavvyCAN feed live on {ifaces}.")
        print("  In SavvyCAN: Connection -> Add New Device Connection")
        print(f"              -> QT SerialBus Devices -> socketcan / "
              f"{VCAN_FOR_CHANNEL[chans[0]]}")
        print()

    LiveSession(args.backend, session, backend_kwargs, sinks=sinks).run()
    return 0


def _cmd_setup_vcan(args: argparse.Namespace) -> int:
    """Create the virtual CAN interfaces SavvyCAN attaches to."""
    import subprocess

    ifaces = [VCAN_FOR_CHANNEL[c] for c in _parse_channels(args.channels)]
    cmds = [["modprobe", "vcan"]]
    for iface in ifaces:
        if Path(f"/sys/class/net/{iface}").exists():
            print(f"{iface} already exists — leaving it alone.")
            continue
        cmds.append(["ip", "link", "add", "dev", iface, "type", "vcan"])
        cmds.append(["ip", "link", "set", "up", iface])

    print("These need root. Running via sudo:\n")
    for c in cmds:
        print("  sudo " + " ".join(c))
    print()
    for c in cmds:
        r = subprocess.run(["sudo"] + c)
        if r.returncode != 0:
            print(f"\nfailed: sudo {' '.join(c)}", file=sys.stderr)
            return r.returncode
    print("\nvcan interfaces ready.")
    return 0


def _cmd_setup_udev(args: argparse.Namespace) -> int:
    """Install the udev rule that stops the adapter vanishing mid-capture."""
    from .provision import install_udev
    return install_udev(dry_run=getattr(args, "dry_run", False))


def _cmd_install_service(args: argparse.Namespace) -> int:
    """Make the whole setup survive reboots and replugs."""
    from .provision import (install_record_service, install_udev,
                            install_vcan_service)

    dry = args.dry_run
    ifaces = [VCAN_FOR_CHANNEL[c] for c in _parse_channels(args.channels)]

    rc = install_udev(dry_run=dry)
    if rc:
        return rc
    rc = install_vcan_service(ifaces, enable=True, dry_run=dry)
    if rc:
        return rc
    if args.recorder:
        rc = install_record_service(
            root=str(Path(args.root).expanduser().resolve()),
            channels=args.channels, rotate_s=args.rotate,
            bitrate=args.bitrate, enable=args.enable_recorder, dry_run=dry)
        if rc:
            return rc
    print("\nProvisioning complete.")
    return 0


def _cmd_go(args: argparse.Namespace) -> int:
    """One command: interfaces up, SavvyCAN wired and running, capture live."""
    from .provision import ensure_vcan
    from .savvycan import SavvyCanConfig, find_savvycan, is_running, launch
    from .tui.live import LiveSession

    chans = _parse_channels(args.channels)
    ifaces = [VCAN_FOR_CHANNEL[c] for c in chans]

    print("[1/4] virtual CAN interfaces")
    if not ensure_vcan(ifaces):
        print("error: could not create vcan interfaces", file=sys.stderr)
        return 1
    print(f"      {', '.join(ifaces)} ready")

    print("[2/4] SavvyCAN configuration")
    if args.no_savvycan:
        print("      skipped (--no-savvycan)")
    elif is_running():
        # SavvyCAN rewrites its ini on exit, so editing it underneath a
        # running instance would be silently reverted.
        print("      SavvyCAN is already running — leaving its config alone.")
        print(f"      Connect manually to: {', '.join(ifaces)}")
    else:
        cfg = SavvyCanConfig()
        bak = cfg.backup()
        cfg.set_socketcan_connections(ifaces, args.bitrate)
        print(f"      wired {', '.join(ifaces)} as SocketCAN connections"
              + (f" (backup: {bak.name})" if bak else ""))

    print("[3/4] SavvyCAN")
    if args.no_savvycan or args.no_launch:
        print("      not launching")
    elif is_running():
        print("      already running")
    else:
        exe = find_savvycan()
        if exe is None:
            print("      SavvyCAN binary not found — skipping launch.")
            print("      Looked in PATH and ~/Downloads/SavvyCAN-x86_64.AppImage")
        else:
            launch(exe)
            print(f"      launched {exe.name}; it will auto-connect on startup")

    print("[4/4] capture")
    sinks = []
    if not args.no_savvycan:
        from .sinks.vcan import VCanSink
        try:
            sinks.append(VCanSink({c: VCAN_FOR_CHANNEL[c] for c in chans}))
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    session = Path(args.session_dir or
                   f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    backend_kwargs = {"channels": args.channels, "bitrate": args.bitrate,
                      "reconnect": True}
    sess = LiveSession("canalystii", session, backend_kwargs, sinks=sinks)
    sess.run()

    if args.analyze:
        print("\nRunning post-capture pipeline...")
        return _run_pipeline_for(session, args)
    return 0


def _run_pipeline_for(session: Path, args: argparse.Namespace) -> int:
    from .pipeline import run_pipeline
    reg = Path(args.registry).expanduser() if getattr(args, "registry", None) else None
    dbc = Path(args.dbc).expanduser() if getattr(args, "dbc", None) else None
    try:
        res = run_pipeline(session, registry_path=reg, dbc_out=dbc,
                           promote=getattr(args, "promote", False))
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"\nreport:   {res['report']}")
    print(f"evidence: {res['evidence_file']}")
    if res.get("dbc"):
        print(f"dbc:      {res['dbc']}")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    """Headless capture — no TUI, no operator."""
    from .provision import ensure_vcan
    from .recorder import HeadlessRecorder

    chans = _parse_channels(args.channels)
    sinks = []
    if args.savvycan:
        from .sinks.vcan import VCanSink
        ifaces = [VCAN_FOR_CHANNEL[c] for c in chans]
        if not ensure_vcan(ifaces):
            print("warning: no vcan interfaces; continuing without the "
                  "SavvyCAN feed", file=sys.stderr)
        else:
            try:
                sinks.append(VCanSink({c: VCAN_FOR_CHANNEL[c] for c in chans}))
            except RuntimeError as e:
                print(f"warning: {e}", file=sys.stderr)

    if args.backend == "canalystii":
        backend_kwargs = {"channels": args.channels, "bitrate": args.bitrate,
                          "reconnect": True}
    elif args.backend == "mock":
        backend_kwargs = {"duration_s": args.duration or 60.0, "speed": 1.0}
    else:
        backend_kwargs = {"speed": 0.0}

    rec = HeadlessRecorder(
        args.backend, Path(args.root).expanduser(),
        backend_kwargs=backend_kwargs,
        sinks=sinks, rotate_s=args.rotate, max_duration_s=args.duration,
    )
    rc = rec.run()
    if args.analyze:
        for seg in rec.segments:
            _run_pipeline_for(seg, args)
    return rc


def _cmd_pipeline(args: argparse.Namespace) -> int:
    return _run_pipeline_for(Path(args.session_dir).expanduser(), args)


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Check every link in the chain before you rely on it on a drive."""
    ok = True

    print("== adapter ==")
    usb_ok = False
    try:
        import usb.core
        dev = usb.core.find(idVendor=0x04D8, idProduct=0x0053)
        if dev is None:
            print("  MISSING  CANalyst-II (04d8:0053) is not on the USB bus")
            ok = False
        else:
            usb_ok = True
            print(f"  ok       CANalyst-II on bus {dev.bus} addr {dev.address}")
    except Exception as e:
        print(f"  ERROR    could not probe USB: {e!r}")
        ok = False

    print("== autosuspend ==")
    import glob
    suspect = []
    for p in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            base = Path(p).parent
            if (base / "idVendor").read_text().strip() != "04d8":
                continue
            if (base / "idProduct").read_text().strip() != "0053":
                continue
            ctrl = (base / "power" / "control").read_text().strip()
            if ctrl != "on":
                suspect.append(f"{base.name} power/control={ctrl}")
        except Exception:
            continue
    if suspect:
        print(f"  WARN     autosuspend enabled ({'; '.join(suspect)})")
        print("           run: drivox-canre setup-udev")
        ok = False
    elif usb_ok:
        print("  ok       autosuspend disabled for this device")
    else:
        print("  skipped  adapter not present")

    print("== vcan interfaces ==")
    for c in _parse_channels(args.channels):
        iface = VCAN_FOR_CHANNEL[c]
        if Path(f"/sys/class/net/{iface}").exists():
            print(f"  ok       {iface}")
        else:
            print(f"  MISSING  {iface} — run: drivox-canre setup-vcan")
            ok = False

    print("== python deps ==")
    for mod in ("can", "canalystii", "cantools", "yaml", "numpy", "rich"):
        try:
            __import__(mod)
            print(f"  ok       {mod}")
        except Exception as e:
            print(f"  MISSING  {mod} ({e.__class__.__name__})")
            ok = False

    print("\n" + ("all checks passed" if ok else "some checks failed — see above"))
    return 0 if ok else 1


def _cmd_replay(args: argparse.Namespace) -> int:
    from .tui.live import LiveSession
    session = Path(args.session_dir or
                   f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    LiveSession(f"replay:{args.csv}", session,
                backend_kwargs={"speed": args.speed}).run()
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    from .reports.analyze import build_state_from_csv, render_report
    from .core.markers import MarkerLog, Marker
    state = build_state_from_csv(args.csv)
    markers = MarkerLog()
    if args.markers and Path(args.markers).exists():
        import csv as _csv
        with open(args.markers) as fp:
            for row in _csv.DictReader(fp):
                markers.add(float(row["t_s"]), row["label"], row.get("note", ""))
    render_report(state, markers)
    return 0


def _cmd_export_dbc(args: argparse.Namespace) -> int:
    from .hypotheses import HypothesisRegistry
    from .dbc_export import export
    reg = HypothesisRegistry.load(args.input)
    levels = tuple(args.confidence.split(","))
    out = export(reg, args.out, confidences=levels)
    print(f"wrote {out}")
    return 0


def _cmd_hypotheses(args: argparse.Namespace) -> int:
    from .hypotheses import HypothesisRegistry
    console = Console()
    reg = HypothesisRegistry.load(args.input)
    if args.sub == "list":
        for fid in sorted(reg.frames):
            f = reg.frames[fid]
            console.print(f"[bold]0x{fid:03X}[/] {f.name}  "
                          f"({len(f.signals)} signals, {f.sender})")
            for s in f.signals:
                console.print(f"  - {s.name:<24} "
                              f"start={s.start_bit:>3} len={s.length_bits:>2} "
                              f"[dim]{s.confidence}[/]")
        return 0
    if args.sub == "confirm":
        for f in reg.frames.values():
            for s in f.signals:
                if s.name == args.name:
                    s.confidence = args.level
                    reg.save(args.input)
                    console.print(f"set {s.name} -> {args.level}")
                    return 0
        console.print(f"[red]signal {args.name} not found[/]")
        return 1
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="drivox-canre")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("live", help="run live TUI session against a backend")
    pl.add_argument("backend",
                    help="mock | canalystii | panda | replay:<path>")
    pl.add_argument("--session-dir", default=None)
    pl.add_argument("--mock-duration", type=float, default=120.0)
    pl.add_argument("--mock-speed", type=float, default=1.0)
    pl.add_argument("--replay-speed", type=float, default=1.0)
    pl.add_argument("--savvycan", action="store_true",
                    help="also republish frames on vcan0/vcan1 so SavvyCAN "
                         "can attach to the same live capture")
    pl.add_argument("--channels", default="0",
                    help="CANalyst-II channels to capture, e.g. '0' or '0,1'")
    pl.add_argument("--bitrate", type=int, default=500000)
    pl.add_argument("--no-reconnect", action="store_true",
                    help="fail instead of re-opening the adapter after a "
                         "USB dropout")
    pl.set_defaults(func=_cmd_live)

    pg = sub.add_parser("go",
                        help="one command: vcan up, SavvyCAN wired+launched, "
                             "dual-channel capture live")
    pg.add_argument("--channels", default="0,1")
    pg.add_argument("--bitrate", type=int, default=500000)
    pg.add_argument("--session-dir", default=None)
    pg.add_argument("--no-savvycan", action="store_true",
                    help="capture only; no vcan feed, no config, no launch")
    pg.add_argument("--no-launch", action="store_true",
                    help="wire SavvyCAN's config but don't start it")
    pg.add_argument("--analyze", action="store_true",
                    help="run the post-capture pipeline when the session ends")
    pg.add_argument("--registry", default=None, help="hypothesis YAML")
    pg.add_argument("--dbc", default=None, help="DBC output path")
    pg.add_argument("--promote", action="store_true",
                    help="apply confidence promotions on direct test matches")
    pg.set_defaults(func=_cmd_go)

    pr2 = sub.add_parser("record",
                         help="headless capture for unattended / fleet use")
    pr2.add_argument("--root", default="./captures",
                     help="directory to write session_* segments into")
    pr2.add_argument("--backend", default="canalystii",
                     help="canalystii | mock | replay:<path>")
    pr2.add_argument("--channels", default="0,1")
    pr2.add_argument("--bitrate", type=int, default=500000)
    pr2.add_argument("--rotate", type=float, default=0.0,
                     help="start a new segment every N seconds (0 = never)")
    pr2.add_argument("--duration", type=float, default=0.0,
                     help="stop after N seconds (0 = until signalled)")
    pr2.add_argument("--savvycan", action="store_true",
                     help="also publish to vcan for a live SavvyCAN viewer")
    pr2.add_argument("--analyze", action="store_true")
    pr2.add_argument("--registry", default=None)
    pr2.add_argument("--dbc", default=None)
    pr2.add_argument("--promote", action="store_true")
    pr2.set_defaults(func=_cmd_record)

    pp = sub.add_parser("pipeline",
                        help="session -> analysis -> marker evidence -> DBC")
    pp.add_argument("session_dir")
    pp.add_argument("--registry", default=None, help="hypothesis YAML")
    pp.add_argument("--dbc", default=None, help="DBC output path")
    pp.add_argument("--promote", action="store_true",
                    help="promote confidence where a marker matches the "
                         "signal's recorded confirming_test (off by default)")
    pp.set_defaults(func=_cmd_pipeline)

    pv = sub.add_parser("setup-vcan",
                        help="create the vcan interfaces SavvyCAN attaches to")
    pv.add_argument("--channels", default="0,1")
    pv.set_defaults(func=_cmd_setup_vcan)

    pu = sub.add_parser("setup-udev",
                        help="install udev rule: no root needed, no autosuspend")
    pu.add_argument("--dry-run", action="store_true")
    pu.set_defaults(func=_cmd_setup_udev)

    pi = sub.add_parser("install-service",
                        help="udev rule + systemd units so vcan (and "
                             "optionally recording) survive reboot")
    pi.add_argument("--channels", default="0,1")
    pi.add_argument("--bitrate", type=int, default=500000)
    pi.add_argument("--recorder", action="store_true",
                    help="also install the headless recorder unit")
    pi.add_argument("--enable-recorder", action="store_true",
                    help="start the recorder at boot (implies --recorder)")
    pi.add_argument("--root", default="~/captures",
                    help="where the recorder unit writes sessions")
    pi.add_argument("--rotate", type=int, default=600)
    pi.add_argument("--dry-run", action="store_true",
                    help="print what would be done without doing it")
    pi.set_defaults(func=_cmd_install_service)

    pd = sub.add_parser("doctor",
                        help="check adapter, autosuspend, vcan and deps")
    pd.add_argument("--channels", default="0")
    pd.set_defaults(func=_cmd_doctor)

    pr = sub.add_parser("replay", help="replay an existing CSV in the live TUI")
    pr.add_argument("csv")
    pr.add_argument("--session-dir", default=None)
    pr.add_argument("--speed", type=float, default=1.0,
                    help="replay speed multiplier; 0 = as-fast-as-possible")
    pr.set_defaults(func=_cmd_replay)

    pa = sub.add_parser("analyze", help="batch analysis on a captured CSV")
    pa.add_argument("csv")
    pa.add_argument("--markers", default=None,
                    help="markers.csv produced by a live session")
    pa.set_defaults(func=_cmd_analyze)

    px = sub.add_parser("export-dbc", help="export hypothesis registry to DBC")
    px.add_argument("input", help="hypothesis YAML file")
    px.add_argument("out", help="output DBC path")
    px.add_argument("--confidence", default="medium,high,confirmed",
                    help="comma-separated confidence levels to include")
    px.set_defaults(func=_cmd_export_dbc)

    ph = sub.add_parser("hypotheses", help="list / confirm hypotheses")
    ph.add_argument("input", help="hypothesis YAML file")
    ph_sub = ph.add_subparsers(dest="sub", required=True)
    ph_list = ph_sub.add_parser("list")
    ph_conf = ph_sub.add_parser("confirm")
    ph_conf.add_argument("name", help="signal name to update")
    ph_conf.add_argument("level",
                         choices=["low", "medium", "high", "confirmed"])
    ph.set_defaults(func=_cmd_hypotheses)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
