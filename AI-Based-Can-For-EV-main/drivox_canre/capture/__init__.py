from .base import Frame, Backend

__all__ = ["Frame", "Backend", "build_backend"]


def build_backend(spec: str, **kwargs) -> Backend:
    """Build a backend from a string spec.

    spec values:
      mock                  -> synthetic Xpres-T-like data
      replay:<path>         -> CSV replay
      canalystii            -> Waveshare CANalyst-II
      panda                 -> Comma.ai Panda (silent mode)
    """
    if spec == "mock":
        from .mock import MockBackend
        return MockBackend(**kwargs)
    if spec.startswith("replay:"):
        from .replay import ReplayBackend
        return ReplayBackend(spec.split(":", 1)[1], **kwargs)
    if spec == "canalystii":
        from .canalystii import CanalystBackend
        return CanalystBackend(**kwargs)
    if spec == "panda":
        from .panda import PandaBackend
        return PandaBackend(**kwargs)
    raise ValueError(f"unknown backend spec: {spec}")
