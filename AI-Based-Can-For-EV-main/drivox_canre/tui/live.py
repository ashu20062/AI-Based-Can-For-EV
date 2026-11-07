"""Rich-based live dashboard. Runs capture in a background thread and
renders state + markers + recent changes at ~5 Hz.
"""
from __future__ import annotations

import csv
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..capture import build_backend
from ..capture.base import Backend, Frame
from ..core.markers import DEFAULT_MARKERS, MarkerLog
from ..core.periodicity import analyze
from ..core.state import StateStore, format_id
from .keys import RawKeyReader


REFRESH_HZ = 5.0
QUIT_KEYS = {"q", "Q", "\x03", "\x04"}   # q / Q / Ctrl-C / Ctrl-D


def _format_data(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def _format_changed(prev: bytes, cur: bytes) -> Text:
    text = Text()
    for i in range(8):
        if i >= len(cur):
            text.append("--", style="dim")
        else:
            byte = cur[i]
            if i < len(prev) and prev[i] != byte:
                text.append(f"{byte:02X}", style="bold yellow on red")
            else:
                text.append(f"{byte:02X}")
        if i < 7:
            text.append(" ")
    return text


class LiveSession:
    def __init__(self, backend_spec: str, session_dir: Path,
                 backend_kwargs: dict | None = None,
                 sinks: list | None = None):
        self.backend_spec = backend_spec
        self.backend_kwargs = backend_kwargs or {}
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.frames_csv = self.session_dir / "can_frames.csv"
        self.markers_csv = self.session_dir / "markers.csv"
        self.meta_txt = self.session_dir / "session_meta.txt"

        self.state = StateStore()
        self.markers = MarkerLog()
        self.sinks = list(sinks or [])
        self.backend: Optional[Backend] = None
        # Keyed on (channel, can_id): the same ID on two buses carries
        # unrelated payloads, and sharing a slot would cross-highlight them.
        self._prev_data: dict[tuple[int, int], bytes] = {}
        self._stop_evt = threading.Event()
        self._frame_writer = None
        self._frame_fp = None
        self._cap_thread: Optional[threading.Thread] = None
        self._t_start_wall: float = 0.0
        self._capture_error: Optional[str] = None
        self._sink_errors: dict[str, str] = {}

    def _capture_loop(self) -> None:
        try:
            for frame in self.backend.frames():
                if self._stop_evt.is_set():
                    return
                self.state.observe(frame)
                self._write_frame(frame)
                self._fan_out(frame)
                self._prev_data[(frame.channel, frame.can_id)] = frame.data
        except Exception as e:
            self._capture_error = repr(e)
            sys.stderr.write(f"\n[capture thread crashed] {e!r}\n")
        finally:
            try:
                self.backend.close()
            except Exception:
                pass

    def _fan_out(self, f: Frame) -> None:
        """Hand the frame to every secondary consumer.

        One misbehaving sink must never kill the capture, so failures are
        counted and the sink is dropped rather than propagated.
        """
        for sink in list(self.sinks):
            try:
                sink.write(f)
            except Exception as e:
                self._sink_errors[sink.name] = repr(e)
                self.sinks.remove(sink)

    def _write_frame(self, f: Frame) -> None:
        if self._frame_writer is None:
            return
        padded = list(f.data) + [""] * (8 - len(f.data))
        self._frame_writer.writerow([
            f"{f.t:.6f}",
            f"0x{f.can_id:03X}",
            f.dlc,
            *padded,
            f.data.hex(),
            "",
            f.channel,
        ])

    def run(self) -> None:
        self.backend = build_backend(self.backend_spec, **self.backend_kwargs)
        self._frame_fp = self.frames_csv.open("w", newline="")
        self._frame_writer = csv.writer(self._frame_fp)
        # `channel` is appended last so collector.py-era readers, which look
        # columns up by name, keep working against these files unchanged.
        self._frame_writer.writerow([
            "timestamp_s", "can_id_hex", "dlc",
            "b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7",
            "data_hex", "label", "channel",
        ])

        self._t_start_wall = time.time()
        self._cap_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._cap_thread.start()

        console = Console()
        layout = self._build_layout()

        try:
            with RawKeyReader() as keys, Live(layout, console=console,
                                              refresh_per_second=REFRESH_HZ,
                                              screen=True):
                interval = 1.0 / REFRESH_HZ
                next_tick = time.monotonic()
                while not self._stop_evt.is_set():
                    next_tick += interval
                    while True:
                        slack = next_tick - time.monotonic()
                        if slack <= 0:
                            break
                        ch = keys.poll(timeout=min(slack, 0.1))
                        if ch is None:
                            continue
                        if ch in QUIT_KEYS:
                            self._stop_evt.set()
                            break
                        self._handle_key(ch)
                    self._refresh_layout(layout)
        finally:
            self._stop_evt.set()
            if self._cap_thread:
                self._cap_thread.join(timeout=2.0)
            for sink in self.sinks:
                try:
                    sink.close()
                except Exception:
                    pass
            if self._frame_fp:
                self._frame_fp.close()
            self._write_meta()
            self.markers.save(self.markers_csv)
            print(f"\nSession saved to {self.session_dir}/")
            if self._capture_error:
                print(f"capture ended with: {self._capture_error}")
            for name, err in self._sink_errors.items():
                print(f"sink {name!r} dropped out: {err}")

    def _handle_key(self, ch: str) -> None:
        if ch == " ":
            self.markers.add(self.state.last_t, "space_marker")
            return
        label = DEFAULT_MARKERS.get(ch)
        if label is None:
            return
        if label == "custom":
            self.markers.add(self.state.last_t, "custom",
                             note=f"manual @ {self.state.last_t:.2f}")
            return
        self.markers.add(self.state.last_t, label)

    def _build_layout(self) -> Layout:
        root = Layout()
        root.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=8),
        )
        root["body"].split_row(
            Layout(name="ids", ratio=3),
            Layout(name="right", ratio=2),
        )
        root["right"].split_column(
            Layout(name="changes", ratio=1),
            Layout(name="markers", ratio=1),
        )
        return root

    def _refresh_layout(self, layout: Layout) -> None:
        # Each panel renders in isolation so one failure doesn't blank the dashboard.
        for name, fn in [
            ("header",  self._render_header),
            ("ids",     self._render_ids_table),
            ("changes", self._render_recent_changes),
            ("markers", self._render_markers),
            ("footer",  self._render_footer),
        ]:
            try:
                layout[name].update(fn())
            except Exception:
                pass

    def _render_header(self) -> Panel:
        elapsed = self.state.last_t if self.state.t0 is not None else 0.0
        frames = self.state.frames_seen
        rate = frames / elapsed if elapsed > 0 else 0.0
        n_ids = len(self.state.snapshot_ids())
        text = Text()
        text.append(" DRIVOX-CANRE ", style="bold reverse")
        text.append(f"  backend={self.backend.name}  ")
        text.append(f"t={elapsed:7.2f}s  ", style="cyan")
        text.append(f"frames={frames:>7}  ", style="cyan")
        text.append(f"rate={rate:6.0f}/s  ", style="cyan")
        text.append(f"ids={n_ids:>3}  ", style="cyan")
        text.append(f"markers={len(self.markers.all()):>3}  ", style="magenta")
        text.append(f"-> {self.session_dir.name}", style="dim")

        reconnects = getattr(self.backend, "reconnect_count", 0)
        if reconnects:
            text.append(f"  reconnects={reconnects}", style="bold red")
        if getattr(self.backend, "_dev", "n/a") is None:
            text.append("  [ADAPTER DOWN]", style="bold white on red")
        for sink in self.sinks:
            try:
                text.append(f"  {sink.stats()}", style="green")
            except Exception:
                pass
        for name in self._sink_errors:
            text.append(f"  {name}=DEAD", style="bold red")
        return Panel(text, padding=(0, 1))

    def _render_ids_table(self) -> Panel:
        multi = self.state.is_multichannel
        ids = sorted(self.state.snapshot_ids(),
                     key=lambda s: (s.channel, s.can_id))
        t = Table.grid(padding=(0, 1))
        t.add_column("ID", style="bold")
        t.add_column("frames", justify="right")
        t.add_column("Hz", justify="right")
        t.add_column("kind", style="dim")
        t.add_column("data (last)", style="white")
        t.add_row("[bold]ID[/]", "[bold]frames[/]", "[bold]Hz[/]",
                  "[bold]kind[/]", "[bold]b0..b7[/]")
        for s in ids:
            dur = max(0.001, s.last_seen_t - s.first_seen_t)
            rate = s.frame_count / dur if dur > 0 else 0
            report = analyze(list(s.arrival_intervals), s.frame_count, dur,
                             s.first_seen_t, s.last_seen_t)
            prev = self._prev_data.get((s.channel, s.can_id), b"")
            t.add_row(
                format_id(s.can_id, s.channel, with_channel=multi),
                str(s.frame_count),
                f"{rate:5.1f}",
                report.kind[:8],
                _format_changed(prev, s.last_data),
            )
        return Panel(t, title="IDs (q to quit)", border_style="cyan")

    def _render_recent_changes(self) -> Panel:
        recent = self.state.recent_changes(20)
        multi = self.state.is_multichannel
        t = Table.grid(padding=(0, 1))
        t.add_column("t", justify="right", style="dim")
        t.add_column("id")
        t.add_column("b", justify="right")
        t.add_column("delta")
        for c in reversed(recent):
            t.add_row(
                f"{c.t:6.2f}",
                format_id(c.can_id, c.channel, with_channel=multi),
                str(c.byte_idx),
                f"{c.old:02X}->{c.new:02X}",
            )
        return Panel(t, title="recent byte changes", border_style="yellow")

    def _render_markers(self) -> Panel:
        t = Table.grid(padding=(0, 1))
        t.add_column("t", justify="right", style="dim")
        t.add_column("label", style="magenta")
        t.add_column("note", style="dim")
        for m in reversed(self.markers.all()[-12:]):
            t.add_row(f"{m.t:6.2f}", m.label, m.note)
        return Panel(t, title="markers", border_style="magenta")

    def _render_footer(self) -> Panel:
        hint = Text()
        hint.append("Hotkeys: ", style="bold")
        groups = [
            ("S/D", "sport/drive"),
            ("R/r", "regen on/off"),
            ("B/b", "brake hard/release"),
            ("A/a", "AC on/off"),
            ("H/h", "headlights on/off"),
            ("T/t", "turn L/R"),
            ("0-5", "phase markers"),
            ("space", "generic mark"),
            ("q", "quit"),
        ]
        for i, (k, lbl) in enumerate(groups):
            hint.append(f"[{k}]", style="bold yellow")
            hint.append(f" {lbl}   ")
            if i % 5 == 4:
                hint.append("\n")
        return Panel(hint, title="markers", border_style="green")

    def _write_meta(self) -> None:
        elapsed = self.state.last_t or 0.0
        with self.meta_txt.open("w") as f:
            f.write(f"backend: {self.backend_spec}\n")
            f.write(f"start_wall_time: {self._t_start_wall}\n")
            f.write(f"duration_s: {elapsed:.2f}\n")
            f.write(f"total_frames: {self.state.frames_seen}\n")
            f.write(f"unique_ids: {len(self.state.snapshot_ids())}\n")
            f.write(f"marker_count: {len(self.markers.all())}\n")
            chans = getattr(self.backend, "channels", None)
            if chans is not None:
                f.write(f"channels: {','.join(str(c) for c in chans)}\n")
            f.write(f"reconnects: {getattr(self.backend, 'reconnect_count', 0)}\n")
            for span in getattr(self.backend, "dropped_spans", []) or []:
                f.write(f"gap_s: {span[0]:.2f} -> {span[1]:.2f}\n")
            for sink in self.sinks:
                try:
                    f.write(f"sink: {sink.name} {sink.stats()}\n")
                except Exception:
                    pass
            for name, err in self._sink_errors.items():
                f.write(f"sink_failed: {name} {err}\n")
            if self._capture_error:
                f.write(f"capture_error: {self._capture_error}\n")
