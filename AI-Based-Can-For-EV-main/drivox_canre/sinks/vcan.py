"""Virtual-SocketCAN sink — feeds SavvyCAN (or candump, or anything else).

SavvyCAN cannot talk to the CANalyst-II directly: it is a proprietary
ControlCAN USB device, not a serial/slcan adapter. Re-publishing the captured
frames onto virtual SocketCAN interfaces lets stock SavvyCAN attach with
    Connection -> Add New Device Connection
    -> QT SerialBus Devices  ->  socketcan / vcan0

Each hardware channel gets its own vcan interface, so bus identity survives the
hop — merging both CAN buses onto one vcan would make ch0 and ch1 frames with
the same arbitration ID indistinguishable.
"""
from __future__ import annotations

from ..capture.base import Frame


class VCanSink:
    """Publish frames to vcan interfaces, one per hardware channel."""

    name = "vcan"

    def __init__(self, channel_map: dict[int, str] | None = None):
        self.channel_map = channel_map or {0: "vcan0", 1: "vcan1"}
        self._buses: dict[int, object] = {}
        self.sent = 0
        self.errors = 0
        self._unmapped: set[int] = set()
        self._open()

    def _open(self) -> None:
        import can

        missing = [n for n in self.channel_map.values() if not _iface_exists(n)]
        if missing:
            raise RuntimeError(
                f"virtual CAN interface(s) not found: {', '.join(missing)}.\n"
                f"Create them with:  drivox-canre setup-vcan\n"
                f"(or manually: sudo modprobe vcan && "
                f"sudo ip link add dev {missing[0]} type vcan && "
                f"sudo ip link set up {missing[0]})"
            )

        for ch, iface in self.channel_map.items():
            self._buses[ch] = can.interface.Bus(channel=iface, interface="socketcan")
        # Recorded separately: stats() is read after close() when the session
        # writes session_meta.txt, and close() drops the bus handles.
        self._active_ifaces = [self.channel_map[c] for c in sorted(self._buses)]

    def write(self, frame: Frame) -> None:
        bus = self._buses.get(frame.channel)
        if bus is None:
            self._unmapped.add(frame.channel)
            return
        try:
            import can

            bus.send(
                can.Message(
                    arbitration_id=frame.can_id,
                    data=frame.data,
                    dlc=frame.dlc,
                    is_extended_id=frame.is_extended,
                ),
                timeout=0,
            )
            self.sent += 1
        except Exception:
            # vcan has a finite txqueue; a full queue drops this frame only.
            self.errors += 1

    def close(self) -> None:
        for bus in self._buses.values():
            try:
                bus.shutdown()
            except Exception:
                pass
        self._buses.clear()

    def stats(self) -> str:
        ifaces = ",".join(getattr(self, "_active_ifaces", []))
        s = f"{ifaces} tx={self.sent}"
        if self.errors:
            s += f" drop={self.errors}"
        if self._unmapped:
            s += f" unmapped_ch={sorted(self._unmapped)}"
        return s


def _iface_exists(name: str) -> bool:
    from pathlib import Path

    return Path(f"/sys/class/net/{name}").exists()
