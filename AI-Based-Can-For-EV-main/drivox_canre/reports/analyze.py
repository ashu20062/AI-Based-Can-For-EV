"""Offline analysis: load a capture (CSV or live state), produce a
structured report following the project's required output format.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..capture.replay import ReplayBackend
from ..core.classify import classify_byte
from ..core.markers import MarkerLog, Marker
from ..core.periodicity import analyze as analyze_periodicity
from ..core.state import StateStore, IdState, format_id
from ..core.windows import diff_around_marker
from ..core.words import find_words


@dataclass(slots=True)
class IdReport:
    can_id: int
    channel: int
    state: IdState
    period_kind: str
    period_ms: float
    rate_hz: float
    cv: float
    byte_classes: list
    word_candidates: list


def build_state_from_csv(path: str | Path) -> StateStore:
    state = StateStore()
    backend = ReplayBackend(path, speed=0.0)
    for frame in backend.frames():
        state.observe(frame)
    return state


def report_id(s: IdState, duration: float) -> IdReport:
    intervals = list(s.arrival_intervals)
    p = analyze_periodicity(intervals, s.frame_count, duration,
                            s.first_seen_t, s.last_seen_t)
    byte_classes = [classify_byte(list(s.byte_history[i]))
                    for i in range(len(s.byte_history))]
    words = find_words([list(h) for h in s.byte_history])
    return IdReport(
        can_id=s.can_id, channel=s.channel, state=s,
        period_kind=p.kind, period_ms=p.period_ms, rate_hz=p.rate_hz, cv=p.cv,
        byte_classes=byte_classes, word_candidates=words,
    )


def render_report(state: StateStore, markers: MarkerLog | None = None,
                  top_n_ids: int = 50, console: Console | None = None) -> None:
    console = console or Console()
    duration = (state.last_t or 0.0)
    multi = state.is_multichannel

    summary = Table.grid(padding=(0, 1))
    summary.add_column("k", style="bold")
    summary.add_column("v")
    summary.add_row("duration", f"{duration:.2f} s")
    summary.add_row("frames",  f"{state.frames_seen}")
    summary.add_row("ids",     f"{len(state.snapshot_ids())}")
    summary.add_row("rate",    f"{state.frames_seen / max(duration, 1e-6):.1f} fps")
    if multi:
        per_ch = {}
        for s in state.snapshot_ids():
            per_ch.setdefault(s.channel, [0, 0])
            per_ch[s.channel][0] += 1
            per_ch[s.channel][1] += s.frame_count
        summary.add_row("channels", ", ".join(
            f"ch{c}: {n_ids} ids / {n_fr} frames"
            for c, (n_ids, n_fr) in sorted(per_ch.items())))
    console.print(Panel(summary, title="capture summary"))

    ids = sorted(state.snapshot_ids(),
                 key=lambda s: (-s.frame_count, s.channel, s.can_id))[:top_n_ids]
    t = Table(title="per-ID overview", show_lines=False)
    t.add_column("ID")
    t.add_column("frames", justify="right")
    t.add_column("Hz", justify="right")
    t.add_column("kind")
    t.add_column("period (ms)", justify="right")
    t.add_column("cv", justify="right")
    t.add_column("active span", justify="right")
    for s in ids:
        r = report_id(s, duration)
        active = max(0.0, s.last_seen_t - s.first_seen_t)
        t.add_row(
            format_id(s.can_id, s.channel, with_channel=multi),
            str(s.frame_count),
            f"{r.rate_hz:5.1f}",
            r.period_kind,
            f"{r.period_ms:7.1f}",
            f"{r.cv:4.2f}",
            f"{active:6.1f}s",
        )
    console.print(t)

    bt = Table(title="byte classification", show_lines=False)
    bt.add_column("ID")
    bt.add_column("b")
    bt.add_column("kind")
    bt.add_column("uniq", justify="right")
    bt.add_column("notes", style="dim")
    for s in ids:
        r = report_id(s, duration)
        first = True
        for i, c in enumerate(r.byte_classes):
            if c.kind == "constant" and c.unique_values <= 1:
                continue
            bt.add_row(
                format_id(s.can_id, s.channel, with_channel=multi) if first else "",
                str(i), c.kind, str(c.unique_values), c.notes,
            )
            first = False
    console.print(bt)

    wt = Table(title="16-bit word candidates (|r| >= 0.6)", show_lines=False)
    wt.add_column("ID")
    wt.add_column("bytes")
    wt.add_column("r", justify="right")
    wt.add_column("endian")
    wt.add_column("range", justify="right")
    for s in ids:
        r = report_id(s, duration)
        for w in r.word_candidates:
            wt.add_row(
                format_id(s.can_id, s.channel, with_channel=multi),
                f"{w.byte_lo}+{w.byte_hi}",
                f"{w.correlation:+.2f}",
                w.endianness,
                f"{w.sample_min}..{w.sample_max}",
            )
    console.print(wt)

    if markers:
        mks = markers.all()
        if mks:
            console.print(Panel(f"{len(mks)} markers in capture",
                                title="markers", border_style="magenta"))
            for m in mks:
                _render_marker_diff(state, m, console)


def _render_marker_diff(state: StateStore, m: Marker, console: Console) -> None:
    multi = state.is_multichannel
    deltas = diff_around_marker(state.snapshot_ids(), m, pre_s=5, post_s=5)
    if not deltas:
        console.print(f"[dim]marker {m.label} @ {m.t:.2f}s: no byte deltas detected[/]")
        return
    t = Table(title=f"marker {m.label} @ {m.t:.2f}s — top byte deltas")
    t.add_column("ID")
    t.add_column("byte", justify="right")
    t.add_column("interpretation")
    t.add_column("pre", style="dim")
    t.add_column("post", style="dim")
    t.add_column("novelty", justify="right")
    for d in deltas[:15]:
        t.add_row(
            format_id(d.can_id, d.channel, with_channel=multi),
            str(d.byte_idx),
            d.interpretation,
            " ".join(f"{v:02X}" for v in d.pre_value_set[:6]),
            " ".join(f"{v:02X}" for v in d.post_value_set[:6]),
            f"{d.novelty:.2f}",
        )
    console.print(t)
