"""Pure regex classification: event type, school(s), flip details, noise filtering.

No I/O and no twscrape types here — everything takes plain strings/lists so
it's trivially testable against fixtures.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

from cfb_offers.config import School
from cfb_offers.profile import STATE_NAMES, parse_position

DECOMMIT_PATTERNS = [
    r"de-?commit(?:ted|s|ment)?",
    r"backs? off",
    r"reopen(?:ed|ing)\s+(?:his|my|her)\s+recruitment",
]

# Bare "committed" is too loose ("committed 19 errors", "committed assault") -
# every commit phrase requires an explicit "to" (or an unambiguous variant).
COMMIT_PATTERNS = [
    r"committed\s+to",
    r"has\s+committed",
    r"commits?\s+to",
    r"commitment\s+to",
    r"flips?\s+to",
    r"has\s+flipped",
    r"100%\s+committed",
    r"#committed\b",
]

# #AGTG alone is not an offer signal (it's used on all kinds of recruiting
# posts, including non-offer ones) - an actual offer phrase must be present.
OFFER_PATTERNS = [
    r"offer\s+from",
    r"blessed\s+to\s+receive",
    r"receiving\s+an\s+offer",
    r"received\s+an\s+offer",
    r"has\s+received\s+an\s+offer",
    r"excited\s+to\s+offer",
    r"extended\s+an\s+offer",
    r"new\s+offer",
    # Bare "<School> offered" - no "from"/"excited to"/etc alongside it, but
    # still an unambiguous first-person offer announcement. Not added to
    # HARD_OFFER_PATTERNS: is_invite_only() runs before event-type detection
    # (see classify_tweet), so a "Game Day Invite ... offered" post is still
    # correctly dropped as an invite regardless of this pattern.
    r"\boffered\b",
]

NOISE_PATTERNS = [
    r"should\s+offer",
    r"needs?\s+to\s+offer",
    r"would\s+love\s+to\s+see",
    r"\bprediction\b",
    r"crystal\s+ball",
    r"if\s+he\s+commits",
    # Hypothetical / third-person speculation about a future offer ("he might
    # be able to get an offer from a bigger brand", "hoping to get an offer").
    r"\b(?:might|could|would|will|may|should|hope|hopes|hoping|going|gonna|wants?)"
    r"(?:\s+(?:be\s+able|like))?\s+(?:to\s+)?(?:get|earn|land|pick\s+up)\s+an?\s+offer",
    # Reporter roundup / preview posts name multiple prospects but announce
    # nothing - never worth a row.
    r"visitor\s+list",
    r"commits?\s+to\s+keep\s+an\s+eye\s+on",
    r"following\s+the\s+future",
    r"game\s+preview",
    r"players?\s+to\s+watch",
    r"is\s+about\s+to\s+be",
]

# "Blessed to receive a Game Day Invite/camp invite/junior day invite/visit
# invite" is an INVITE, not an offer - generic offer wording like "blessed to
# receive" is reused for these too, so an invite phrase must be checked for
# and suppressed unless a genuine, unambiguous offer phrase is also present
# (HARD_OFFER_PATTERNS - a strict subset of OFFER_PATTERNS that never appears
# in invite wording).
INVITE_PATTERNS = [
    r"game\s*-?\s*day\s+(?:visit\s+)?invite",
    r"camp\s+invite",
    r"junior\s+day\s+invite",
    r"visit\s+invite",
    r"(?:un)?official\s+visit",
]
HARD_OFFER_PATTERNS = [
    r"offer\s+from",
    r"received\s+an\s+offer",
    r"has\s+received\s+an\s+offer",
    r"excited\s+to\s+offer",
    r"extended\s+an\s+offer",
    r"new\s+offer",
]

# "blessed to receive" also precedes awards, visits, etc. - an offer event
# needs the word itself (incl. the "🅾️ffer" emoji spelling).
OFFER_WORD_RE = re.compile(r"offer|\U0001F17E\ufe0f?\s?ffer", re.IGNORECASE)

FLIP_RE = re.compile(
    r"flips?\s+to\s+(?P<to>[^.,!\n]+?)(?:\s+from\s+(?P<from>[^.,!\n]+))?(?:[.,!\n]|$)",
    re.IGNORECASE,
)

# "Player X chose Y over A, B, C": only Y is the committing school. Without
# this, the "over" list gets scanned by match_schools too and any of our 15
# schools mentioned there (as a school the player passed on) would wrongly
# produce a row.
CHOSE_OVER_RE = re.compile(r"\bchose\s+(?P<to>.+?)\s+over\s+.+", re.IGNORECASE | re.DOTALL)

# --- school-match qualifier/suffix rejection list --------------------------
# A plain-text alias match is rejected when immediately preceded by one of
# these qualifiers ("West Florida", "North Texas", ...) or immediately
# followed by one of these suffixes ("Texas A&M", "Michigan State", "Notre
# Dame of Maryland", "Alabama A&M", "Texas A&M-Texarkana", ...). Kept here as
# a single list rather than scattered per-school special cases.
SCHOOL_REJECT_PREFIXES = [
    "West", "North", "South", "East",
    "Western", "Eastern", "Northern", "Southern", "Central", "Middle",
]
SCHOOL_REJECT_SUFFIXES = [
    "State", "A&M", "Tech", "Southern", "Christian", "Baptist", "of Maryland",
    "Texarkana", "San Antonio", "Rays",
    # Added for the 11-school expansion: "North Carolina A&T"/"...Central",
    # "UNC Charlotte"/"...Pembroke"/"...Wilmington", "Florida Atlantic"/
    # "...International", "Texas A&M-Commerce", "IU Indianapolis" all need
    # to reject the shorter tracked alias they share a prefix with, the same
    # way "Texas A&M" already rejects plain "Texas".
    "A&T", "Central", "Charlotte", "Pembroke", "Wilmington", "Atlantic",
    "International", "Commerce", "Indianapolis",
    # "Tennessee Valley" (a semi-pro/JUCO-level program), not Tennessee.
    "Valley",
]

# --- non-football sport signals ---------------------------------------------
# Sport-name words are matched case-insensitively; position abbreviations
# (RHP, INF, OF, ...) are ambiguous with common English words when lowercased
# ("of", "pg"), so they're matched case-sensitively, uppercase only.
OFF_SPORT_WORD_RE = re.compile(
    r"\b(basketball|hoops|baseball|softball|volleyball|gymnastics|soccer|"
    r"track|wrestling|lacrosse|golf|tennis|swimming)\b",
    re.IGNORECASE,
)
OFF_SPORT_HANDLE_RE = re.compile(
    r"@\w*(?:basketball|baseball|softball|volleyball|hoops|soccer|wbb|mbb)\w*", re.IGNORECASE
)
OFF_SPORT_ABBR_RE = re.compile(r"\b(?:WBB|MBB|RHP|LHP|INF|OF|PG|SG)\b|C/PF")

FOOTBALL_WORD_RE = re.compile(r"football", re.IGNORECASE)
FOOTBALL_EMOJI = "\U0001F3C8"  # 🏈


@dataclass(frozen=True)
class ClassifiedEvent:
    event_type: str  # offer | commit | decommit
    is_flip: bool
    school: str
    notes: str = ""


def _search_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_invite_only(text: str) -> bool:
    """True if `text` is a game-day/camp/junior-day/visit INVITE with no
    unambiguous offer phrase alongside it. Invite posts reuse generic offer
    wording ("blessed to receive a Game Day Invite...") that would otherwise
    match OFFER_PATTERNS, so this must be checked before event detection."""
    if not _search_any(INVITE_PATTERNS, text):
        return False
    return not _search_any(HARD_OFFER_PATTERNS, text)


def is_noise(text: str) -> bool:
    """Speculation / fan wording / roundup-preview posts that should never
    produce an event."""
    return _search_any(NOISE_PATTERNS, text) or is_invite_only(text)


def is_school_account(handle: str, schools: list[School]) -> bool:
    """True if `handle` is one of the schools' own official team accounts."""
    handle = handle.lstrip("@").lower()
    return any(handle == h.lstrip("@").lower() for s in schools for h in s.handles)


