"""Regressions from the first real backfill review. Every fixture is
paraphrased and anonymized - made-up players, reporters and handles."""
from cfb_offers.classify import classify_tweet
from cfb_offers.dedupe import normalize_name
from cfb_offers.profile import parse_class_year
from cfb_offers.sources import classify_author, resolve_player_mention

FOOTBALL_BIO = "c/o 2028 | WR | Fakeville HS football"


def schools_of(text, schools, bio=FOOTBALL_BIO):
    return [(e.event_type, e.school) for e in classify_tweet(text, schools, bio)]


# --- offers: hypothetical wording and offer/school proximity ---------------

def test_hypothetical_offer_about_a_coach_is_noise(schools):
    text = ("Coach Fakeman is solid - if he does a good job at LSU he might be able "
            "to get an offer from a bigger brand and make a title run 🏈")
    assert schools_of(text, schools) == []


def test_school_must_be_named_near_the_offer_phrase(schools):
    text = ("Spent the summer training in Oregon with my guys. Today I'm blessed to "
            "receive an offer from Fakeville State University!! Long road ahead but "
            "grateful for everyone who helped me get here 🏈")
    assert schools_of(text, schools) == []


def test_existing_offers_list_is_not_a_new_offer(schools):
    text = ("2028 WR Fake Player reports an offer from Fakeville State. "
            "He also holds offers from Oregon and Michigan 🏈")
    assert schools_of(text, schools) == []


def test_offer_named_right_after_phrase_still_counts(schools):
    text = "Blessed to receive my 9th offer from The University Of Tennessee #AGTG 🏈"
    assert schools_of(text, schools) == [("offer", "Tennessee")]


def test_blessed_to_receive_without_the_word_offer_is_not_an_offer(schools):
    text = "Blessed to receive Defensive MVP against Florida State University High school 🏈"
    assert schools_of(text, schools) == []


def test_official_visit_is_an_invite_not_an_offer(schools):
    text = "Blessed to receive an official visit to Notre Dame this weekend! 🏈"
    assert schools_of(text, schools) == []


def test_offer_emoji_spelling_still_counts(schools):
    text = "EXTREMELY BLESSED to receive an \U0001F17E️ffer from @Vol_Football 🏈"
    assert schools_of(text, schools) == [("offer", "Tennessee")]


# --- look-alike schools ----------------------------------------------------

def test_middle_tennessee_and_tennessee_valley_are_rejected(schools):
    assert schools_of("Blessed to receive an offer from Middle Tennessee! 🏈", schools) == []
    assert schools_of("Blessed to receive my 3rd offer from Tennessee Valley 🏈", schools) == []


def test_osu_alone_does_not_mean_ohio_state(schools):
    text = "Blessed to receive an offer from @FakeCowboysFB 🧡 thank you to the OSU staff 🏈"
    assert schools_of(text, schools) == []


# --- commits: the school must be what was committed to --------------------

def test_commit_to_something_other_than_a_school_is_dropped(schools):
    for text in (
        "Kentucky's win at Texas A&M sealed it: 2029 QB Fake Player commits to the Wildcats 🏈",
        "2027 Notre Dame commit LB Fake Player commits to the All-American Bowl!! 🏈",
        "Coach said the Sooners' lack of commitment to a clear RB1 comes from depth 🏈",
        "Here is the latest from Oregon DB commit Fake Player, he says what you want your commits to say 🏈",
    ):
        assert schools_of(text, schools) == [], text


def test_commit_to_a_tracked_school_still_counts(schools):
    text = "BREAKING: Class of 2028 LB Fake Player has Committed to Michigan 🏈"
    assert schools_of(text, schools) == [("commit", "Michigan")]


# --- other sports hidden in handles ---------------------------------------

def test_off_sport_word_inside_a_handle_drops_the_tweet(schools):
    text = "Super excited to announce my commitment to THE Ohio State University! @FakeHSBaseball12"
    assert schools_of(text, schools) == []


