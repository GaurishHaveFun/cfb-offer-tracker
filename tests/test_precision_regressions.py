"""Regression tests for the precision pass (school matching, football-only
filtering, tighter phrases, player detection, profile parsing, reporter
announcement rules).

Every fixture here is made up and fully anonymized - none of it is copied
from sample.csv (a public repo, many recruits are minors). Handles/names
follow the pattern @Recruit2027QB / "Recruit Name".
"""
import asyncio

from cfb_offers.classify import classify_tweet, match_schools
from cfb_offers.main import process_tweet
from cfb_offers.profile import parse_class_year, parse_position
from cfb_offers.sources import classify_author


# ---------------------------------------------------------------------------
# 1. School matching: qualifiers, suffixes, HTML entities, mention/hashtag
#    scoping.
# ---------------------------------------------------------------------------

def test_west_qualifier_rejects_florida(schools):
    text = "Blessed to receive an offer from the University of West Florida! 🏈"
    assert match_schools(text, schools) == []


def test_state_suffix_rejects_alabama_and_michigan(schools):
    assert match_schools("Offered by Alabama State University 🏈", schools) == []
    assert match_schools("Cartae commits to Michigan State 🏈", schools) == []


def test_am_suffix_rejects_texas_but_matches_texas_am(schools):
    text = "Blessed to receive an offer from Texas A&M University! #aggies 🏈"
    assert match_schools(text, schools) == ["Texas A&M"]
    assert "Texas" not in match_schools(text, schools)


def test_html_entity_decoded_before_matching(schools):
    text = "Excited to receive an offer from Texas A&amp;M! 🏈"
    assert match_schools(text, schools) == ["Texas A&M"]


def test_notre_dame_of_maryland_rejected(schools):
    text = "Blessed to receive an offer from Notre Dame of Maryland University! 🏈"
    assert match_schools(text, schools) == []


def test_texarkana_and_san_antonio_suffixes_rejected(schools):
    assert match_schools("Committed to Texas A&M-Texarkana 🏈", schools) == []
    assert match_schools("Committed to Texas A&M San Antonio 🏈", schools) == []


def test_lookalike_handle_does_not_count_as_school_mention(schools):
    # "Georgia" only appears inside an unrelated @handle - must not match.
    text = (
        "Blessed to receive an offer from Ohio State! 🏈 Go Buckeyes! "
        "Thanks to @RecruitGeorgiaFake and @ChadFakeReporter for the love."
    )
    assert match_schools(text, schools) == ["Ohio State"]


def test_plain_alias_does_not_match_inside_hashtag(schools):
    text = "Just talking #GeorgiaPeaches and sweet tea today, nothing recruiting related. 🏈"
    assert match_schools(text, schools) == []


def test_hashtag_alias_still_matches_literally(schools):
    text = "Committed!! #RollTide 🏈"
    assert match_schools(text, schools) == ["Alabama"]


def test_mention_counts_only_for_configured_handle(schools):
    # @AlabamaWBBFake contains "Alabama" but isn't Alabama's configured
    # football handle/coach handle, so it must not resolve to Alabama.
    text = "Thank you @AlabamaWBBFake for the shoutout, appreciate the support! 🏈"
    assert match_schools(text, schools) == []


def test_official_handle_mention_matches_even_without_plain_text_alias(schools):
    text = "So blessed to receive this offer! Thank you coach! 🏈 @AlabamaFTBL"
    assert match_schools(text, schools) == ["Alabama"]


# ---------------------------------------------------------------------------
# 2. Football-only: non-football sport signals reject, football signal
#    required.
# ---------------------------------------------------------------------------

def test_basketball_position_rejects_offer(schools):
    text = "Blessed to receive an offer from Alabama! PG | c/o 2027"
    assert classify_tweet(text, schools) == []


def test_volleyball_word_rejects_commit(schools):
    text = "Committed to continue my volleyball career at Georgia! 🏐"
    assert classify_tweet(text, schools) == []


def test_baseball_bio_rejects_even_with_clean_tweet_text(schools):
    text = "Excited to receive an offer from LSU!"
    bio = "RHP | c/o 2027 | Anytown HS"
    assert classify_tweet(text, schools, bio=bio) == []


