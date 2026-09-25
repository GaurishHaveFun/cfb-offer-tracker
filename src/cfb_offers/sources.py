"""Author classification (player / reporter / coach) and player-mention resolution.

Pure functions: takes plain strings (bio, tweet text, handle, mentions) and
`School` config, returns plain values. No twscrape types, no network calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from cfb_offers.config import School
from cfb_offers.profile import parse_class_year, parse_position

REPORTER_BIO_RE = re.compile(
    r"247|on3|rivals|espn|recruiting\s+(?:analyst|reporter|insider|director)|covers\s+.*recruiting",
    re.IGNORECASE,
)

# A strict subset of REPORTER_BIO_RE: bios that unambiguously self-identify
# as an outlet/analyst, as opposed to a bare outlet-name mention that can
# just as easily be a player's own accolade ("ESPN Top 300", "247 4-star").
# Used to gate the first-person override below - a bare mention doesn't
# block it, an explicit "I'm a ... reporter/analyst" bio does.
STRONG_OUTLET_BIO_RE = re.compile(
    r"recruiting\s+(?:analyst|reporter|insider|director)|covers\s+.*recruiting|"
    r"(?:writer|reporter|analyst|insider)\s+(?:for|at)\s+(?:247|on3|rivals|espn)",
    re.IGNORECASE,
)

# First-person offer wording ("I am blessed", "blessed to receive", "my
# offer") means the tweet's author is the player announcing their own offer,
# whatever a stray outlet-name word in their bio might suggest - unless the
# bio is unambiguously an outlet's (see STRONG_OUTLET_BIO_RE).
FIRST_PERSON_OFFER_RE = re.compile(
    r"\bi\s*(?:'m|\s+am)\b(?:[^.!\n]{0,25})\bblessed\b|\bblessed\s+to\s+receive\b|\bmy\s+offer\b",
    re.IGNORECASE,
)

# An @mention immediately preceded by "Coach"/"Coaches" is a coach, never
# the recruit being written about - excluded from player-mention resolution
# even when the school's coach_handles config doesn't happen to list it.
COACH_MENTION_RE = re.compile(r"\bcoach(?:es)?\s*[:,]?\s*@(\w+)", re.IGNORECASE)

COACH_BIO_RE = re.compile(
    r"\bcoach\b|coordinator|\bOC\b|\bDC\b|recruiting\s+director|assistant",
    re.IGNORECASE,
)

# Bios that read as an organization / media outlet / politician / business,
# not a person - these get dropped outright (they're not reporters, and
# their stray "assistant"/"recruiting" wording shouldn't be read as coach or
# player either) unless the reporter rule above already claimed them.
ORG_BIO_RE = re.compile(
    r"\b(governor|senator|mayor|congress(?:man|woman)?|state\s+representative|"
    r"political|politician|campaign|marketing\s+agency|marketing|"
    r"foundation|nonprofit|non-profit|charity|department\s+of|"
    r"state\s+government|city\s+of|county\s+government|official\s+page|"
    r"official\s+account|LLC|Inc\.?|corporation|real\s+estate|law\s+firm|"
    r"attorney|insurance|care\s+home|hospital|clinic|church|ministry|"
    r"news\s+network|magazine|newspaper|radio\s+station|consumer\s+hotline)\b",
    re.IGNORECASE,
)

# A class year alone ("c/o 2027", "Class of '27", "2027") or a football
# position plus some recruit context ("DL | Norcross HS") - a bare state
# code, "HS" alone, or a location alone is not enough.
RECRUIT_CONTEXT_RE = re.compile(
    r"high\s+school|\bHS\b|\bprep\b|academy|class\s+of|c/?o\s*['‘’]?\d{2}|"
    r"committed|recruit|offer",
    re.IGNORECASE,
)

# A recruiting-reporter idiom ("...he tells me for @Outlet", "...she tells us
# for @Outlet") that's unambiguous enough to trust even when the bio itself
# gives no reporter/outlet signal (e.g. a terse joke bio) - used as a last
# resort below, after every bio-based check has come up empty.
TEXT_REPORTER_RE = re.compile(r"\btells\s+(?:me|us)\s+for\s+@\w+", re.IGNORECASE)


def classify_author(bio: str, schools: list[School] | None = None, text: str = "") -> str | None:
    """Returns 'reporter', 'coach', 'player', or None (drop: fan/parody/org/unknown).

    `text` (the tweet itself, optional) lets a bare outlet-name word in the
    bio ("ESPN Top 300") get overridden by first-person offer wording in the
    tweet - see FIRST_PERSON_OFFER_RE/STRONG_OUTLET_BIO_RE.
    """
    schools = schools or []
    bio = bio or ""
    text = text or ""
    is_reporter_bio = bool(REPORTER_BIO_RE.search(bio))
    overridable = (
        is_reporter_bio
        and bool(FIRST_PERSON_OFFER_RE.search(text))
        and not STRONG_OUTLET_BIO_RE.search(bio)
    )
    if is_reporter_bio and not overridable:
        return "reporter"
    if ORG_BIO_RE.search(bio):
        return None
    if COACH_BIO_RE.search(bio) and _mentions_a_school(bio, schools):
        return "coach"
    year = parse_class_year(bio)
    if year and 2026 <= int(year) <= 2030:
        return "player"
    if parse_position(bio) and RECRUIT_CONTEXT_RE.search(bio):
        return "player"
    if is_reporter_bio:
        # Overridable, but no player signal in the bio to confirm it - fall
        # back to the original reporter classification rather than dropping.
        return "reporter"
    if TEXT_REPORTER_RE.search(text):
        return "reporter"
    return None


def is_coach_handle(handle: str, schools: list[School]) -> bool:
    handle = handle.lstrip("@").lower()
    return any(handle == h.lstrip("@").lower() for s in schools for h in s.coach_handles)


def _mentions_a_school(text: str, schools: list[School]) -> bool:
    if not schools:
        return True  # no config given (unit tests) — don't block on this
    for school in schools:
        for alias in school.aliases:
            if re.search(re.escape(alias), text, re.IGNORECASE):
                return True
    return False


@dataclass(frozen=True)
class PlayerMention:
    handle: str  # "" if unresolved (name-only)
    name: str  # "" if unresolved (handle-only)


NAME_BEFORE_VERB_RE = re.compile(
    r"(?:^|[.•\U0001F6A8\s])"  # start, bullet, or emoji/space boundary
    r"(?:20\d{2}\s+)?"  # optional leading class year
    r"(?:[A-Z]{1,4}/?[A-Z]{0,4}\s+)?"  # optional position(s)
    r"(?P<name>[A-Z][a-zA-Z'.-]+\s+[A-Z][a-zA-Z'.-]+)\s+"
    r"(?:has\s+)?(?:committed|commits|decommitted|de-commits?|flips?|has\s+received|received)"
)


def resolve_player_mention(
    text: str,
    mentions: list[str],
    exclude_handles: list[str],
) -> PlayerMention:
    """For a reporter/coach tweet: the player is the @mention that isn't a
    school/outlet/coach account. Falls back to parsing the name out of the
    tweet text (e.g. "2027 QB John Smith has committed to...") with a blank
    handle when no such mention exists.
    """
    excluded = {h.lstrip("@").lower() for h in exclude_handles}
    excluded |= {h.lower() for h in COACH_MENTION_RE.findall(text)}
    for mention in mentions:
        if mention.lstrip("@").lower() not in excluded:
            return PlayerMention(handle=mention.lstrip("@"), name="")

    m = NAME_BEFORE_VERB_RE.search(text)
    if m:
        return PlayerMention(handle="", name=m.group("name").strip())

    return PlayerMention(handle="", name="")
