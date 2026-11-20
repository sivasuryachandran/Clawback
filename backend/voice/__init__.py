"""Teammate C: the orb's backend (POST /voice/ask)."""
from .intents import ask  # noqa: F401  (section 3.4 contract: ask(text) -> {intent, answer, open_card_id})