def detect_event_type(text: str) -> tuple[str | None, bool]:
    """Returns (event_type, is_flip). Checked in order: decommit, commit, offer."""
    if _search_any(DECOMMIT_PATTERNS, text):
        return "decommit", False
    if _search_any(COMMIT_PATTERNS, text):
        return "commit", bool(re.search(r"flip", text, re.IGNORECASE))
    if _search_any(OFFER_PATTERNS, text) and OFFER_WORD_RE.search(text):
        return "offer", False
    return None, False


def has_off_topic_sport(text: str) -> bool:
    """True if `text` clearly points to a non-football sport."""
    text = text or ""
    return bool(
        OFF_SPORT_WORD_RE.search(text)
        or OFF_SPORT_ABBR_RE.search(text)
        or OFF_SPORT_HANDLE_RE.search(text)
    )


def has_football_signal(text: str, bio: str, schools: list[School]) -> bool:
    """True if there's a positive football signal anywhere in the tweet text
    or the author's bio: a football position, the word "football", the 🏈
    emoji, or a tag of an official football/coach handle from config.
    """
    combined = f"{text or ''} {bio or ''}"
    if FOOTBALL_WORD_RE.search(combined) or FOOTBALL_EMOJI in combined:
        return True
    if parse_position(combined):
        return True
    mentions = {m.lower() for m in re.findall(r"@(\w+)", combined)}
    for school in schools:
        handles = {h.lstrip("@").lower() for h in (school.handles + school.coach_handles)}
        if mentions & handles:
            return True
    return False


