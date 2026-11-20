"""The one seam between B's code and teammates' modules.

Tomorrow the real modules exist and are imported first; tonight the rehearsal stand-ins in
dev/stubs/ are used. Deleting dev/stubs/ needs no change here once the real modules land.

Assumption to confirm with Siva: backend/pact/__init__.py re-exports the 3.4 functions
(active_pact, build_context, run_gate, run_approved, execute_once, record, proposal_hash)
and AlreadyRan.
"""
try:
    from backend import db  # type: ignore[attr-defined]  # A
except ImportError:
    from dev.stubs import db  # noqa: F401

# Siva's real gate reads the real database (A's backend/db.py, or Siva's devkit stand-in), so it is
# only used when backend.db exists. Until then the stub gate pairs with the stub db (consistent pair).
_REAL_DB = db.__name__ == "backend.db"
try:
    if not _REAL_DB:
        raise ImportError("real pact needs the real db")
    import backend.pact as pact  # type: ignore  # Siva
    pact.run_gate  # noqa: B018  (fail over if the package exists but is empty)
    pact.active_pact, pact.build_context, pact.record  # noqa: B018
except (ImportError, AttributeError):
    from dev.stubs import pact  # noqa: F401

try:
    from backend.analytics.events import track  # type: ignore  # C
except ImportError:
    from dev.stubs.analytics import track  # noqa: F401
