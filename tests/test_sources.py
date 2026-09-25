from cfb_offers.sources import classify_author, resolve_player_mention


def test_player_bio_classified_as_player(tweets, schools):
    assert classify_author(tweets["player_offer"]["author"]["description"], schools) == "player"


def test_reporter_bio_classified_as_reporter(tweets, schools):
    assert classify_author(tweets["reporter_commit"]["author"]["description"], schools) == "reporter"


def test_coach_bio_classified_as_coach(tweets, schools):
    assert classify_author(tweets["coach_offer"]["author"]["description"], schools) == "coach"


def test_reporter_via_on3(tweets, schools):
    assert classify_author(tweets["flip"]["author"]["description"], schools) == "reporter"


def test_fan_bio_dropped(tweets, schools):
    assert classify_author(tweets["fan_speculation"]["author"]["description"], schools) is None


def test_generic_unclassifiable_bio_dropped(tweets, schools):
    assert classify_author(tweets["noise_unclassified_author"]["author"]["description"], schools) is None


def test_resolve_player_mention_from_at_mention(tweets):
    t = tweets["same_event_reporter_side"]
    mention = resolve_player_mention(t["text"], ["big_dline44"], exclude_handles=["MichRecruiting"])
    assert mention.handle == "big_dline44"
    assert mention.name == ""


def test_resolve_player_mention_falls_back_to_name_parsing(tweets):
    t = tweets["reporter_commit"]
    mention = resolve_player_mention(t["text"], [], exclude_handles=[])
    assert mention.handle == ""
    assert mention.name == "John Smith"


def test_resolve_player_mention_excludes_school_and_own_handle():
    text = "2027 ATH Prospect Name has committed to Alabama"
    mention = resolve_player_mention(
        text, ["AlabamaFTBL", "RecruitInsiderX"], exclude_handles=["AlabamaFTBL", "RecruitInsiderX"]
    )
    # no non-excluded mention -> falls back to name parsing
    assert mention.handle == ""
    assert mention.name == "Prospect Name"
