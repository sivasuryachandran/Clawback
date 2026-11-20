from fastapi import APIRouter

from backend.debug import dbaccess, sample

from . import recovery, stats

router = APIRouter(tags=["stats"])


@router.get("/stats")
def get_stats(mock: int = 0):
    """Dollars recovered + Done / Needs your OK / Stopped / No action counts (section 3.7).
    ?mock=1 answers from labelled sample data (development only)."""
    try:
        if mock:
            with dbaccess.use_override(sample.build().query):
                return dict(stats.compute(), sample=True)
        out = stats.compute()
        recovery.reconcile()          # report newly finished actions to analytics (throttled, never raises)
        return out
    except Exception as exc:
        return stats.unavailable(exc)
