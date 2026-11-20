"""Re-exports so `from backend.watch import tick` and the debug panel's lookup of
`backend.watch.match` / `backend.watch.MATCH_SQL` both work (section 3.4 + C's debug
panel contract, docs/C_CONTRACTS.md section 10)."""

from backend.watch.matcher import MATCH_SQL, is_exact_match, match  # noqa: F401
from backend.watch.price_watch import on_price_change, tick  # noqa: F401