def test_gymnastics_word_rejects_commit(schools):
    text = "Verbally committed to Clemson gymnastics! So blessed."
    assert classify_tweet(text, schools) == []


def test_no_football_signal_at_all_is_dropped(schools):
    # A school name plus "committed to" wording with no sport signal either
    # way (generic civic post) - must not produce a row.
    text = "Our city is committed to improving roads near the Georgia line."
    assert classify_tweet(text, schools) == []


def test_football_word_alone_is_enough_signal(schools):
    text = "Blessed to receive an offer from Oregon! Excited to keep playing football. @oregonfootball"
    ev = classify_tweet(text, schools)
    assert len(ev) == 1
    assert ev[0].school == "Oregon"


# ---------------------------------------------------------------------------
# 3. Tighter phrases: #AGTG alone, bare "committed".
# ---------------------------------------------------------------------------

def test_agtg_alone_is_not_an_offer(schools):
    text = "So blessed right now! #AGTG 🏈"
    assert classify_tweet(text, schools) == []


def test_bare_committed_without_to_is_not_a_commit(schools):
    text = "He committed 3 turnovers in the game against Georgia. Rough night."
    assert classify_tweet(text, schools) == []


def test_committed_to_phrase_still_works(schools):
    text = "100% committed to Georgia! Go Dawgs! 🏈 @GeorgiaFootball"
    ev = classify_tweet(text, schools)
    assert len(ev) == 1
    assert ev[0].event_type == "commit"
    assert ev[0].school == "Georgia"


# ---------------------------------------------------------------------------
# 4. Player detection: class-year range, position+context, org/political
#    bios dropped.
# ---------------------------------------------------------------------------

def test_state_code_alone_is_not_a_player_bio(schools):
    assert classify_author("TX | HS", schools) is None


def test_class_year_out_of_range_without_position_is_not_a_player(schools):
    assert classify_author("Class of 2035, marketing intern", schools) is None


def test_class_year_in_range_is_a_player(schools):
    assert classify_author("c/o 2028 | Anytown HS", schools) == "player"


def test_position_with_recruit_context_is_a_player_even_outside_year_range(schools):
    assert classify_author("DL | Class of '25 | Norcross High School", schools) == "player"


def test_politician_bio_is_dropped(schools):
    assert classify_author("State Senator fighting for working families", schools) is None


def test_business_bio_is_dropped(schools):
    assert classify_author("Family-owned marketing agency serving the tri-state area", schools) is None


# ---------------------------------------------------------------------------
# 5. profile.py: class-year range, ambiguous single-letter positions.
# ---------------------------------------------------------------------------

def test_class_year_outside_2026_2030_is_blank():
    assert parse_class_year("Class of 2035") == ""
    assert parse_class_year("c/o 2025") == ""


def test_class_year_inside_2026_2030_parses():
    assert parse_class_year("c/o 2029") == "2029"
    assert parse_class_year("Class of 2030") == "2030"


def test_ambiguous_c_not_pulled_out_of_non_football_combo():
    assert parse_position('6\'5" C/PF | Anytown HS') == ""


def test_ambiguous_positions_accepted_as_football_combo():
    assert parse_position("K/P | c/o 2027") == "K/P"
    assert parse_position("S/CB | c/o 2027") == "S/CB"


def test_ambiguous_p_not_parsed_from_pitcher_context():
    assert parse_position("P | Varsity Baseball pitcher") == ""


def test_ambiguous_solo_position_accepted_with_football_word_nearby():
    assert parse_position("S | football safety, c/o 2027") == "S"


# ---------------------------------------------------------------------------
# 6. Reporter posts: roundup/preview drop, "chose X over" restriction,
#    player-must-be-resolved.
# ---------------------------------------------------------------------------

def test_visitor_list_roundup_is_dropped(schools):
    text = "Checking out the visitor list for this weekend's game between Georgia and Clemson."
    assert classify_tweet(text, schools) == []


def test_players_to_watch_preview_is_dropped(schools):
    text = "Players to watch this week as recruiting heats up around the SEC."
    assert classify_tweet(text, schools) == []


def test_chose_x_over_only_counts_x(schools):
    # Vanderbilt isn't one of our 15 - the "over" schools must not count.
    text = (
        "NEWS: a 2027 four-star has committed to Vanderbilt, he tells our team. "
        "He chose Vanderbilt over Georgia, Clemson and Michigan."
    )
    assert classify_tweet(text, schools) == []


