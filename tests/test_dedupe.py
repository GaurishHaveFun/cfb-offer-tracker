from cfb_offers.dedupe import add_source, dedupe_events, make_event_key, normalize_name
from cfb_offers.models import OfferRecord


def _record(**overrides) -> OfferRecord:
    base = dict(
        event_key="",
        event_type="commit",
        is_flip=False,
        school="Michigan",
        player_name="Andre Walker",
        player_handle="big_dline44",
        class_year="2027",
        position="DL",
        height="6'4",
        weight="260lbs",
        high_school="East HS",
        state="MI",
        source_type="player",
        source_handle="big_dline44",
        tweet_id="1",
        tweet_date="2026-09-01T12:00:00",
        tweet_url="https://x.com/big_dline44/status/1",
        tweet_text="100% committed to Michigan!",
        also_reported_by="",
        notes="",
        scraped_at="2026-09-01T12:05:00",
    )
    base.update(overrides)
    base["event_key"] = make_event_key(base["player_handle"], base["player_name"], base["school"], base["event_type"])
    return OfferRecord(**base)


def test_make_event_key_prefers_handle():
    key_with_handle = make_event_key("big_dline44", "Andre Walker", "Michigan", "commit")
    key_by_name_only = make_event_key("", "Andre Walker", "Michigan", "commit")
    assert key_with_handle == "big_dline44|michigan|commit"
    assert key_by_name_only == "andre walker|michigan|commit"


def test_normalize_name_collapses_whitespace_and_case():
    assert normalize_name("  Andre   Walker ") == "andre walker"


def test_add_source_dedupes_and_preserves_order():
    result = add_source("HandleA", "HandleB")
    assert result == "HandleA, HandleB"
    assert add_source(result, "HandleA") == result  # no duplicate


def test_player_and_reporter_same_event_collapse_to_one_row():
    player_side = _record(
        source_type="player",
        source_handle="big_dline44",
        tweet_id="100",
        tweet_date="2026-09-01T12:00:00",
    )
    reporter_side = _record(
        source_type="reporter",
        source_handle="MichRecruiting",
        player_handle="big_dline44",  # reporter @-mentioned the player
        tweet_id="101",
        tweet_date="2026-09-01T12:10:00",
        tweet_text="2027 DL Andre Walker (@big_dline44) has committed to Michigan!",
    )

    result = dedupe_events([player_side, reporter_side])

    assert len(result) == 1
    row = result[0]
    assert row.tweet_id == "100"  # earliest tweet kept as the row
    assert row.also_reported_by == "MichRecruiting"


def test_decommit_after_commit_is_a_separate_row():
    commit = _record(event_type="commit", tweet_id="1", tweet_date="2026-09-01T12:00:00")
    decommit = _record(
        event_type="decommit",
        tweet_id="2",
        tweet_date="2026-09-05T12:00:00",
        tweet_text="Decommitting from Michigan.",
    )
    result = dedupe_events([commit, decommit])
    assert len(result) == 2
    event_types = {r.event_type for r in result}
    assert event_types == {"commit", "decommit"}


def test_unrelated_events_stay_separate():
    a = _record(tweet_id="1")
    b = _record(tweet_id="2", school="Ohio State", player_handle="other_guy", player_name="Other Guy")
    result = dedupe_events([a, b])
    assert len(result) == 2
