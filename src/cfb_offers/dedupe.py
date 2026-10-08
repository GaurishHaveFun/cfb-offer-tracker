"""Collapses multiple tweets about the same recruiting event into one row.

Pure functions operating on OfferRecord / plain strings — no I/O.
"""
from __future__ import annotations

import re
from dataclasses import replace

from cfb_offers.models import OfferRecord, VisitRecord


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


def add_completed_note(notes: str, tweet_url: str) -> str:
    """Records the thank-you post that turned an upcoming visit completed."""
    note = f"completed: {tweet_url}"
    if note in notes:
        return notes
    return f"{notes}; {note}" if notes else note


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Names shorter than this (letters only) are too generic to match a handle.
_MIN_NAME_LETTERS = 6


def canonical_key(key: str, known_keys) -> str:
    """Maps a name-based key ("braylen bedford|ole miss|commit", from a
    reporter post with no @handle) onto a handle-based key for the same
    school and event whose handle spells out the name
    ("braylen_bedford|ole miss|commit", from the player's own post), so both
    reports are one event. Handle-based keys are returned unchanged."""
    identity, _, rest = key.partition("|")
    if " " not in identity:  # handles never contain spaces
        return key
    name = _alnum(identity)
    if len(name) < _MIN_NAME_LETTERS:
        return key
    for other in known_keys:
        other_identity, _, other_rest = other.partition("|")
        if other_rest == rest and " " not in other_identity and name in _alnum(other_identity):
            return other
    return key


def canonicalize(records: list[OfferRecord]) -> list[OfferRecord]:
    handle_keys = {r.event_key for r in records if " " not in r.event_key.partition("|")[0]}
    out = []
    for r in records:
        key = canonical_key(r.event_key, handle_keys)
        out.append(r if key == r.event_key else replace(r, event_key=key))
    return out


def dedupe_events(records: list[OfferRecord]) -> list[OfferRecord]:
    """One row per event: the earliest tweet is kept as the row, and every
    other source's handle is folded into `also_reported_by`. A reporter's
    name-only report and the player's own post merge (see canonical_key).
    An upcoming visit with a later completed post turns completed, with
    that post's URL in `notes`.
    """
    groups: dict[str, list[OfferRecord]] = {}
    for r in canonicalize(records):
        groups.setdefault(r.event_key, []).append(r)

    result = []
    for group in groups.values():
        group = sorted(group, key=lambda r: (r.tweet_date, r.tweet_id))
        main = group[0]
        also = main.also_reported_by
        for other in group[1:]:
            if other.source_handle and other.source_handle != main.source_handle:
                also = add_source(also, other.source_handle)
        if also != main.also_reported_by:
            main = replace(main, also_reported_by=also)
        if isinstance(main, VisitRecord) and main.visit_status == "upcoming":
            done = next((r for r in group if r.visit_status == "completed"), None)
            if done is not None:
                main = replace(
                    main, visit_status="completed", notes=add_completed_note(main.notes, done.tweet_url)
                )
        result.append(main)
    return result