def test_chose_x_over_counts_x_when_x_is_one_of_our_15(schools):
    text = (
        "NEWS: a 2027 four-star football recruit has committed to Georgia, he tells our team. "
        "He chose Georgia over Clemson, Michigan and Oregon."
    )
    ev = classify_tweet(text, schools)
    assert len(ev) == 1
    assert ev[0].school == "Georgia"


def _run(coro):
    return asyncio.run(coro)


def test_reporter_tweet_with_no_resolvable_player_produces_no_row(schools):
    async def fake_resolve(handle, name):
        return {"name": name, "handle": handle, "class_year": "", "position": "", "height": "", "weight": "", "high_school": "", "state": ""}

    status, records, _ = _run(
        process_tweet(
            handle="RecruitInsiderFake",
            text="BREAKING: a 2027 four-star has committed to Georgia!",
            author_desc="On3 recruiting reporter covering the SEC.",
            author_location="",
            author_displayname="Recruit Insider Fake",
            mentions=[],
            tweet_id="1",
            tweet_date="2026-09-20T00:00:00+00:00",
            tweet_url="https://x.com/RecruitInsiderFake/status/1",
            schools_cfg=schools,
            school_handles=[h for s in schools for h in s.handles],
            resolve_profile=fake_resolve,
        )
    )
    assert status == "noise"
    assert records == []


def test_reporter_tweet_with_resolvable_player_produces_a_row(schools):
    async def fake_resolve(handle, name):
        return {
            "name": "", "handle": handle, "class_year": "2027", "position": "QB",
            "height": "", "weight": "", "high_school": "", "state": "",
        }

    status, records, _ = _run(
        process_tweet(
            handle="RecruitInsiderFake",
            text="2027 QB @Recruit2027QB has committed to Georgia! 🏈",
            author_desc="On3 recruiting reporter covering the SEC.",
            author_location="",
            author_displayname="Recruit Insider Fake",
            mentions=["Recruit2027QB"],
            tweet_id="2",
            tweet_date="2026-09-20T00:00:00+00:00",
            tweet_url="https://x.com/RecruitInsiderFake/status/2",
            schools_cfg=schools,
            school_handles=[h for s in schools for h in s.handles],
            resolve_profile=fake_resolve,
        )
    )
    assert status == "ok"
    assert len(records) == 1
    assert records[0].school == "Georgia"
    assert records[0].player_handle == "Recruit2027QB"


# ---------------------------------------------------------------------------
# Positive cases modeled on the 8 correct sample.csv rows (anonymized) -
# precision fixes must not kill recall.
# ---------------------------------------------------------------------------

POSITIVE_CASES = [
    ("Blessed to receive an offer from @AlabamaFTBL! #RollTide 🏈", "Alabama", "QB | c/o 2027 | Anytown HS"),
    ("Blessed to receive an offer from The Ohio State University! Go Buckeyes! 🏈", "Ohio State", "WR | c/o 2027 | Anytown HS"),
    ("EXTREMELY BLESSED TO RECEIVE AN OFFER FROM OREGON!!! 🏈 #scoducks", "Oregon", "ATH | c/o 2028 | Anytown HS"),
    ("Blessed to receive an offer from @Vol_Football! 🏈", "Tennessee", "LB | c/o 2027 | Anytown HS"),
    ("Blessed to receive my 18th offer from The University Of Tennessee! 🏈", "Tennessee", "DL | c/o 2027 | Anytown HS"),
    ("Blessed to receive an offer from the university of Texas A&M! #aggies 🏈", "Texas A&M", "OL | c/o 2027 | Anytown HS"),
]


def test_positive_offer_cases_still_classify(schools):
    for text, expected_school, bio in POSITIVE_CASES:
        ev = classify_tweet(text, schools, bio=bio)
        assert len(ev) == 1, f"expected 1 event for {text!r}, got {ev}"
        assert ev[0].event_type == "offer"
        assert ev[0].school == expected_school


# ---------------------------------------------------------------------------
# All six failure classes through the full pipeline at once: zero rows.
# ---------------------------------------------------------------------------

