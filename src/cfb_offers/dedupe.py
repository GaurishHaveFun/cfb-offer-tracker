"""Collapses multiple tweets about the same recruiting event into one row.

Pure functions operating on OfferRecord / plain strings — no I/O.
"""
from __future__ import annotations

import re
from dataclasses import replace

from cfb_offers.models import OfferRecord


def normalize_name(name: str) -> str:
    # Drop a quoted nickname so 'Kavarris "Duke" Duncan' == 'Kavarris Duncan'.
    name = re.sub(r'["\u201c][^"\u201c\u201d]*["\u201d]', " ", name or "")
    return re.sub(r"\s+", " ", name.strip().lower())


def make_event_key(player_handle: str, player_name: str, school: str, event_type: str) -> str:
    """`event_key = player_handle (or normalized name) + school + event_type`.

    A later decommit or re-offer is a different event_type, so it gets its
    own key (and own row) rather than merging into an earlier commit/offer.
    """
    identity = (player_handle or "").lstrip("@").lower() or normalize_name(player_name)
    return f"{identity}|{school.lower()}|{event_type}"


def add_source(also_reported_by: str, new_handle: str) -> str:
    """Appends `new_handle` to an also_reported_by list, deduped, order-preserving."""
    if not new_handle:
        return also_reported_by
    existing = [h.strip() for h in also_reported_by.split(",") if h.strip()]
    if new_handle not in existing:
        existing.append(new_handle)
    return ", ".join(existing)


def dedupe_events(records: list[OfferRecord]) -> list[OfferRecord]:
    """One row per event_key: the earliest tweet is kept as the row, and every
    other source's handle is folded into `also_reported_by`.
    """
    groups: dict[str, list[OfferRecord]] = {}
    for r in records:
        groups.setdefault(r.event_key, []).append(r)

    result = []
    for group in groups.values():
        group = sorted(group, key=lambda r: (r.tweet_date, r.tweet_id))
        main = group[0]
        also = main.also_reported_by
        for other in group[1:]:
            if other.source_handle and other.source_handle != main.source_handle:
                also = add_source(also, other.source_handle)
        result.append(replace(main, also_reported_by=also) if also != main.also_reported_by else main)
    return result
