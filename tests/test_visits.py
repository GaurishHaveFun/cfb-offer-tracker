"""Upcoming/completed visit classification for the 'visits' tab, plus the
visit query set, process_tweet's visit records and the upcoming ->
completed merge. All tweets are made up."""
import asyncio

import pytest

from cfb_offers import main
from cfb_offers.classify import classify_visit
from cfb_offers.dedupe import dedupe_events
from cfb_offers.models import VisitRecord
from cfb_offers.queries import MAX_QUERY_LEN, VISIT_PHRASES, build_slice_queries, build_visit_queries
from cfb_offers.sources import classify_author

PLAYER_BIO = "WR | c/o 2028 | Anytown HS"


def visits(text, schools, bio=PLAYER_BIO):
    return [(v.school, v.visit_type) for v in classify_visit(text, schools, bio=bio)]


def statuses(text, schools, bio=PLAYER_BIO):
    return [(v.school, v.visit_type, v.status) for v in classify_visit(text, schools, bio=bio)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Thank you @GeorgiaFootball for having me this weekend! #GoDawgs", [("Georgia", "unofficial")]),
        ("Thanks coach for letting me visit Alabama, will be back!", [("Alabama", "unofficial")]),
        ("Had a great official visit at LSU this weekend. Thank you coaches!", [("LSU", "official")]),
        ("Amazing #OV at Oregon 🦆 thank you for having me", [("Oregon", "official")]),
        ("Great unofficial visit at Texas today, thanks for having me", [("Texas", "unofficial")]),
        ("Had a great time at Michigan today! Thanks for the visit coach", [("Michigan", "unofficial")]),
    ],
)
def test_completed_visits(text, expected, schools):
    assert statuses(text, schools) == [(*e, "completed") for e in expected]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I will be visiting Georgia today !! #GoDawgs", [("Georgia", "unofficial")]),
        ("I'll be visiting Alabama this weekend", [("Alabama", "unofficial")]),
        ("Visiting LSU today 🐯", [("LSU", "unofficial")]),
        ("I'm on campus at Clemson today!", [("Clemson", "unofficial")]),
        ("On campus at Texas this weekend", [("Texas", "unofficial")]),
        ("I'll be at the Michigan game tonight 〽️", [("Michigan", "unofficial")]),
        ("Heading to Oregon this weekend for a visit", [("Oregon", "unofficial")]),
        ("OV set for 6/12 at Georgia!! Can't wait", [("Georgia", "official")]),
        ("Upcoming official visit to Oregon", [("Oregon", "official")]),
        ("Blessed to receive an official visit invite to LSU", [("LSU", "official")]),
        ("Thanks for the Game Day invite @AlabamaFTBL", [("Alabama", "unofficial")]),
        ("Blessed to receive a junior day invite from Ohio State 🏈", [("Ohio State", "unofficial")]),
        (
            "Excited to announce my official visits: Georgia 6/12, Texas 6/19",
            [("Georgia", "official"), ("Texas", "official")],
        ),
    ],
)
def test_upcoming_visits(text, expected, schools):
    assert statuses(text, schools) == [(*e, "upcoming") for e in expected]


def test_versus_game_visit_goes_to_the_first_named_school(schools):
    text = "I will be visiting Clemson vs Georgia southern game!! @ClemsonFB"
    assert statuses(text, schools) == [("Clemson", "unofficial", "upcoming")]


@pytest.mark.parametrize(
    "text, expected",
    [
        # past tense beats "game day visit" / "invite"
        ("Had a great game day visit to @MizzouFootball yesterday. Thanks coach for the invite!", ["Missouri"]),
        ("Great game day visit saturday at @ClemsonFB. Thank you for the invite", ["Clemson"]),
        ("Great time at @GamecockFB this past weekend. Really appreciated the game day invite", ["South Carolina"]),
        ("Thank you @Vol_Football for having me and for the gameday invite", ["Tennessee"]),
        # "looking forward to coming back" closes a thank-you post
        (
            "Great experience at @MizzouFootball game day visit today. Thanks for the hospitality! "
            "Looking forward to coming back.",
            ["Missouri"],
        ),
    ],
)
def test_past_visits_are_completed(text, expected, schools):
    assert statuses(text, schools) == [(s, "unofficial", "completed") for s in expected]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Can\u2019t wait to be at Georgia State today for a game day visit!!", ["Georgia State"]),
        ("I can\u2019t wait to be @NDFootball this weekend for a game day visit!", ["Notre Dame"]),
        ("Looking forward to a game day visit @UKFootball for Saturday's game against LSU.", ["Kentucky"]),
        ("Looking forward to being back on the hill Saturday for @RazorbackFB vs @Vol_Football", ["Arkansas"]),
    ],
)
def test_upcoming_game_day_visits(text, expected, schools):
    assert statuses(text, schools) == [(s, "unofficial", "upcoming") for s in expected]


def test_taking_an_official_visit_is_upcoming(schools):
    assert statuses("I will be taking an official visit to Texas Tech on Oct 17th!!", schools) == [
        ("Texas Tech", "official", "upcoming")
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("We had a great visit with @MizzouFootball Saturday during their win over Florida.", ["Missouri"]),
        ("Had a great time in Knoxville watching the win vs Auburn! i appreciate @Vol_Football", ["Tennessee"]),
        ("Had a great visit down in Tucson yesterday @ArizonaFBall. Big win against Cincinnati!", []),
        ("Had a great time at the Gophers win vs Michigan. Loved the atmosphere", []),
        ("Excited to be back on campus tonight at Rutgers for a game visit Vs. Indiana!!", []),
    ],
)
def test_opponents_get_no_visit(text, expected, schools):
    assert [v[0] for v in statuses(text, schools)] == expected


