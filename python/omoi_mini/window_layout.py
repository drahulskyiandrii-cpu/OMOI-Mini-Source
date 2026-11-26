"""Pure geometry for an optional dock, in Qt logical screen coordinates."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def dock_geometry(current: Rect, available: Rect, opened: bool, compact_width: int) -> Rect:
    """Preserve compact width on close; clamp to the current monitor work area."""
    width = min(available.width, max(1, compact_width + (450 if opened else 0)))
    height = min(available.height, max(1, current.height))
    x = min(max(current.x, available.x), available.x + available.width - width)
    y = min(max(current.y, available.y), available.y + available.height - height)
    return Rect(x, y, width, height)
