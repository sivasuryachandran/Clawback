"""Teammate C: read-only debug data for the Under the hood page."""


def overview() -> dict:
    """Section 3.4 contract: GET /debug/overview, read-only. (Lazy import keeps `import backend.debug` light.)"""
    from .panels import build
    return build()
