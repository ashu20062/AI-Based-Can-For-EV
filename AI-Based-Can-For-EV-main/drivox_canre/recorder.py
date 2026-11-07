"""Headless recording — no TUI, no operator.

For fleet collection and for leaving a capture running over SSH. Rotates
session directories so a long run does not become one unbounded CSV, and
survives adapter dropouts because the backend reconnects underneath it.

Ends cleanly on SIGINT/SIGTERM so systemd can stop it without losing the
tail of the current segment.
"""
from __future__ import annotations

import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .capture import build_backend
from .core.state import StateStore
from .session import frame_row, open_frame_csv, write_meta


class HeadlessRecorder:
    def __init__(self, backend_spec: str, root: Path,
                 backend_kwargs: dict | None = None,
                 sinks: list | None = None,
                 rotate_s: float = 0.0,
                 max_duration_s: float = 0.0,
                 status_every_s: float = 30.0):
        self.backend_spec = backend_spec
        self.backend_kwargs = backend_kwargs or {}
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sinks = list(sinks or [])
        self.rotate_s = rotate_s
        self.max_duration_s = max_duration_s
        self.status_every_s = status_every_s

        self.backend = None
        self._stop = False
        self.segments: list[Path] = []

    def _install_signals(self) -> None:
        def handler(signum, _frame):
            if not self._stop:
                print(f"\n[recorder] signal {signum}, finishing segment...",
                      flush=True)
            self._stop = True
            if self.backend is not None:
                try:
                    self.backend.close()
                except Exception:
                    pass

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass    # not on the main thread

    def _new_segment(self) -> tuple[Path, object, object, StateStore, float]:
        d = self.root / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        d.mkdir(parents=True, exist_ok=True)
        fp, writer = open_frame_csv(d / "can_frames.csv")
        self.segments.append(d)
        print(f"[recorder] segment -> {d}", flush=True)
        return d, fp, writer, StateStore(), time.time()

    def _close_segment(self, d: Path, fp, state: StateStore,
                       start_wall: float) -> None:
        fp.close()
        if state.frames_seen == 0:
            # A rotation landing on the stop boundary would otherwise leave an
            # empty session directory behind, which reads as a failed capture.
            try:
                (d / "can_frames.csv").unlink(missing_ok=True)
                d.rmdir()
                if d in self.segments:
                    self.segments.remove(d)
                print(f"[recorder] discarded empty segment {d.name}", flush=True)
                return
            except OSError:
                pass    # not empty after all; fall through and keep it
        write_meta(
            d / "session_meta.txt",
            backend_spec=self.backend_spec,
            start_wall=start_wall,
            duration_s=state.last_t or 0.0,
            frames=state.frames_seen,
            unique_ids=len(state.snapshot_ids()),
            backend=self.backend, sinks=self.sinks,
            extra={"mode": "headless"},
        )
        print(f"[recorder] closed {d.name}: {state.frames_seen} frames, "
              f"{len(state.snapshot_ids())} ids", flush=True)

    def run(self) -> int:
        self._install_signals()
        self.backend = build_backend(self.backend_spec, **self.backend_kwargs)

        d, fp, writer, state, start_wall = self._new_segment()
        t_run0 = time.monotonic()
        t_seg0 = t_run0
        t_status = t_run0

        try:
            for frame in self.backend.frames():
                if self._stop:
                    break
                state.observe(frame)
                writer.writerow(frame_row(frame))
                for sink in list(self.sinks):
                    try:
                        sink.write(frame)
                    except Exception as e:
                        print(f"[recorder] sink {sink.name} dropped: {e!r}",
                              flush=True)
                        self.sinks.remove(sink)

                now = time.monotonic()
                if self.status_every_s and now - t_status >= self.status_every_s:
                    t_status = now
                    rc = getattr(self.backend, "reconnect_count", 0)
                    print(f"[recorder] {state.frames_seen} frames, "
                          f"{len(state.snapshot_ids())} ids, "
                          f"{now - t_run0:.0f}s elapsed"
                          + (f", {rc} reconnects" if rc else ""), flush=True)

                # Checked before rotation so a run ending on a segment
                # boundary stops cleanly instead of opening a segment it will
                # never write to.
                if self.max_duration_s and now - t_run0 >= self.max_duration_s:
                    break

                if self.rotate_s and now - t_seg0 >= self.rotate_s:
                    self._close_segment(d, fp, state, start_wall)
                    d, fp, writer, state, start_wall = self._new_segment()
                    t_seg0 = now
        except Exception as e:
            print(f"[recorder] capture ended: {e!r}", file=sys.stderr, flush=True)
        finally:
            self._stop = True
            try:
                self.backend.close()
            except Exception:
                pass
            for sink in self.sinks:
                try:
                    sink.close()
                except Exception:
                    pass
            self._close_segment(d, fp, state, start_wall)

        print(f"[recorder] done. {len(self.segments)} segment(s) under {self.root}",
              flush=True)
        return 0
