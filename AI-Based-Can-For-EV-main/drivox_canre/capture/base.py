"""Abstract capture backend and the Frame type shared across the app."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(slots=True, frozen=True)
class Frame:
    t: float          # seconds since capture start
    can_id: int
    dlc: int
    data: bytes       # right-padded to 8 if needed by backend
    channel: int = 0  # source bus on multi-channel adapters; 0 on single-bus

    @property
    def id_hex(self) -> str:
        return f"0x{self.can_id:03X}"

    @property
    def is_extended(self) -> bool:
        return self.can_id > 0x7FF


class Backend(Protocol):
    name: str

    def frames(self) -> Iterator[Frame]:
        """Yield Frame objects until stopped or exhausted."""
        ...

    def close(self) -> None:
        ...
