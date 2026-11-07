"""Post-drive pipeline: session -> analysis -> marker evidence -> DBC.

Deliberately does *not* promote hypothesis confidence on its own by default.
Promoting a signal is a scientific claim, and the registry records a
`confirming_test` for each one precisely so that claim is made against a
stated test rather than a correlation. The pipeline gathers and ranks the
evidence; a human decides. `--promote` exists for when you have already
looked and want the obvious ones applied, and even then it only fires on an
explicit marker/confirming-test match, never on correlation alone.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .core.markers import Marker, MarkerLog
from .core.state import StateStore, format_id
from .core.windows import diff_around_marker
from .reports.analyze import build_state_from_csv, render_report


@dataclass
class Evidence:
    marker: str
    t: float
    channel: int
    can_id: int
    byte_idx: int
    interpretation: str
    novelty: float
    pre: tuple
    post: tuple


def load_markers(path: Path) -> MarkerLog:
    log = MarkerLog()
    if not path.exists():
        return log
    with path.open() as fp:
        for row in csv.DictReader(fp):
            try:
                log.add(float(row["t_s"]), row["label"], row.get("note", ""))
            except (KeyError, ValueError):
                continue
    return log


def gather_evidence(state: StateStore, markers: MarkerLog,
                    pre_s: float = 5.0, post_s: float = 5.0,
                    top_per_marker: int = 20) -> list[Evidence]:
    out: list[Evidence] = []
    states = state.snapshot_ids()
    for m in markers.all():
        deltas = diff_around_marker(states, m, pre_s=pre_s, post_s=post_s)
        for d in deltas[:top_per_marker]:
            out.append(Evidence(
                marker=m.label, t=m.t, channel=d.channel, can_id=d.can_id,
                byte_idx=d.byte_idx, interpretation=d.interpretation,
                novelty=d.novelty, pre=d.pre_value_set, post=d.post_value_set,
            ))
    return out


def _signal_byte_span(start_bit: int, length_bits: int) -> set[int]:
    """Approximate byte span a signal occupies.

    Bit numbering differs between DBC big/little endian conventions, so this
    is indicative only — it is used to *rank* evidence for a human, never to
    decide anything on its own.
    """
    first = start_bit // 8
    last = (start_bit + max(1, length_bits) - 1) // 8
    return set(range(first, last + 1))


def correlate_with_registry(evidence: list[Evidence], registry) -> list[dict]:
    """Match evidence against hypotheses on the same ID and overlapping bytes."""
    hits: list[dict] = []
    for ev in evidence:
        frame = registry.frames.get(ev.can_id)
        if frame is None:
            continue
        for sig in frame.signals:
            if ev.byte_idx not in _signal_byte_span(sig.start_bit, sig.length_bits):
                continue
            test = (sig.confirming_test or "").lower()
            label = ev.marker.lower()
            direct = bool(test) and (label in test or label.split("_")[0] in test)
            hits.append({
                "signal": sig.name,
                "can_id": ev.can_id,
                "channel": ev.channel,
                "byte": ev.byte_idx,
                "marker": ev.marker,
                "novelty": ev.novelty,
                "interpretation": ev.interpretation,
                "confidence": sig.confidence,
                "direct_test_match": direct,
                "confirming_test": sig.confirming_test,
            })
    hits.sort(key=lambda h: (not h["direct_test_match"], -h["novelty"]))
    return hits


def _write_evidence_md(path: Path, evidence: list[Evidence],
                       hits: list[dict], multi: bool) -> None:
    lines = ["# Marker evidence", ""]
    if not evidence:
        lines += ["No markers in this session, or no byte deltas around them.", ""]
    else:
        by_marker: dict[str, list[Evidence]] = {}
        for ev in evidence:
            by_marker.setdefault(f"{ev.marker} @ {ev.t:.2f}s", []).append(ev)
        for title, evs in by_marker.items():
            lines += [f"## {title}", "",
                      "| ID | byte | interpretation | novelty | pre | post |",
                      "|---|---|---|---|---|---|"]
            for ev in evs:
                lines.append(
                    f"| {format_id(ev.can_id, ev.channel, with_channel=multi)} "
                    f"| {ev.byte_idx} | {ev.interpretation} | {ev.novelty:.2f} "
                    f"| {' '.join(f'{v:02X}' for v in ev.pre[:6])} "
                    f"| {' '.join(f'{v:02X}' for v in ev.post[:6])} |")
            lines.append("")

    lines += ["# Registry correlation", ""]
    if not hits:
        lines += ["No evidence landed on a byte covered by an existing "
                  "hypothesis.", ""]
    else:
        lines += ["`direct` means the marker label matches the signal's "
                  "recorded confirming_test — the only case `--promote` acts "
                  "on.", "",
                  "| signal | ID | byte | marker | novelty | confidence | direct |",
                  "|---|---|---|---|---|---|---|"]
        for h in hits:
            lines.append(
                f"| {h['signal']} "
                f"| {format_id(h['can_id'], h['channel'], with_channel=multi)} "
                f"| {h['byte']} | {h['marker']} | {h['novelty']:.2f} "
                f"| {h['confidence']} | {'yes' if h['direct_test_match'] else 'no'} |")
        lines.append("")
    path.write_text("\n".join(lines))


_NEXT = {"low": "medium", "medium": "high", "high": "confirmed"}


def run_pipeline(session_dir: Path, registry_path: Path | None = None,
                 dbc_out: Path | None = None, promote: bool = False,
                 confidences: tuple[str, ...] = ("medium", "high", "confirmed"),
                 console: Console | None = None) -> dict:
    console = console or Console()
    session_dir = Path(session_dir)
    frames_csv = session_dir / "can_frames.csv"
    if not frames_csv.exists():
        raise FileNotFoundError(f"no can_frames.csv in {session_dir}")

    result: dict = {"session": str(session_dir)}

    console.print(f"[bold]1/4[/] loading {frames_csv}")
    state = build_state_from_csv(frames_csv)
    markers = load_markers(session_dir / "markers.csv")
    result.update(frames=state.frames_seen, ids=len(state.snapshot_ids()),
                  markers=len(markers.all()),
                  multichannel=state.is_multichannel)
    console.print(f"      {state.frames_seen} frames, "
                  f"{len(state.snapshot_ids())} ids, "
                  f"{len(markers.all())} markers")

    console.print("[bold]2/4[/] writing analysis report")
    report_path = session_dir / "analysis_report.txt"
    with report_path.open("w") as fp:
        render_report(state, markers, console=Console(file=fp, width=160))
    result["report"] = str(report_path)

    console.print("[bold]3/4[/] gathering marker evidence")
    evidence = gather_evidence(state, markers)
    hits: list[dict] = []
    registry = None
    if registry_path is not None:
        from .hypotheses import HypothesisRegistry
        registry = HypothesisRegistry.load(registry_path)
        hits = correlate_with_registry(evidence, registry)
    ev_path = session_dir / "evidence.md"
    _write_evidence_md(ev_path, evidence, hits, state.is_multichannel)
    result.update(evidence=len(evidence), registry_hits=len(hits),
                  evidence_file=str(ev_path))
    console.print(f"      {len(evidence)} deltas, {len(hits)} touch a hypothesis")

    promoted: list[str] = []
    if promote and registry is not None:
        for h in hits:
            if not h["direct_test_match"]:
                continue
            frame = registry.frames.get(h["can_id"])
            for sig in (frame.signals if frame else []):
                if sig.name != h["signal"]:
                    continue
                nxt = _NEXT.get(sig.confidence)
                if nxt:
                    sig.confidence = nxt
                    promoted.append(f"{sig.name}: -> {nxt}")
        if promoted:
            registry.save(registry_path)
    result["promoted"] = promoted
    for p in promoted:
        console.print(f"      [yellow]promoted[/] {p}")

    if dbc_out is not None and registry is not None:
        console.print(f"[bold]4/4[/] exporting DBC -> {dbc_out}")
        from .dbc_export import export
        export(registry, dbc_out, confidences=confidences)
        result["dbc"] = str(dbc_out)
    else:
        console.print("[bold]4/4[/] skipping DBC (no registry/output given)")

    return result