def should_drop_for_sport(text: str, bio: str, schools: list[School]) -> bool:
    """The full football-only gate: reject on any non-football sport signal
    in the tweet or bio, and require a positive football signal too.

    A field (tweet text or bio) that names an off-topic sport is only
    disqualifying when that SAME field doesn't also explicitly say
    "football" - multi-sport HS athletes routinely list several sports in
    one bio ("Football (DL/OL) | Basketball | Track/Field"), and that must
    not be read the same as a bio that's purely about another sport (e.g.
    a bare "RHP | c/o 2027 | Anytown HS" baseball bio, which still has no
    football word anywhere and stays rejected)."""
    for field in (text, bio):
        if has_off_topic_sport(field) and not FOOTBALL_WORD_RE.search(field or ""):
            return True
    return not has_football_signal(text, bio, schools)


def _mask_handles_and_hashtags(text: str) -> str:
    """Blanks out every @handle and #hashtag token (same length, so offsets
    into the original text still line up) so a plain-text alias can't match
    a substring inside one (e.g. "Georgia" inside "@RecruitGeorgia")."""
    return re.sub(r"[@#]\w+", lambda m: " " * len(m.group(0)), text)


def _suffix_pattern(phrase: str) -> str:
    return r"\s+".join(re.escape(word) for word in phrase.split())


_PREFIX_ALT = "|".join(re.escape(p) for p in SCHOOL_REJECT_PREFIXES)
_PREFIX_RE = re.compile(rf"\b(?:{_PREFIX_ALT})[\s-]*$", re.IGNORECASE)
_SUFFIX_ALT = "|".join(_suffix_pattern(s) for s in SCHOOL_REJECT_SUFFIXES)
_SUFFIX_RE = re.compile(rf"^[\s-]*(?:{_SUFFIX_ALT})\b", re.IGNORECASE)


def _is_rejected_context(text: str, start: int, end: int) -> bool:
    if _PREFIX_RE.search(text[:start]):
        return True
    if _SUFFIX_RE.search(text[end:]):
        return True
    return False


