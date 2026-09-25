"""run() saves to the sheet after every query, so a mid-run failure keeps
everything already scraped. All network pieces are faked."""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from cfb_offers import main
from cfb_offers.models import OfferRecord


def _tweet(tid: str):
    user = SimpleNamespace(
        username=f"FakeRecruit{tid}", displayname="Fake Recruit",
        rawDescription="c/o 2028 WR", location="",
    )
    return SimpleNamespace(
        id_str=tid, rawContent=f"offer tweet {tid}", user=user, mentionedUsers=[],
        date=dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc),
    )


def _record(tid: str) -> OfferRecord:
    return OfferRecord(
        event_key=f"fakerecruit{tid}|oregon|offer", event_type="offer", is_flip=False,
        school="Oregon", player_name="Fake Recruit", player_handle=f"FakeRecruit{tid}",
        class_year="2028", position="WR", height="", weight="", high_school="", state="",
        source_type="player", source_handle=f"FakeRecruit{tid}", tweet_id=tid,
        tweet_date="2026-09-20T00:00:00+00:00", tweet_url="", tweet_text="",
    )


class FakeAPI:
    def __init__(self, pages, fail_on=None):
        self.pages = pages  # query -> list of tweets
        self.fail_on = fail_on
        self.pool = object()

    async def search(self, query, limit):
        if query == self.fail_on:
            raise RuntimeError("simulated crash mid-backfill")
        for t in self.pages[query]:
            yield t


@pytest.fixture
def harness(monkeypatch):
    synced: list[list[str]] = []

    async def fake_process_tweet(**kw):
        return "event", [_record(kw["tweet_id"])], None

    async def noop(*a, **k):
        return None

    monkeypatch.setenv("X_COOKIES", "auth_token=x; ct0=y")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("SHEET_ID", "sheet")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(main, "build_all_queries", lambda schools, days: ["q1", "q2", "q3"])
    monkeypatch.setattr(main, "process_tweet", fake_process_tweet)
    monkeypatch.setattr(main, "jitter", noop)
    monkeypatch.setattr(main, "require_active_accounts", noop)
    monkeypatch.setattr(main.sheets, "open_sheet", lambda *a: "ws")
    monkeypatch.setattr(main.sheets, "latest_tweet_date", lambda ws: None)
    monkeypatch.setattr(
        main.sheets, "sync_records",
        lambda ws, recs: (synced.append([r.tweet_id for r in recs]), (len(recs), 0))[1],
    )

    def use_api(api):
        async def fake_build_api(cookies):
            return api
        monkeypatch.setattr(main, "build_api", fake_build_api)

    return synced, use_api


PAGES = {"q1": [_tweet("1"), _tweet("2")], "q2": [_tweet("2"), _tweet("3")], "q3": [_tweet("4")]}


def test_syncs_after_each_query_and_skips_repeat_tweets(harness):
    synced, use_api = harness
    use_api(FakeAPI(PAGES))
    asyncio.run(main.run(["--backfill"]))
    # tweet 2 surfaced in q1 and q2 but is only processed/synced once
    assert synced == [["1", "2"], ["3"], ["4"]]


def test_crash_mid_run_keeps_earlier_queries_saved(harness, tmp_path):
    synced, use_api = harness
    use_api(FakeAPI(PAGES, fail_on="q3"))
    dump = tmp_path / "raw.jsonl"
    with pytest.raises(RuntimeError):
        asyncio.run(main.run(["--backfill", "--dump-raw", str(dump)]))
    assert synced == [["1", "2"], ["3"]]
    assert [json.loads(line)["id"] for line in dump.read_text().splitlines()] == ["1", "2", "3"]