NEGATIVE_CASES = [
    # 1. school matching (lookalike suffix / handle)
    "Offered by Alabama State University! 🏈",
    # 2. non-football sport
    "Committed to continue my volleyball career at Georgia! 🏐",
    # 3. #AGTG alone / bare "committed"
    "So blessed right now! #AGTG 🏈",
    "He committed 3 turnovers in the game against Georgia. Rough night.",
    # 4. would-be player bio with only a state code (tested via classify_author
    #    directly above; here a tweet with no recruit context at all)
    "Our city is committed to improving roads near the Georgia line.",
    # 6. reporter roundup
    "Players to watch this week as recruiting heats up around the SEC.",
]


def test_all_six_failure_classes_produce_zero_rows(schools):
    for text in NEGATIVE_CASES:
        assert classify_tweet(text, schools) == [], f"expected no events for {text!r}"


# ---------------------------------------------------------------------------
# 7. "Offer from @unofficial-handle" must not let a hashtag alias assign a
#    school - a hashtag like #RollTide/#AllIn is reused far beyond the
#    school it nominally belongs to (a different sport's account, or a
#    small college that happens to share the hashtag).
# ---------------------------------------------------------------------------

def test_offer_from_unofficial_handle_ignores_hashtag_alias(schools):
    # @AlabamaHoopsFake isn't Alabama's configured football handle - the
    # #RollTide hashtag alone must not be trusted to assign Alabama.
    text = "Blessed to receive an offer from @AlabamaHoopsFake! #RollTide 🏈"
    assert classify_tweet(text, schools) == []


def test_offer_from_unofficial_handle_sharing_another_schools_hashtag(schools):
    # A small college ("Fakemont") posts its own offer using #AllIn (also
    # used as Clemson's alias) - the offer is "from @FakemontFootball",
    # which isn't Clemson's configured handle, so it must not resolve to
    # Clemson.
    text = (
        "I am HONORED to receive an Offer from @FakemontFootball !!! "
        "#ClawsUp #AllIn 🏈"
    )
    assert classify_tweet(text, schools) == []


def test_offer_from_official_handle_still_uses_hashtag_fallback(schools):
    # The control case: when the "from @handle" IS the school's own
    # configured handle, the hashtag (or the handle mention itself) still
    # resolves normally.
    text = "Blessed to receive an offer from @ClemsonFB! #AllIn 🏈"
    ev = classify_tweet(text, schools)
    assert len(ev) == 1
    assert ev[0].school == "Clemson"


# ---------------------------------------------------------------------------
# 8. A state name used as a location ("... University in Oregon") is not the
#    school.
# ---------------------------------------------------------------------------

def test_state_name_as_location_is_not_a_school_match(schools):
    text = "Committed to Fakemont University in Oregon! 🏈 #excited"
    assert match_schools(text, schools) == []


def test_state_name_as_school_alias_still_matches(schools):
    text = "Blessed to receive an offer from Oregon! 🏈 #GoDucks"
    assert match_schools(text, schools) == ["Oregon"]


# ---------------------------------------------------------------------------
# 9. Directional qualifiers (Western/Eastern/Northern/Southern/Central) are
#    rejected the same way West/North/South/East already are, and a
#    Game Day Invite is not an offer.
# ---------------------------------------------------------------------------

def test_directional_qualifier_rejects_school(schools):
    assert match_schools("Committed to Western Michigan University! 🏈", schools) == []


def test_game_day_invite_is_not_an_offer(schools):
    text = "Blessed to receive a Game Day Invite from Western Michigan! #excited 🏈"
    assert classify_tweet(text, schools) == []


# ---------------------------------------------------------------------------
# 10. Invites (game day / camp / junior day / visit) never count as offers
#     unless a genuine offer phrase also applies.
# ---------------------------------------------------------------------------

def test_visit_invite_alone_is_not_an_offer(schools):
    text = "Blessed to receive a Game Day Visit invite from @PennStateFball! #WeAre 🏈"
    assert classify_tweet(text, schools) == []


def test_camp_invite_alone_is_not_an_offer(schools):
    text = "Excited for the camp invite from Georgia this weekend! #GoDawgs 🏈"
    assert classify_tweet(text, schools) == []


