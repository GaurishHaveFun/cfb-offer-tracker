"""--backfill-days: week-sliced queries, progress labels, and resume."""
import asyncio
from datetime import date

import pytest

from cfb_offers import main
from cfb_offers.queries import MAX_QUERY_LEN, backfill_slices, build_slice_queries


def test_slices_cover_the_window_newest_first_without_gaps():
    slices = backfill_slices(90, 7, today=date(2026, 9, 25))
    assert slices[0] == (date(2026, 9, 19), date(2026, 9, 26))  # until: is exclusive -> includes today
    assert slices[-1][0] == date(2026, 6, 28)
    for (since, _), (_, until) in zip(slices, slices[1:]):
        assert until == since  # each window ends where the previous one began
    assert len(slices) == 13


def test_slice_queries_fit_and_carry_both_dates(schools):
    queries = build_slice_queries(schools, date(2026, 9, 1), date(2026, 9, 8))
    assert all(len(q) <= MAX_QUERY_LEN for q in queries)
    assert all("since:2026-09-01 until:2026-09-08" in q for q in queries)
    for school in schools:
        assert sum(school.aliases[0] in q for q in queries) >= 1


def test_plan_labels_and_resume(schools, monkeypatch):
    monkeypatch.setattr(main, "backfill_slices", lambda days: backfill_slices(days, 7, today=date(2026, 9, 25)))
    full = main.search_plan(schools, 21, 21)
    resumed = main.search_plan(schools, 21, 21, resume_from_slice=2)
    assert full[0][0].startswith("slice 1/3 (2026-09-19..2026-09-26) query 1/")
    assert resumed[0][0].startswith("slice 2/3 ")
    assert len(resumed) == len(full) * 2 // 3


def test_normal_runs_have_no_slices(schools):
    plan = main.search_plan(schools, 3, None)
    assert plan and all(label == "" and "until:" not in q for label, q in plan)


def test_backfill_days_refuses_to_run_in_ci(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(SystemExit):
        asyncio.run(main.run(["--backfill-days", "90"]))
