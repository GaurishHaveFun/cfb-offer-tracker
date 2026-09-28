"""Parses recruit info out of a player's bio/location text.

Pure string-in, string-out functions — every field is "" when not found.
"""
from __future__ import annotations

import re

POSITIONS = [
    "QB", "RB", "WR", "TE", "OL", "OT", "OG", "IOL", "C", "DL", "DE", "DT",
    "EDGE", "LB", "CB", "S", "DB", "ATH", "K", "P",
]
# C, P, S and K are ambiguous on their own (center/pitcher, safety/shooting
# guard, kicker/thousand); only trust them standalone when a football word is
# nearby, and never split them out of a non-football combo like "C/PF".
AMBIGUOUS_POSITIONS = {"C", "P", "S", "K"}
UNAMBIGUOUS_POSITIONS = [p for p in POSITIONS if p not in AMBIGUOUS_POSITIONS]

# Longest-first so e.g. "IOL" isn't shadowed by a shorter token, and allow
# combos like "WR/DB".
_POS_ALT = "|".join(sorted(POSITIONS, key=len, reverse=True))
# A combo needs 2+ slash-separated tokens, each a recognized position (e.g.
# "OL/C", "K/P", "S/CB") - this is how ambiguous letters get accepted.
# Bios commonly write positions lowercase ("rb", "wr/db") so all three
# position regexes below are case-insensitive; that's safe for the
# unambiguous list (none of them double as ordinary lowercase English
# words), and the ambiguous letters stay gated behind a nearby "football"
# word regardless of case.
POSITION_RE = re.compile(rf"\b(?:{_POS_ALT})(?:/(?:{_POS_ALT}))+\b", re.IGNORECASE)
_UNAMBIG_ALT = "|".join(sorted(UNAMBIGUOUS_POSITIONS, key=len, reverse=True))
_SOLO_UNAMBIG_RE = re.compile(rf"\b(?:{_UNAMBIG_ALT})\b", re.IGNORECASE)
_AMBIG_ALT = "|".join(sorted(AMBIGUOUS_POSITIONS, key=len, reverse=True))
# Excludes any letter directly touching a "/" - that's a non-football combo
# (e.g. "PF" in "C/PF" isn't a recognized position, so the combo regex above
# never matched it, and a lone "C" shouldn't be pulled out of it either).
_SOLO_AMBIG_RE = re.compile(rf"(?<!/)\b(?:{_AMBIG_ALT})\b(?!/)", re.IGNORECASE)
_FOOTBALL_WORD_RE = re.compile(r"football", re.IGNORECASE)

# Bios paste in whichever apostrophe/quote character their phone's keyboard
# auto-curls to - straight ('), right single quote (’), or left single
# quote (‘, e.g. "C/O ‘28" where autocorrect opened a quote it
# never closed). All three are accepted here.
_APOS = "'‘’"
CLASS_YEAR_RE = re.compile(
    rf"c/?o\s*[{_APOS}]?(?P<full1>20(?:2[6-9]|30))|"
    # "c/o30" / "c/o 27" - a class-of abbreviation followed directly by the
    # short 2-digit year, no apostrophe and no full 4-digit year.
    rf"c/?o\s*(?P<short3>2[6-9]|30)\b|"
    rf"class\s+of\s*[{_APOS}]?(?:(?P<full2>20(?:2[6-9]|30))|(?P<short2>2[6-9]|30))|"
    rf"[{_APOS}](?P<short1>2[6-9]|30)\b|"
    # "28'" / "28’" - year first, apostrophe after, glued to the next token
    # ("5⭐️ 28’RIVALS", "/28’/WR"). A trailing space ("UCF 26' |") is left
    # alone: that's usually a college graduation year, not a recruit's class.
    rf"(?<![\d.])(?P<short4>2[6-9]|30)[{_APOS}](?=[A-Za-z/|])|"
    r"\b(?P<full3>20(?:2[6-9]|30))\b",
    re.IGNORECASE,
)

HEIGHT_RE = re.compile(r"\b([4-7])[\'’-](\d{1,2})\"?")
WEIGHT_RE = re.compile(r"\b(\d{2,3})\s?(?:lbs?\.?)\b", re.IGNORECASE)

HIGH_SCHOOL_RE = re.compile(
    r"([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*\s+(?:High\s+School|HS|Academy|Prep))",
)

STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY",
}
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


# A bare 4-digit year that's part of a date ("September 6, 2026",
# "9/6/2026", "Sept. 2026") is an event date, not a class year.
_DATE_BEFORE_YEAR_RE = re.compile(
    r"(?:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-])$",
    re.IGNORECASE,
)


def parse_class_year(text: str) -> str:
    text = text or ""
    m = None
    for cand in CLASS_YEAR_RE.finditer(text):
        if cand.group("full3") and _DATE_BEFORE_YEAR_RE.search(text[: cand.start()]):
            continue
        m = cand
        break
    if not m:
        return ""
    for key in ("full1", "full2", "full3"):
        if m.group(key):
            return m.group(key)
    for key in ("short1", "short2", "short3", "short4"):
        if m.group(key):
            return f"20{m.group(key)}"
    return ""


def parse_position(text: str) -> str:
    text = text or ""
    m = POSITION_RE.search(text)  # combo (2+ tokens), e.g. "OL/C", "K/P"
    if m:
        return m.group(0).upper()
    m = _SOLO_UNAMBIG_RE.search(text)
    if m:
        return m.group(0).upper()
    m = _SOLO_AMBIG_RE.search(text)
    if m and _FOOTBALL_WORD_RE.search(text):
        return m.group(0).upper()
    return ""


def parse_height(text: str) -> str:
    m = HEIGHT_RE.search(text or "")
    return f"{m.group(1)}'{m.group(2)}" if m else ""


def parse_weight(text: str) -> str:
    m = WEIGHT_RE.search(text or "")
    return f"{m.group(1)}lbs" if m else ""


def parse_high_school(text: str) -> str:
    m = HIGH_SCHOOL_RE.search(text or "")
    return m.group(1).strip() if m else ""


def parse_state(text: str) -> str:
    text = text or ""
    for name, abbrev in STATE_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            return abbrev
    for token in re.findall(r"\b[A-Z]{2}\b", text):
        if token in STATE_ABBREVS:
            return token
    return ""


def parse_bio(description: str, location: str = "") -> dict[str, str]:
    combined = f"{description or ''} {location or ''}"
    return {
        "class_year": parse_class_year(combined),
        "position": parse_position(combined),
        "height": parse_height(combined),
        "weight": parse_weight(combined),
        "high_school": parse_high_school(combined),
        "state": parse_state(combined),
    }
