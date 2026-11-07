from .base import Sink

__all__ = ["Sink", "build_sink"]


def build_sink(spec: str, **kwargs) -> Sink:
    """Build a sink from a string spec.

    spec values:
      vcan          -> republish onto vcan0/vcan1 for SavvyCAN, candump, ...
    """
    if spec == "vcan":
        from .vcan import VCanSink

        return VCanSink(**kwargs)
    raise ValueError(f"unknown sink spec: {spec}")
