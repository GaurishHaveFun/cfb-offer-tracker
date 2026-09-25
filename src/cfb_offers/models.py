"""Row schema for the sheet/CSV output."""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class OfferRecord:
    """One row = one recruiting event (offer / commit / decommit)."""

    event_key: str
    event_type: str  # "offer" | "commit" | "decommit"
    is_flip: bool
    school: str
    player_name: str
    player_handle: str
    class_year: str
    position: str
    height: str
    weight: str
    high_school: str
    state: str
    source_type: str  # "player" | "reporter" | "coach"
    source_handle: str
    tweet_id: str
    tweet_date: str
    tweet_url: str
    tweet_text: str
    also_reported_by: str = ""
    notes: str = ""
    scraped_at: str = ""

    @staticmethod
    def columns() -> list[str]:
        return [f.name for f in fields(OfferRecord)]

    def as_row(self) -> list[str]:
        return [str(getattr(self, name)) for name in self.columns()]