# --- who the player is ----------------------------------------------------

def test_reporter_credit_mentions_are_not_the_player():
    text = "Class of 2027 DL Fake “Tank” Player has Flipped his Commitment to Alabama, he tells me for @FakeOutlet"
    m = resolve_player_mention(text, ["FakeOutlet"], exclude_handles=[])
    assert (m.handle, m.name) == ("", "Fake “Tank” Player")


def test_more_from_reporter_mention_is_not_the_player():
    text = "2027 DL Fake Player flips to Alabama.  More from @FakeReporter:"
    m = resolve_player_mention(text, ["FakeReporter"], exclude_handles=[])
    assert (m.handle, m.name) == ("", "Fake Player")


def test_outlet_handles_are_never_the_player():
    text = "Texas made an early move on a top 2029 prospect. Fake Player visited and picked up an offer. @FakeHorns247"
    m = resolve_player_mention(text, ["FakeHorns247"], exclude_handles=[])
    assert (m.handle, m.name) == ("", "Fake Player")


def test_name_tagged_right_after_is_the_players_handle():
    text = "2028 LB Fake Player (@FakePlayer28) has committed to Oklahoma"
    m = resolve_player_mention(text, ["FakePlayer28"], exclude_handles=[])
    assert (m.handle, m.name) == ("FakePlayer28", "Fake Player")


def test_quoted_nickname_does_not_split_the_same_player():
    assert normalize_name("Fake “Tank” Player") == normalize_name("Fake Player")
    assert normalize_name('Fake "Tank" Player') == normalize_name("Fake Player")


def test_first_person_offer_with_ranking_bio_is_a_player():
    bio = "4⭐️/28’/WR/ESPN 300/6’1”/175lbs"
    assert classify_author(bio, text="Blessed to receive an offer from the University of Michigan.") == "player"


def test_short_year_before_apostrophe_is_a_class_year_only_when_glued():
    assert parse_class_year("5⭐️ 28’RIVALS #1 WR") == "2028"
    assert parse_class_year("4⭐️/28’/WR") == "2028"
    assert parse_class_year("Sports Journalist (in Training) | UCF 26' | 🇦🇺") == ""


def test_year_inside_a_date_is_not_a_class_year():
    assert parse_class_year("Classic game ~ Sept 6, 2026 | Fake Stadium") == ""
    assert parse_class_year("Kickoff 9/6/2026 at Fake Field") == ""
    assert parse_class_year("Kickoff September 2026") == ""
    assert parse_class_year("WR | Class of 2026 | Fakeville HS") == "2026"
    assert parse_class_year("QB 2027 | Fakeville HS") == "2027"


def test_event_promo_commitment_wording_is_not_a_commit(schools):
    text = ("From South Florida to the pros and a continued commitment to the next "
            "generation. 🏈 Join us at the Fake Kickoff Luncheon in Miami, FL")
    assert schools_of(text, schools) == []


def test_list_of_schools_that_have_offered_is_not_new(schools):
    text = ("Fake County (Ga.) 2029 DL Fake Player picked up an offer from FSU on his visit. "
            "\n\nAuburn, Georgia, Georgia Tech, Miami, Clemson, Oregon, Ohio State, Michigan "
            "and plenty of others have offered. 🏈")
    assert schools_of(text, schools) == [("offer", "Florida State")]


def test_offer_emoji_with_a_space_still_counts(schools):
    text = "I AM VERY BLESSED TO RECEIVE MY FIRST \U0001F17E\ufe0f FFER TO THE GEORGIA STATE UNIVERSITY 🏈"
    assert schools_of(text, schools) == [("offer", "Georgia State")]


def test_bare_school_offered_still_counts(schools):
    assert schools_of("Oklahoma offered #blessed 🏈", schools) == [("offer", "Oklahoma")]
