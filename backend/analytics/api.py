from fastapi import APIRouter

from backend.debug import dbaccess, sample

from . import stats

router = APIRouter(tags=["stats"])


@router.get("/stats")
def get_stats(mock: int = 0):
    """Dollars recovered + Done / Needs your OK / Stopped / No action counts (section 3.7).
    ?mock=1 answers from labelled sample data (development only)."""
    try:
        if mock:
            with dbaccess.use_override(sample.build().query):
                return dict(stats.compute(), sample=True)
        return stats.compute()
    except Exception as exc:
        return stats.unavailable(exc)
