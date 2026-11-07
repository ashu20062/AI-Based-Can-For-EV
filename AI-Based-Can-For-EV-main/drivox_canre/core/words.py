"""Adjacent-byte correlation to detect 16-bit (or wider) words."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(slots=True)
class WordCandidate:
    byte_lo: int        # lower index (in raw frame)
    byte_hi: int        # higher index
    correlation: float  # Pearson r of byte values across frames
    endianness: str     # 'BE' or 'LE' best guess based on which produces smoother sequence
    sample_min: int
    sample_max: int


def find_words(byte_series: Sequence[Sequence[int]],
               min_corr: float = 0.6,
               min_samples: int = 16) -> list[WordCandidate]:
    """byte_series: list of length-N sequences (one per byte position).

    Returns adjacent pairs whose absolute Pearson correlation exceeds min_corr.
    Endianness chosen as the orientation that yields lower
    first-difference variance (i.e. the "smoother" interpretation).
    """
    out: list[WordCandidate] = []
    n = len(byte_series)
    for i in range(n - 1):
        a = np.asarray(byte_series[i], dtype=float)
        b = np.asarray(byte_series[i + 1], dtype=float)
        if len(a) < min_samples or len(b) < min_samples:
            continue
        if np.std(a) == 0 or np.std(b) == 0:
            continue
        m = min(len(a), len(b))
        r = float(np.corrcoef(a[:m], b[:m])[0, 1])
        if abs(r) < min_corr:
            continue
        be = a[:m] * 256.0 + b[:m]
        le = b[:m] * 256.0 + a[:m]
        be_var = float(np.var(np.diff(be)))
        le_var = float(np.var(np.diff(le)))
        endian = "BE" if be_var <= le_var else "LE"
        chosen = be if endian == "BE" else le
        out.append(WordCandidate(
            byte_lo=i, byte_hi=i + 1,
            correlation=r,
            endianness=endian,
            sample_min=int(chosen.min()),
            sample_max=int(chosen.max()),
        ))
    return out
