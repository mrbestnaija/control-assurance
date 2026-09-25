"""Two controls, deliberately: one with an honest falsifier, one with a lazy one.

This is the example project shipped with control-assurance. Running `prove --all` against it produces a
PROVED and a NOT_PROVED in the same output, which is the point: a demo that only shows success teaches
nothing about what the tool measures.
"""


def enforce_max_position(size: float, cap: float) -> float:
    """Refuse a position above the cap. Covered by an HONEST falsifier."""
    if size > cap:
        raise ValueError(f"position {size} above cap {cap}")
    return size


def enforce_min_lot(size: float, floor: float) -> float:
    """Refuse a lot below the floor. Covered by a LAZY falsifier that notices nothing."""
    if size < floor:
        raise ValueError(f"lot {size} below floor {floor}")
    return size
