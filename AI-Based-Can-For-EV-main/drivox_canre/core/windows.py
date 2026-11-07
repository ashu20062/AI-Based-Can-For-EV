"""Pre/post-marker diff windows: 'what changed when you pressed S?'."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .markers import Marker
from .state import IdState, ByteChange


@dataclass(slots=True)
class WindowedByteDelta:
    can_id: int
    byte_idx: int
    channel: int
    pre_value_set: tuple[int, ...]
    post_value_set: tuple[int, ...]
    pre_variance: float
    post_variance: float
    novelty: float          # fraction of post values not seen in pre window
    interpretation: str


def _byte_values_in_window(state: IdState, byte_idx: int,
                           t_start: float, t_end: float) -> list[int]:
    """Linear walk through recent_changes for the time window.
    NOTE: this is a coarse approximation when the byte history deque is
    longer than the change history. Sufficient for marker-anchored diffs
    over ~10s windows."""
    # Walk recent_changes for that byte in window
    return [c.new for c in state.recent_changes
            if c.byte_idx == byte_idx and t_start <= c.t <= t_end]


def _variance(values: Sequence[int]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / len(values)


def diff_around_marker(states: Iterable[IdState], marker: Marker,
                       pre_s: float = 5.0, post_s: float = 5.0,
                       guard_s: float = 0.5) -> list[WindowedByteDelta]:
    """For each (id, byte) compute pre/post value-set delta around marker."""
    out: list[WindowedByteDelta] = []
    pre_start = marker.t - pre_s
    pre_end = marker.t - guard_s
    post_start = marker.t + guard_s
    post_end = marker.t + post_s

    for s in states:
        if s.last_seen_t < pre_start or s.first_seen_t > post_end:
            continue
        n_bytes = len(s.byte_history)
        for i in range(n_bytes):
            pre = _byte_values_in_window(s, i, pre_start, pre_end)
            post = _byte_values_in_window(s, i, post_start, post_end)
            if not pre and not post:
                continue
            pre_set = set(pre)
            post_set = set(post)
            if pre_set == post_set and not post:
                continue
            new_vals = post_set - pre_set
            novelty = len(new_vals) / max(1, len(post_set))
            interp = _interpret(pre_set, post_set, novelty)
            if interp == "no_change":
                continue
            out.append(WindowedByteDelta(
                can_id=s.can_id, byte_idx=i, channel=s.channel,
                pre_value_set=tuple(sorted(pre_set)),
                post_value_set=tuple(sorted(post_set)),
                pre_variance=_variance(pre), post_variance=_variance(post),
                novelty=novelty,
                interpretation=interp,
            ))
    out.sort(key=lambda d: (-d.novelty, -d.post_variance,
                            d.channel, d.can_id, d.byte_idx))
    return out


def _interpret(pre: set[int], post: set[int], novelty: float) -> str:
    if not pre and not post:
        return "no_change"
    if not pre and post:
        return "started_changing"
    if pre and not post:
        return "stopped_changing"
    if pre == post:
        return "no_change"
    if novelty >= 0.5:
        return "new_values_dominant"
    if novelty > 0:
        return "new_values_present"
    return "value_set_shrank"
