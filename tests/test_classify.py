import asyncio

from cfb_offers.classify import (
    classify_tweet,
    detect_event_type,
    is_noise,
    is_school_account,
    iter_profilefblive_players,
    match_schools,
    parse_profilefblive_roundup,
)
from cfb_offers.main import process_tweet


def test_player_self_announced_offer(tweets, schools):
    ev = classify_tweet(tweets["player_offer"]["text"], schools)
    assert len(ev) == 1
    assert ev[0].event_type == "offer"
    assert ev[0].school == "Alabama"
    assert ev[0].is_flip is False


def test_reporter_commit(tweets, schools):
    ev = classify_tweet(tweets["reporter_commit"]["text"], schools)
    assert len(ev) == 1
    assert ev[0].event_type == "commit"
    assert ev[0].school == "Ohio State"
    assert ev[0].is_flip is False


def test_coach_offer(tweets, schools):
    t = tweets["coach_offer"]
    ev = classify_tweet(t["text"], schools, bio=t["author"]["description"])
    assert len(ev) == 1
    assert ev[0].event_type == "offer"
    assert ev[0].school == "Oregon"


def test_flip_is_commit_with_flag_and_notes(tweets, schools):
    t = tweets["flip"]
    ev = classify_tweet(t["text"], schools, bio=t["author"]["description"])
    assert len(ev) == 1
    assert ev[0].event_type == "commit"
    assert ev[0].is_flip is True
    assert ev[0].school == "Texas"
    assert "Oklahoma" in ev[0].notes


def test_decommit(tweets, schools):
    t = tweets["decommit"]
    ev = classify_tweet(t["text"], schools, bio=t["author"]["description"])
    assert len(ev) == 1
    assert ev[0].event_type == "decommit"
    assert ev[0].school == "Clemson"


def test_multi_school_offer_gets_one_row_per_school(tweets, schools):
    t = tweets["multi_school_offer"]
    ev = classify_tweet(t["text"], schools, bio=t["author"]["description"])
    schools_matched = {e.school for e in ev}
    assert schools_matched == {"Alabama", "LSU"}
    assert all(e.event_type == "offer" for e in ev)


def test_fan_speculation_is_noise(tweets):
    assert is_noise(tweets["fan_speculation"]["text"]) is True


def test_fan_speculation_classifies_to_no_events(tweets, schools):
    assert classify_tweet(tweets["fan_speculation"]["text"], schools) == []


def test_noise_patterns_direct():
    assert is_noise("Man Georgia should offer this kid") is True
    assert is_noise("Just my prediction, he ends up at LSU") is True
    assert is_noise("Blessed to receive an offer from Georgia!") is False


def test_event_type_order_decommit_before_commit():
    # contains both "commit" and "decommit" wording; decommit must win
    text = "He was committed to Florida but has now decided to decommit."
    event_type, _ = detect_event_type(text)
    assert event_type == "decommit"


def test_school_account_detected(schools):
    assert is_school_account("AlabamaFTBL", schools) is True
    assert is_school_account("@AlabamaFTBL", schools) is True
    assert is_school_account("random_fan", schools) is False


def test_profilefblive_roundup_parses_multiple_players(schools):
    text = (
        "Auburn has extended six new scholarship offers to HS prospects in the last 24 hours. "
        "@AuburnFootball 4.8⭐ 2029 ATH Jaedon White @lighting_white3 "
        "3.7⭐ 2029 LB Jonah Parker @jonahdp29 3.5⭐ 2030 CB Brice Johnson @BriceJohnson155 "
        "3.5⭐ 2029 WR Jarvis Woods @tha_kidii 3.5⭐ 2029 DE Charlie Hughes @Chughesfootball "
        "3.5⭐ 2027 OT Moreno Fisher Jr. @reno_fisherjr"
    )
    events = parse_profilefblive_roundup(text, schools)
    assert len(events) == 6
    assert {e.school for e in events} == {"Auburn"}
    assert {e.event_type for e in events} == {"offer"}
    assert {e.notes for e in events} == {""}
    assert any(e.school == "Auburn" and e.event_type == "offer" for e in events)


def test_profilefblive_roundup_processed_as_offer_rows(schools):
    async def _run():
        text = (
            "Auburn has extended six new scholarship offers to HS prospects in the last 24 hours. "
            "@AuburnFootball 4.8⭐ 2029 ATH Jaedon White @lighting_white3 "
            "3.7⭐ 2029 LB Jonah Parker @jonahdp29"
        )

        async def _resolve(handle: str, name: str):
            return {
                "name": name,
                "handle": handle,
                "class_year": "2029",
                "position": "ATH" if "Jaedon" in name else "LB",
                "height": "",
                "weight": "",
                "high_school": "",
                "state": "",
            }

        status, rows, _ = await process_tweet(
            handle="profilefblive",
            text=text,
            author_desc="",
            author_location="",
            author_displayname="PROFILE",
            mentions=["lighting_white3", "jonahdp29"],
            tweet_id="123",
            tweet_date="2026-10-14",
            tweet_url="https://x.com/profilefblive/status/123",
            schools_cfg=schools,
            school_handles=[h for s in schools for h in s.handles],
            resolve_profile=_resolve,
        )
        assert status == "ok"
        assert len(rows) == 2
        assert {row.school for row in rows} == {"Auburn"}
        assert {row.position for row in rows} == {"ATH", "LB"}

    asyncio.run(_run())


def test_profilefblive_commit_roundup_not_parsed_as_offers(schools):
    text = "Auburn has landed a commitment from 4.8⭐ 2029 ATH Jaedon White @lighting_white3 who committed to Auburn"
    assert iter_profilefblive_players(text, schools) == []


def test_profilefblive_ignores_lowercase_lookalikes(schools):
    text = "Auburn has extended new scholarship offers 4.8⭐ 2029 ath jaedon white @lighting_white3"
    assert iter_profilefblive_players(text, schools) == []


def test_match_schools_no_match_returns_empty(schools):
    assert match_schools("nothing relevant here", schools) == []
