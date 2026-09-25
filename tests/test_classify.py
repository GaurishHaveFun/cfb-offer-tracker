from cfb_offers.classify import classify_tweet, detect_event_type, is_noise, is_school_account, match_schools


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


def test_match_schools_no_match_returns_empty(schools):
    assert match_schools("nothing relevant here", schools) == []