_STATE_NAME_WORDS = set(STATE_NAMES.keys())
# A state-name alias (e.g. "Oregon", "Texas", "Georgia") directly preceded by
# "in" is a location ("... University in Oregon", "a QB in Texas"), not the
# school - see _is_location_context. Generalized (for the 11-school
# expansion) to also cover city names that are themselves school aliases:
# "Miami" is both the school and a hometown ("a QB from Miami", "Miami-area
# high school"), so it gets the same "in <word>" guard plus two more that
# don't apply to state names - a trailing "-area"/" area" and a trailing
# ", FL"/", Florida" (the common "<City>, <ST>" hometown format).
_IN_PREFIX_RE = re.compile(r"\bin[\s-]*$", re.IGNORECASE)
_CITY_LOCATION_WORDS = {"miami"}
_CITY_LOCATION_SUFFIX_RE = re.compile(
    r"^[\s-]*(?:area\b|,\s*(?:FL|Fla\.?|Florida)\b)", re.IGNORECASE
)

# Short/ambiguous aliases that collide with other well-known institutions -
# "USF" also means the University of San Francisco, "IU" is too generic on
# its own - only trusted as a plain-text match alongside explicit football
# context (the word "football" or the 🏈 emoji) in the same text. A mention
# of the school's own official handle always matches regardless (handled
# separately, before this alias loop runs).
_AMBIGUOUS_ALIASES = {"usf", "iu"}

# "USC" is ambiguous with the University of South Carolina (Gamecocks) -
# reject that one alias (not "Trojans") when the tweet also names South
# Carolina context anywhere, not just immediately adjacent.
_USC_SOUTH_CAROLINA_CONTEXT_RE = re.compile(
    r"south\s+carolina|gamecocks|upstate|usc\s+aiken|usc\s+beaufort",
    re.IGNORECASE,
)

# "Miami" is ambiguous with Miami University (Ohio, "RedHawks") - reject
# that one alias when the tweet also names Ohio/RedHawks/"Miami University"
# context anywhere.
_MIAMI_OHIO_CONTEXT_RE = re.compile(
    r"miami\s*\(\s*oh\s*\)|miami,?\s+oh\b|miami\s+of\s+ohio|miami\s+university|redhawks",
    re.IGNORECASE,
)

# The offer is "from @handle" - if that handle isn't one of our configured
# official football/coach handles, a hashtag alias must not be trusted to
# assign a school (a hashtag like #RollTide or #AllIn gets reused far beyond
# the school it nominally belongs to, e.g. on a women's-basketball offer or
# an unrelated small college's offer that happens to share the hashtag).
_FROM_HANDLE_RE = re.compile(r"\bfrom\s+@(\w+)", re.IGNORECASE)


def _from_handle_is_unofficial(text: str, schools: list[School]) -> bool:
    m = _FROM_HANDLE_RE.search(text)
    if not m:
        return False
    handle = m.group(1).lower()
    official = {
        h.lstrip("@").lower() for s in schools for h in (s.handles + s.coach_handles)
    }
    return handle not in official


def _is_location_context(alias: str, text: str, start: int, end: int) -> bool:
    """True if `alias` (already matched at text[start:end]) reads as a
    location rather than the school - a state name preceded by "in" ("...
    University in Oregon"), or a tracked city name (currently just "Miami")
    preceded by "in" or followed by "-area"/" area" or ", FL"/", Florida"
    (the "<hometown>, <ST>" bio format)."""
    lower = alias.lower()
    if lower in _STATE_NAME_WORDS and _IN_PREFIX_RE.search(text[:start]):
        return True
    if lower in _CITY_LOCATION_WORDS:
        if _IN_PREFIX_RE.search(text[:start]):
            return True
        if _CITY_LOCATION_SUFFIX_RE.search(text[end:]):
            return True
    return False


