"""Builds X advanced-search query strings.

Schools are packed several-per-query (aliases only) behind one shared
offer/commit/decommit phrase clause, greedily grouped so each query stays
under MAX_QUERY_LEN characters (X rejects or truncates long queries). This
keeps the 26-school config (originally 15, expanded later) to about 13
queries per run instead of 78 (26 schools x 3 event types), which is a
small handful compared to what pushed a single burner account past
twscrape's SearchTimeline rate limit in ~1 minute before this packing
existed.

Phrases are deduped against each other since X matches whole phrases:
"has received an offer" is already covered by "received an offer", and
"100% committed" is already covered by "committed". "decommit" and
"decommitted" are both kept because X does not prefix-match.

Because a query no longer maps to a single school or event type, main.py
must take the event type and school(s) for every tweet from classify.py
(alias/phrase matching against the tweet text), not from which query
found it.
"""
from __future__ import annotations

from datetime import date, timedelta

from cfb_offers.config import School

MAX_QUERY_LEN = 500

OFFER_PHRASES = [
    '"offer from"', '"blessed to receive"', '"received an offer"',
    '"excited to offer"', '"extended an offer"',
]
# The bare "committed" phrase matched way too much non-recruiting noise
# ("committed 19 errors", "committed to protecting families"); every commit
# phrase now requires "to" (or an unambiguous variant like #Committed).
COMMIT_PHRASES = [
    '"committed to"', '"has committed"', "#Committed", '"100% committed"',
    '"commits to"', '"commitment to"', '"flips to"', '"has flipped"',
]
# Trimmed to one "reopening ... recruitment" phrasing and dropped the vague
# "backs off" (also a query-length concession - see module docstring).
DECOMMIT_PHRASES = [
    '"decommit"', '"de-commit"', '"decommitted"', '"reopening my recruitment"',
]
ALL_PHRASES = OFFER_PHRASES + COMMIT_PHRASES + DECOMMIT_PHRASES

# Sports that share recruiting-post wording ("committed to", "offer from")
# with football but that we never want - added to every query's shared
# clause so X filters them out before they even reach classify.py.
NEGATIVE_SPORT_TERMS = [
    "-basketball", "-baseball", "-softball", "-volleyball", "-soccer",
    "-gymnastics",
]


def _since_date(since_days: int) -> str:
    return (date.today() - timedelta(days=since_days)).isoformat()


def _phrase_clause(phrases: list[str]) -> str:
    return f"({' OR '.join(phrases)})"


PHRASE_CLAUSE = _phrase_clause(ALL_PHRASES)


def _quote(term: str) -> str:
    # Unquoted multi-word terms are ANDed by X search, splitting the OR group.
    return f'"{term}"' if " " in term and not term.startswith('"') else term


def _alias_clause(schools: list[School]) -> str:
    aliases = " OR ".join(_quote(alias) for school in schools for alias in school.aliases)
    return f"({aliases})"


def _min_suffix(since_days: int) -> str:
    return f"-filter:retweets since:{_since_date(since_days)}"


def _base_query_for_group(schools: list[School], since_days: int) -> str:
    """Phrase clause + aliases + the minimal suffix (no negative-sport
    terms). Used to decide packing/grouping, and as the floor every query
    must fit under - negative terms are added on top of this only if they
    still fit (see _query_for_group)."""
    return f"{PHRASE_CLAUSE} {_alias_clause(schools)} {_min_suffix(since_days)}"


def _query_for_group(
    schools: list[School], since_days: int, max_len: int = MAX_QUERY_LEN
) -> str:
    """The base query, with as many NEGATIVE_SPORT_TERMS appended as still
    fit within max_len (there isn't always room for all of them once a
    group's aliases eat into the budget - see module docstring)."""
    prefix = f"{PHRASE_CLAUSE} {_alias_clause(schools)}"
    suffix = _min_suffix(since_days)
    fitted: list[str] = []
    for term in NEGATIVE_SPORT_TERMS:
        candidate = fitted + [term]
        trial = f"{prefix} {' '.join(candidate)} {suffix}"
        if len(trial) <= max_len:
            fitted = candidate
        else:
            break
    if fitted:
        return f"{prefix} {' '.join(fitted)} {suffix}"
    return f"{prefix} {suffix}"


def group_schools(
    schools: list[School], since_days: int, max_len: int = MAX_QUERY_LEN
) -> list[list[School]]:
    """Greedily packs schools into groups so each group's base query string
    (phrases + aliases + minimal suffix, before any negative-sport terms)
    stays within max_len characters. A school that alone would blow the
    limit still gets its own group rather than being dropped.
    """
    groups: list[list[School]] = []
    current: list[School] = []
    for school in schools:
        candidate = current + [school]
        if current and len(_base_query_for_group(candidate, since_days)) > max_len:
            groups.append(current)
            current = [school]
        else:
            current = candidate
    if current:
        groups.append(current)
    return groups


def build_all_queries(schools: list[School], since_days: int) -> list[str]:
    """Returns one search-query string per school group. The tighter phrase
    list (see COMMIT_PHRASES) means this runs to about 13 queries for the
    26-school config - still far below the 78 of one query per school x
    event-type. Each query combines every offer/commit/decommit phrase with
    several schools' aliases; the caller must classify each matched tweet's
    event type and school(s) from its text (see classify.py) rather than
    from which query found it.
    """
    return [_query_for_group(group, since_days) for group in group_schools(schools, since_days)]
