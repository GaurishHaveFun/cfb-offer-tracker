from cfb_offers.profile import (
    parse_bio,
    parse_class_year,
    parse_height,
    parse_high_school,
    parse_position,
    parse_state,
    parse_weight,
)


def test_parse_class_year_variants():
    assert parse_class_year("c/o 2027") == "2027"
    assert parse_class_year("Class of '27") == "2027"
    assert parse_class_year("’27 Athlete") == "2027"
    assert parse_class_year("2027 QB") == "2027"
    assert parse_class_year("no year here") == ""


def test_parse_position_including_combo():
    assert parse_position("QB | c/o 2027") == "QB"
    assert parse_position("WR/DB | c/o 2026") == "WR/DB"
    assert parse_position("nothing") == ""


def test_parse_height():
    assert parse_height("6'2 195lbs") == "6'2"
    assert parse_height("6-2, 195") == "6'2"
    assert parse_height("no height") == ""


def test_parse_weight():
    assert parse_weight("6'2 195lbs") == "195lbs"
    assert parse_weight("215 lbs") == "215lbs"
    assert parse_weight("no weight") == ""


def test_parse_high_school():
    assert parse_high_school("Westside High School | Atlanta, GA") == "Westside High School"
    assert parse_high_school("Norcross HS") == "Norcross HS"
    assert parse_high_school("IMG Academy") == "IMG Academy"
    assert parse_high_school("no school here") == ""


def test_parse_state_from_abbrev_and_name():
    assert parse_state("Atlanta, GA") == "GA"
    assert parse_state("Austin, Texas") == "TX"
    assert parse_state("nowhere") == ""


def test_parse_bio_full(tweets):
    author = tweets["player_offer"]["author"]
    bio = parse_bio(author["description"], author["location"])
    assert bio["class_year"] == "2027"
    assert bio["position"] == "QB"
    assert bio["height"] == "6'2"
    assert bio["weight"] == "195lbs"
    assert bio["high_school"] == "Westside HS"
    assert bio["state"] == "GA"


def test_parse_bio_missing_fields_are_blank():
    bio = parse_bio("just a person", "")
    assert bio == {
        "class_year": "",
        "position": "",
        "height": "",
        "weight": "",
        "high_school": "",
        "state": "",
    }