def _plain_alias_matches(alias: str, text: str, masked: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
    for m in pattern.finditer(masked):
        if _is_rejected_context(text, m.start(), m.end()):
            continue
        if _is_location_context(alias, text, m.start(), m.end()):
            continue
        return True
    return False


def match_schools(text: str, schools: list[School]) -> list[str]:
    """Names of every school matched in `text`, in schools-list order.

    - @mentions count only for a school whose configured handles/coach_handles
      contain that exact (case-insensitive) handle.
    - hashtag aliases (e.g. "#RollTide") match literally, since they're
      explicitly listed as aliases - unless the tweet explicitly names its
      offering source as "from @some-unofficial-handle", in which case a
      hashtag alone is too weak to trust (see _from_handle_is_unofficial).
    - every other (plain-text) alias must match as a whole word, outside any
      @handle/#hashtag token, not in a rejected qualifier/suffix context (see
      SCHOOL_REJECT_PREFIXES/SCHOOL_REJECT_SUFFIXES), and not a state name
      used as a location ("... University in Oregon").
    """
    text = html.unescape(text or "")
    masked = _mask_handles_and_hashtags(text)
    mentions = {m.lower() for m in re.findall(r"@(\w+)", text)}
    suppress_hashtag = _from_handle_is_unofficial(text, schools)

    matched = []
    for school in schools:
        handles = {h.lstrip("@").lower() for h in (school.handles + school.coach_handles)}
        if mentions & handles:
            matched.append(school.name)
            continue

        hit = False
        if not suppress_hashtag:
            for alias in school.aliases:
                if alias.startswith("#"):
                    if re.search(rf"(?<!\w){re.escape(alias)}\b", text, re.IGNORECASE):
                        hit = True
                        break
        if hit:
            matched.append(school.name)
            continue

        for alias in school.aliases:
            if alias.startswith("#") or alias.startswith("@"):
                continue
            alias_lower = alias.lower()
            if alias_lower == "usc" and _USC_SOUTH_CAROLINA_CONTEXT_RE.search(text):
                continue
            if alias_lower == "miami" and _MIAMI_OHIO_CONTEXT_RE.search(text):
                continue
            if alias_lower in _AMBIGUOUS_ALIASES and not (
                FOOTBALL_WORD_RE.search(text) or FOOTBALL_EMOJI in text
            ):
                continue
            if _plain_alias_matches(alias, text, masked):
                hit = True
                break
        if hit:
            matched.append(school.name)

    return matched


# "committed to X" / "commits to X" / "my commitment to X": X is what was
# committed to - which may not be a school at all ("commits to the Navy
# All-American Bowl", "commitment to a clear RB1", "commits to the Wildcats").
DIRECTED_COMMIT_RE = re.compile(r"\b(?:committed|commits?|commitment|committing)\s+to\b", re.IGNORECASE)
COMMIT_WINDOW_AFTER = 120


def _commit_school_text(text: str) -> str:
    """The part of the tweet that names the committing school.

    For "chose X over A, B, C" wording, only X counts - the "over" list must
    not be scanned. For "committed to X" wording, only the text right after
    the phrase counts. Otherwise (#Committed, "100% committed") the whole
    tweet is used."""
    m = CHOSE_OVER_RE.search(text)
    if m:
        return m.group("to")
    windows = [text[m.end(): m.end() + COMMIT_WINDOW_AFTER] for m in DIRECTED_COMMIT_RE.finditer(text)]
    return "\n".join(windows) if windows else text


def flip_schools(text: str, schools: list[School]) -> tuple[str | None, str | None]:
    """For 'flips to X from Y' wording: returns (to_school, from_school)."""
    m = FLIP_RE.search(text)
    if not m:
        return None, None
    to_school = match_schools(m.group("to"), schools)
    from_school = match_schools(m.group("from") or "", schools)
    return (
        to_school[0] if to_school else None,
        from_school[0] if from_school else None,
    )


# How far around an offer phrase a school may be named and still count as
# the offering school: "Alabama offered" (school before) and "blessed to
# receive my 18th offer from The University Of Tennessee" (school after).
OFFER_WINDOW_BEFORE = 40
OFFER_WINDOW_AFTER = 90
HAVE_OFFERED_BEFORE_RE = re.compile(r"\bha(?:ve|s)\s+(?:also\s+|already\s+)?$", re.IGNORECASE)
OFFER_LIST_RE = re.compile(
    r"\b(?:also\s+)?holds?\s+(?:\w+\s+)?offers?|other\s+offers|offers\s+(?:from|include)"
    # a following sentence that lists schools which "have offered" already
    r"|[.!?]\s+(?=[^.!?\n]*\bha(?:ve|s)\s+(?:also\s+|already\s+)?offered)",
    re.IGNORECASE,
)


def offer_windows(text: str) -> str:
    """The text surrounding every offer-phrase match, joined."""
    spans = []
    for p in OFFER_PATTERNS:
        for m in re.finditer(p, text, re.IGNORECASE):
            # "Auburn, Georgia, Miami and others have offered" lists offers the
            # player already holds - not the news in this tweet.
            if HAVE_OFFERED_BEFORE_RE.search(text[max(0, m.start() - 20): m.start()]):
                continue
            end = m.end() + OFFER_WINDOW_AFTER
            # "...reports an offer from Washington. Also holds offers from
            # Oregon, ..." - schools in the existing-offers list aren't new.
            # Searched on the rest of the tweet, not just the window, since a
            # "... have offered." list can end well past the window.
            cut = OFFER_LIST_RE.search(text, m.end())
            if cut and cut.start() < end:
                end = cut.start()
            after = text[m.end(): end]
            spans.append(text[max(0, m.start() - OFFER_WINDOW_BEFORE): m.end()] + after)
    return "\n".join(spans)


def _school_named_in(school_name: str, window: str, schools: list[School]) -> bool:
    """True if any alias or handle of `school_name` appears in `window`.
    match_schools() already decided the school is genuinely named in the
    tweet (with all its look-alike/context rules); this only checks where."""
    school = next(s for s in schools if s.name == school_name)
    terms = [*school.aliases, *school.handles]
    low = window.lower()
    return any(t.lstrip("@").lower() in low for t in terms if t)


def classify_tweet(text: str, schools: list[School], bio: str = "") -> list[ClassifiedEvent]:
    """The full pipeline for one tweet's text: noise -> sport -> event type ->
    school(s). `bio` is optional (defaults to "") so callers that only have
    tweet text (and existing tests) still work.
    """
    text = html.unescape(text or "")

    if is_noise(text):
        return []

    if should_drop_for_sport(text, bio, schools):
        return []

    event_type, is_flip = detect_event_type(text)
    if event_type is None:
        return []

    if event_type == "offer":
        # A tweet naming several schools gets one row per school - but only
        # schools named near the offer wording itself, so "a job at LSU ...
        # an offer from a bigger brand" doesn't credit LSU with an offer.
        near = offer_windows(text)
        return [
            ClassifiedEvent("offer", False, school)
            for school in match_schools(text, schools)
            if _school_named_in(school, near, schools)
        ]

    if event_type == "commit":
        if is_flip:
            to_school, from_school = flip_schools(text, schools)
            if to_school is None:
                matched = match_schools(_commit_school_text(text), schools)
                to_school = matched[0] if matched else None
            if to_school is None:
                return []
            notes = f"flipped from {from_school}" if from_school else "flip"
            return [ClassifiedEvent("commit", True, to_school, notes)]
        matched = match_schools(_commit_school_text(text), schools)
        if not matched:
            return []
        return [ClassifiedEvent("commit", False, matched[0])]

    if event_type == "decommit":
        matched = match_schools(text, schools)
        if not matched:
            return []
        return [ClassifiedEvent("decommit", False, matched[0])]

    return []
