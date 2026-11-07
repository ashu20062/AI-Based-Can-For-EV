"""Classify a sequence of byte values into a behaviour category."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(slots=True)
class ByteClassification:
    kind: str           # constant | toggle | counter | low_entropy | high_entropy
    unique_values: int
    value_set: tuple[int, ...]
    entropy_bits: float
    notes: str = ""


def _entropy_bits(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    n = len(values)
    counts = Counter(values)
    from math import log2
    return -sum((c / n) * log2(c / n) for c in counts.values() if c > 0)


def _looks_like_counter(values: Sequence[int]) -> bool:
    """Heuristic: monotonic with a small step, or strictly increasing modulo N."""
    if len(values) < 8:
        return False
    diffs = [(b - a) % 256 for a, b in zip(values, values[1:])]
    if not diffs:
        return False
    common_step = Counter(diffs).most_common(1)[0]
    step_val, step_count = common_step
    if step_val == 0:
        return False
    return step_count / len(diffs) >= 0.85


def classify_byte(values: Iterable[int]) -> ByteClassification:
    vs = list(values)
    if not vs:
        return ByteClassification("constant", 0, (), 0.0)
    uniq = sorted(set(vs))
    n_uniq = len(uniq)
    ent = _entropy_bits(vs)
    if n_uniq == 1:
        return ByteClassification("constant", 1, tuple(uniq), 0.0,
                                  notes=f"value=0x{uniq[0]:02X}")
    if n_uniq == 2:
        return ByteClassification("toggle", 2, tuple(uniq), ent,
                                  notes=f"flip between 0x{uniq[0]:02X} and 0x{uniq[1]:02X}")
    if _looks_like_counter(vs):
        diffs = [(b - a) % 256 for a, b in zip(vs, vs[1:])]
        step = Counter(diffs).most_common(1)[0][0]
        return ByteClassification("counter", n_uniq, tuple(uniq[:8]), ent,
                                  notes=f"step={step}, {n_uniq} distinct values")
    if n_uniq <= 8:
        return ByteClassification("low_entropy", n_uniq, tuple(uniq), ent,
                                  notes=f"{n_uniq} discrete states")
    return ByteClassification("high_entropy", n_uniq, tuple(uniq[:8]), ent,
                              notes=f"{n_uniq} distinct values, entropy={ent:.2f} bits")