def test_invite_wording_plus_genuine_offer_phrase_still_counts(schools):
    # A tweet naming both an invite AND an unambiguous offer phrase (e.g. a
    # recap post: "got a Game Day Invite... also received an offer from...")
    # must still produce a row - the invite gate only suppresses invite-only
    # posts, not genuine offer posts that happen to mention an invite too.
    text = (
        "Got a Game Day Invite from Georgia, and also received an offer "
        "from Georgia! 🏈 #GoDawgs"
    )
    ev = classify_tweet(text, schools)
    assert len(ev) == 1
    assert ev[0].school == "Georgia"


# ---------------------------------------------------------------------------
# 11. First-person offer wording makes the tweet's author the player, even
#     when their bio has an ESPN/outlet-sounding phrase and the tweet
#     @-mentions a coach - the coach must never be read as the player.
# ---------------------------------------------------------------------------

def test_first_person_offer_with_espn_bio_phrase_is_still_a_player(schools):
    bio = "2028 | DB | ESPN TOP 300 | HC @CoachFakeName |"
    text = "Blessed to receive an offer from the University of Oregon! @oregonfootball"
    # Without the tweet text, a bare bio mention of "ESPN" reads as a
    # reporter bio (the safe fallback) - it's the first-person offer
    # wording in the tweet itself that overrides it to "player".
    assert classify_author(bio, schools) == "reporter"
    assert classify_author(bio, schools, text=text) == "player"


def test_process_tweet_player_author_not_replaced_by_mentioned_coach(schools):
    async def fake_resolve(handle, name):
        raise AssertionError("resolve_profile should not be called for a player author")

    status, records, profile = _run(
        process_tweet(
            handle="Recruit2028DB",
            text=(
                "After a great conversation with Coach @CoachFakeName, blessed to "
                "receive an offer from the University of Oregon! @oregonfootball"
            ),
            author_desc="2028 | DB | ESPN TOP 300 | HC @CoachFakeName |",
            author_location="",
            author_displayname="Recruit 2028 DB",
            mentions=["CoachFakeName", "oregonfootball"],
            tweet_id="3",
            tweet_date="2026-09-20T00:00:00+00:00",
            tweet_url="https://x.com/Recruit2028DB/status/3",
            schools_cfg=schools,
            school_handles=[h for s in schools for h in s.handles],
            resolve_profile=fake_resolve,
        )
    )
    assert status == "ok"
    assert len(records) == 1
    assert records[0].player_handle == "Recruit2028DB"
    assert records[0].school == "Oregon"


# ---------------------------------------------------------------------------
# 12. Recall fixes: a multi-sport bio that also explicitly says "football"
#     must not be rejected for the other sport it names, and bare
#     "<School> offered" (no "from"/"excited to"/etc) still counts.
# ---------------------------------------------------------------------------

def test_multi_sport_bio_with_football_named_is_not_rejected(schools):
    text = "Blessed to receive my 1st D1 Offer from the University of Alabama! 🐘 @AlabamaFTBL"
    bio = "6'2 210 | Anytown HS | C/O 2028 | Football (DL/OL) | Basketball | Track/Field Athlete"
    ev = classify_tweet(text, schools, bio=bio)
    assert len(ev) == 1
    assert ev[0].school == "Alabama"


def test_pure_other_sport_bio_still_rejected(schools):
    # Control case: a bio that's purely another sport, with no "football"
    # word anywhere, must still be rejected (unchanged from before).
    text = "Excited to receive an offer from LSU!"
    bio = "RHP | c/o 2027 | Anytown HS"
    assert classify_tweet(text, schools, bio=bio) == []


def test_bare_offered_without_from_still_counts(schools):
    text = "Oklahoma offered #blessed 🙏"
    bio = "3 star ATH | Class of 2028 | Anytown HS"
    ev = classify_tweet(text, schools, bio=bio)
    assert len(ev) == 1
    assert ev[0].event_type == "offer"
    assert ev[0].school == "Oklahoma"


# ---------------------------------------------------------------------------
# 13. 11-school expansion collisions (Indiana, Cincinnati, Miami (FL), Texas
#     Tech, Florida State, Georgia Tech, Georgia State, Virginia Tech, North
#     Carolina, South Florida, Georgia Southern). All fixtures below are
#     made up and anonymized, same as the rest of this file.
# ---------------------------------------------------------------------------