def test_thanks_plus_plans_to_return_is_completed(schools):
    text = "Thanks for having me Alabama, will be visiting again soon"
    assert statuses(text, schools) == [("Alabama", "unofficial", "completed")]


@pytest.mark.parametrize(
    "text",
    [
        # a camp isn't a visit; "blessed to receive" an award isn't either
        "Thanks for the camp invite Georgia",
        "Blessed to receive player of the week, thanks for having me Georgia",
        # an offer/commit announcement is that event only
        "Thanks for having me coach! Blessed to receive an offer from Georgia",
        "After a great visit I am 100% committed to Texas",
        "Thank you @GeorgiaFootball for having me today and giving me an official offer to play football",
        "Thanks for having me! Proud to announce my second offer from a school we don't track",
        # reporter roundup
        "Visitor list for Georgia this weekend, thanks for having us",
        # not football
        "Thanks for having me Georgia! #hoops",
        # no visit wording
        "Georgia looked great today",
    ],
)
def test_not_a_visit(text, schools):
    assert visits(text, schools) == []


def test_visit_needs_football_signal(schools):
    assert visits("Thanks for having me Georgia", schools, bio="") == []


def test_school_must_be_named_near_the_visit_wording(schools):
    text = (
        "Thank you @GeorgiaFootball for having me! "
        + "x" * 200
        + " Shoutout to my old coach who played at LSU"
    )
    assert visits(text, schools) == [("Georgia", "unofficial")]


def test_lowercase_ov_is_not_official(schools):
    assert visits("thanks for having me @GeorgiaFootball, ov my goodness", schools) == [
        ("Georgia", "unofficial")
    ]


def test_visit_thanks_overrides_outlet_word_in_player_bio(schools):
    bio = "ESPN Top 300 WR"
    assert classify_author(bio, schools, text="Thank you for having me @GeorgiaFootball") == "player"


def test_visit_queries_cover_every_school_and_stay_under_limit(schools):
    queries = build_visit_queries(schools, 2)
    assert all(len(q) <= MAX_QUERY_LEN for q in queries)
    assert all(q.startswith(f"({' OR '.join(VISIT_PHRASES)})") for q in queries)
    for school in schools:
        assert any(school.aliases[0] in q for q in queries), school.name


def test_visit_slice_queries_use_visit_phrases(schools):
    import datetime as dt

    from cfb_offers.queries import VISIT_PHRASE_CLAUSE

    queries = build_slice_queries(schools, dt.date(2026, 10, 1), dt.date(2026, 10, 6), VISIT_PHRASE_CLAUSE)
    assert queries and all(q.startswith(VISIT_PHRASE_CLAUSE) for q in queries)
    assert all("until:2026-10-06" in q and len(q) <= MAX_QUERY_LEN for q in queries)


def test_search_plan_visits_only(schools):
    plan = main.search_plan(schools, 2, None, offers=False)
    assert [q for _, q in plan] == build_visit_queries(schools, 2)
    both = main.search_plan(schools, 2, None)
    assert len(both) == len(plan) + len(main.build_all_queries(schools, 2))


def test_backfill_plan_includes_visit_queries_per_slice(schools):
    plan = main.search_plan(schools, 5, 5, offers=False)
    assert plan and all("for having me" in q for _, q in plan)
    assert plan[0][0].startswith("slice 1/1")


def test_process_tweet_builds_visit_record(schools):
    async def no_lookup(handle, name):
        raise AssertionError("player's own post needs no lookup")

    status, records, _ = asyncio.run(
        main.process_tweet(
            handle="FakeRecruit9",
            text="Had a great official visit at LSU. Thank you for having me!",
            author_desc=PLAYER_BIO,
            author_location="",
            author_displayname="Fake Recruit",
            mentions=[],
            tweet_id="9",
            tweet_date="2026-10-04T00:00:00+00:00",
            tweet_url="https://x.com/FakeRecruit9/status/9",
            schools_cfg=schools,
            school_handles=main._all_school_handles(schools),
            resolve_profile=no_lookup,
        )
    )
    assert status == "ok"
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, VisitRecord)
    assert (rec.event_key, rec.visit_type, rec.school) == ("fakerecruit9|lsu|visit", "official", "LSU")
    assert rec.visit_status == "completed"
    assert rec.class_year == "2028" and rec.position == "WR"


def _visit(tweet_id, status, date, notes=""):
    return VisitRecord(
        event_key="fakerecruit9|georgia|visit",
        visit_type="unofficial",
        school="Georgia",
        player_name="Fake Recruit",
        player_handle="FakeRecruit9",
        class_year="2028",
        position="WR",
        height="",
        weight="",
        high_school="",
        state="",
        source_type="player",
        source_handle="FakeRecruit9",
        tweet_id=tweet_id,
        tweet_date=date,
        tweet_url=f"https://x.com/FakeRecruit9/status/{tweet_id}",
        tweet_text="",
        notes=notes,
        visit_status=status,
    )


def test_upcoming_visit_turns_completed_when_thanks_follows():
    upcoming = _visit("1", "upcoming", "2026-10-03T00:00:00+00:00")
    thanks = _visit("2", "completed", "2026-10-04T00:00:00+00:00")
    [row] = dedupe_events([thanks, upcoming])
    assert (row.tweet_id, row.visit_status) == ("1", "completed")
    assert row.notes == "completed: https://x.com/FakeRecruit9/status/2"


def test_upcoming_visit_alone_stays_upcoming():
    [row] = dedupe_events([_visit("1", "upcoming", "2026-10-03T00:00:00+00:00")])
    assert (row.visit_status, row.notes) == ("upcoming", "")


def test_visits_csv_path():
    assert main.visits_csv_path("sample.csv") == "sample_visits.csv"
    assert main.visits_csv_path("out") == "out_visits"
