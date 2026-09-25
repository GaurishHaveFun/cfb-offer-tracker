from cfb_offers.queries import (
    ALL_PHRASES,
    COMMIT_PHRASES,
    DECOMMIT_PHRASES,
    MAX_QUERY_LEN,
    OFFER_PHRASES,
    build_all_queries,
    group_schools,
)


def test_every_query_within_max_len(schools):
    queries = build_all_queries(schools, since_days=30)
    for q in queries:
        assert len(q) <= MAX_QUERY_LEN


def test_every_school_alias_in_exactly_one_query(schools):
    groups = group_schools(schools, since_days=7)
    queries = build_all_queries(schools, since_days=7)
    assert len(groups) == len(queries)
    for school in schools:
        containing_groups = [g for g in groups if school in g]
        assert len(containing_groups) == 1, f"{school.name} appears in {len(containing_groups)} groups"
        (group,) = containing_groups
        idx = groups.index(group)
        q = queries[idx]
        for alias in school.aliases:
            assert alias in q


def test_all_phrase_types_present_in_every_query(schools):
    queries = build_all_queries(schools, since_days=7)
    for q in queries:
        assert '"offer from"' in q  # offer
        assert '"committed to"' in q  # commit
        assert '"decommit"' in q  # decommit


def test_redundant_phrases_are_dropped():
    # X matches whole phrases, so a longer phrase is redundant once its
    # substring phrase is already present.
    assert '"has received an offer"' not in ALL_PHRASES
    assert '"received an offer"' in ALL_PHRASES
    # decommit/decommitted are kept - X doesn't prefix-match.
    assert '"decommit"' in DECOMMIT_PHRASES
    assert '"decommitted"' in DECOMMIT_PHRASES


def test_bare_committed_replaced_by_tighter_phrases():
    # Bare "committed" matched too much non-recruiting noise ("committed 19
    # errors"); every commit phrase now requires "to" or an unambiguous
    # variant.
    assert '"committed"' not in ALL_PHRASES
    assert '"committed to"' in COMMIT_PHRASES
    assert '"has committed"' in COMMIT_PHRASES
    assert "#Committed" in COMMIT_PHRASES
    assert '"100% committed"' in COMMIT_PHRASES


def test_agtg_removed_as_standalone_offer_phrase():
    assert "#AGTG" not in ALL_PHRASES


def test_negative_sport_terms_present_where_they_fit(schools):
    # At 26 schools, groups pack closer to MAX_QUERY_LEN than the original
    # 15-school config did, so there's rarely room left for every negative
    # term (basketball is prioritized first and always fits; the later,
    # lower-priority terms like gymnastics may not fit in any group).
    queries = build_all_queries(schools, since_days=7)
    assert all("-basketball" in q for q in queries)
    assert any("-softball" in q for q in queries)


def test_offer_commit_decommit_phrases_combined_into_one_clause():
    for phrase in OFFER_PHRASES + COMMIT_PHRASES + DECOMMIT_PHRASES:
        assert phrase in ALL_PHRASES


def test_query_count_is_about_thirteen_for_default_config(schools):
    # 26 schools packed several-per-query instead of 78 (26 x 3 event
    # types) - length constraints keep it from hitting exactly 5 like the
    # original 15-school config did, but it should still be a modest
    # handful, not one-per-school-per-event-type.
    queries = build_all_queries(schools, since_days=7)
    assert 1 <= len(queries) <= 20
    assert len(queries) < len(schools)


def test_group_schools_never_exceeds_max_len(schools):
    groups = group_schools(schools, since_days=7)
    total_schools = sum(len(g) for g in groups)
    assert total_schools == len(schools)
    # every school appears in exactly one group
    names = [s.name for g in groups for s in g]
    assert sorted(names) == sorted(s.name for s in schools)


def test_query_includes_since_and_filter(schools):
    q = build_all_queries(schools, since_days=7)[0]
    assert "-filter:retweets" in q
    assert "since:" in q


def test_multi_word_aliases_are_quoted(schools):
    joined = " ".join(build_all_queries(schools, since_days=2))
    multi = [a for s in schools for a in s.aliases if " " in a]
    assert multi
    for alias in multi:
        assert f'"{alias}"' in joined