# --- Georgia family: Tech/State/Southern match only themselves, never plain
#     Georgia; plain Georgia still matches alone. ---

def test_georgia_tech_matches_only_georgia_tech(schools):
    text = "Committed to Georgia Tech! 🏈 #StingEm"
    assert match_schools(text, schools) == ["Georgia Tech"]


def test_georgia_state_matches_only_georgia_state(schools):
    text = "Blessed to receive an offer from Georgia State! 🏈"
    assert match_schools(text, schools) == ["Georgia State"]


def test_georgia_southern_matches_only_georgia_southern(schools):
    text = "Excited to commit to Georgia Southern! 🏈"
    assert match_schools(text, schools) == ["Georgia Southern"]


def test_plain_georgia_still_matches_georgia(schools):
    text = "Blessed to receive an offer from Georgia! 🏈 #GoDawgs"
    assert match_schools(text, schools) == ["Georgia"]
    assert match_schools("UGA offered me! 🏈", schools) == ["Georgia"]


# --- Florida family: Florida / Florida State / South Florida kept apart;
#     USF requires football context; other Florida-adjacent schools
#     rejected. ---

def test_florida_state_matches_only_florida_state(schools):
    text = "Committed to Florida State! #GoNoles 🏈"
    assert match_schools(text, schools) == ["Florida State"]


def test_plain_florida_still_matches_florida(schools):
    text = "Blessed to receive an offer from Florida! 🏈 #GoGators"
    assert match_schools(text, schools) == ["Florida"]


def test_usf_alone_needs_football_context(schools):
    assert match_schools("Committed to USF!", schools) == []
    assert match_schools("Committed to USF! 🏈", schools) == ["South Florida"]


def test_west_florida_atlantic_fam_and_famu_rejected(schools):
    assert match_schools("Offered by the University of West Florida! 🏈", schools) == []
    assert match_schools("Committed to Florida Atlantic University! 🏈", schools) == []
    assert match_schools("Offered by Florida A&M University! 🏈", schools) == []


# --- Texas family: Texas / Texas Tech / Texas A&M kept apart; Texas State,
#     Texas A&M-Commerce rejected. ---

def test_texas_tech_matches_only_texas_tech(schools):
    text = "Blessed to receive an offer from Texas Tech! 🏈 #WreckEm"
    assert match_schools(text, schools) == ["Texas Tech"]


def test_texas_state_and_texas_am_commerce_rejected(schools):
    assert match_schools("Committed to Texas State University! 🏈", schools) == []
    assert match_schools("Offered by Texas A&M-Commerce! 🏈", schools) == []


# --- Virginia Tech never matches plain Virginia; plain Virginia isn't
#     tracked at all. ---

def test_virginia_tech_matches_only_virginia_tech(schools):
    text = "Committed to Virginia Tech! 🏈 #Hokies"
    assert match_schools(text, schools) == ["Virginia Tech"]


def test_plain_virginia_is_not_tracked(schools):
    text = "Excited to visit the University of Virginia! 🏈"
    assert match_schools(text, schools) == []


# --- UNC: UNC/North Carolina/Tar Heels match; NC State, UNC Charlotte/
#     Charlotte, UNC Pembroke, UNCW/UNC Wilmington, North Carolina A&T,
#     North Carolina Central are rejected. ---

def test_unc_aliases_match_north_carolina(schools):
    assert match_schools(
        "Blessed to receive an offer from North Carolina! 🏈 #TarHeels", schools
    ) == ["North Carolina"]
    assert match_schools("UNC offered me today! 🏈", schools) == ["North Carolina"]


def test_unc_lookalikes_rejected(schools):
    assert match_schools("Committed to NC State! 🏈", schools) == []
    assert match_schools("Committed to UNC Charlotte! 🏈", schools) == []
    assert match_schools("Offered by UNC Pembroke! 🏈", schools) == []
    assert match_schools("Committed to UNC Wilmington! 🏈", schools) == []
    assert match_schools("Committed to UNCW! 🏈", schools) == []
    assert match_schools("Offered by North Carolina A&T! 🏈", schools) == []
    assert match_schools("Committed to North Carolina Central! 🏈", schools) == []


