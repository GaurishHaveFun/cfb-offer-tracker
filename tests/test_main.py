"""Tests for the pure since-window / page-cap selection logic in main.py.

No network, no sheet, no twscrape - resolve_window/resolve_max_pages take
plain values and return plain values.
"""
import datetime as dt

from cfb_offers.client import PAGES_PER_QUERY_BACKFILL, PAGES_PER_QUERY_DEFAULT
from cfb_offers.main import BACKFILL_DAYS, CI_MAX_SINCE_DAYS, resolve_max_pages, resolve_window

NOW = dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc)


def test_explicit_since_days_always_wins():
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=5,
        backfill_flag=True,
        ci=True,
        latest_tweet_date=None,
        now=NOW,
    )
    assert (since_days, is_backfill, warning) == (5, False, None)


def test_backfill_flag_forces_30_day_window_locally():
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=None,
        backfill_flag=True,
        ci=False,
        latest_tweet_date="2026-08-01T00:00:00Z",
        now=NOW,
    )
    assert since_days == BACKFILL_DAYS
    assert is_backfill is True
    assert warning is None


def test_backfill_flag_is_ignored_in_ci_and_gets_clamped():
    # CI never does the 30-day backfill, even if --backfill is passed.
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=None,
        backfill_flag=True,
        ci=True,
        latest_tweet_date=None,  # empty sheet
        now=NOW,
    )
    assert since_days == CI_MAX_SINCE_DAYS
    assert is_backfill is False
    assert warning is not None


def test_empty_sheet_outside_ci_backfills_normally():
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=None,
        backfill_flag=False,
        ci=False,
        latest_tweet_date=None,
        now=NOW,
    )
    assert since_days == BACKFILL_DAYS
    assert is_backfill is False
    assert warning is None


def test_empty_sheet_in_ci_is_clamped_with_warning():
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=None,
        backfill_flag=False,
        ci=True,
        latest_tweet_date=None,
        now=NOW,
    )
    assert since_days == CI_MAX_SINCE_DAYS
    assert is_backfill is False
    assert warning is not None
    assert "backfill" in warning


def test_small_gap_in_ci_is_not_clamped():
    latest = (NOW - dt.timedelta(days=1)).isoformat().replace("+00:00", "Z")
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=None,
        backfill_flag=False,
        ci=True,
        latest_tweet_date=latest,
        now=NOW,
    )
    assert since_days <= CI_MAX_SINCE_DAYS
    assert warning is None


def test_large_gap_in_ci_is_clamped_with_warning():
    latest = (NOW - dt.timedelta(days=10)).isoformat().replace("+00:00", "Z")
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=None,
        backfill_flag=False,
        ci=True,
        latest_tweet_date=latest,
        now=NOW,
    )
    assert since_days == CI_MAX_SINCE_DAYS
    assert is_backfill is False
    assert warning is not None


def test_large_gap_outside_ci_is_not_clamped():
    latest = (NOW - dt.timedelta(days=10)).isoformat().replace("+00:00", "Z")
    since_days, is_backfill, warning = resolve_window(
        explicit_since_days=None,
        backfill_flag=False,
        ci=False,
        latest_tweet_date=latest,
        now=NOW,
    )
    assert since_days == 11
    assert warning is None


def test_resolve_max_pages_explicit_override_wins():
    assert resolve_max_pages(7, is_backfill=True) == 7
    assert resolve_max_pages(7, is_backfill=False) == 7


def test_resolve_max_pages_default_vs_backfill():
    assert resolve_max_pages(None, is_backfill=False) == PAGES_PER_QUERY_DEFAULT
    assert resolve_max_pages(None, is_backfill=True) == PAGES_PER_QUERY_BACKFILL