# --- Miami: Miami/The U/Canes/Hurricanes match Miami (FL); Miami (OH)/
#     Miami of Ohio/Miami University/RedHawks rejected; "Miami" as a bare
#     location (not the school) doesn't count. ---

def test_miami_aliases_match_miami_fl(schools):
    assert match_schools(
        "Blessed to receive an offer from Miami! 🏈 #GoCanes", schools
    ) == ["Miami (FL)"]
    assert match_schools("Committed to The U! 🏈", schools) == ["Miami (FL)"]
    assert match_schools("Go Canes! Committed! 🏈", schools) == ["Miami (FL)"]
    assert match_schools("Blessed to receive an offer from the Hurricanes! 🏈", schools) == [
        "Miami (FL)"
    ]


def test_miami_of_ohio_context_rejected(schools):
    assert match_schools("Offered by Miami University! 🏈", schools) == []
    assert match_schools("Committed to Miami (OH)! 🏈", schools) == []
    assert match_schools("Committed to Miami! Go RedHawks! 🏈", schools) == []


def test_miami_as_bare_location_is_not_the_school(schools):
    text = "3-star ATH from Miami, FL committed to Fakemont University! 🏈"
    assert match_schools(text, schools) == []
    text2 = "Miami-area high school standout commits to Fakemont! 🏈"
    assert match_schools(text2, schools) == []
    text3 = "Recruit plays high school ball in Miami and committed to Fakemont! 🏈"
    assert match_schools(text3, schools) == []


# --- Indiana: Indiana State / IU Indianapolis rejected; Hoosiers/IU match
#     (IU only with football context or the handle). ---

def test_indiana_state_and_iu_indianapolis_rejected(schools):
    assert match_schools("Committed to Indiana State! 🏈", schools) == []
    assert match_schools("Offered by IU Indianapolis! 🏈", schools) == []


def test_hoosiers_matches_iu_needs_football_context(schools):
    assert match_schools("Committed to the Hoosiers! 🏈", schools) == ["Indiana"]
    assert match_schools("Committed to IU!", schools) == []
    assert match_schools("Committed to IU! 🏈", schools) == ["Indiana"]


# --- Cincinnati: Bearcats matches; UC only via the official handle. ---

def test_bearcats_matches_uc_only_via_handle(schools):
    assert match_schools(
        "Blessed to receive an offer from the Bearcats! 🏈", schools
    ) == ["Cincinnati"]
    assert match_schools("Committed to UC! 🏈", schools) == []
    assert match_schools("Committed! 🏈 @GoBearcatsFB", schools) == ["Cincinnati"]


# --- USC: stays the Trojans; rejected when South Carolina context (or
#     USC Aiken/Beaufort/Upstate) is present anywhere in the tweet. ---

def test_usc_still_matches_trojans(schools):
    text = "Blessed to receive an offer from USC! 🏈 #FightOn"
    assert match_schools(text, schools) == ["USC"]


def test_usc_rejected_with_south_carolina_context(schools):
    assert match_schools("Committed to USC! Go Gamecocks! 🏈", schools) == []
    assert match_schools(
        "Offered by the University of South Carolina (USC)! 🏈", schools
    ) == []
    assert match_schools("Committed to USC Upstate! 🏈", schools) == []
    assert match_schools("Offered by USC Aiken! 🏈", schools) == []
    assert match_schools("Committed to USC Beaufort! 🏈", schools) == []


# --- Michigan/Ohio State/Penn State: directional-qualifier and State-suffix
#     rejections still hold with the larger config. ---

def test_michigan_directional_qualifiers_still_rejected(schools):
    assert match_schools("Committed to Central Michigan! 🏈", schools) == []
    assert match_schools("Committed to Eastern Michigan! 🏈", schools) == []
    assert match_schools("Cartae commits to Michigan State 🏈", schools) == []


# --- Full-pipeline sanity check: a genuine offer for one of the new
#     schools still produces exactly one row end to end. ---

def test_full_pipeline_offer_for_new_school(schools):
    text = "Blessed to receive an offer from Georgia Tech! 🏈 #StingEm"
    bio = "ATH | c/o 2027 | Anytown HS"
    ev = classify_tweet(text, schools, bio=bio)
    assert len(ev) == 1
    assert ev[0].event_type == "offer"
    assert ev[0].school == "Georgia Tech"
