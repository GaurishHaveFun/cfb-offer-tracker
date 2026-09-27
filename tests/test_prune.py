"""--prune-sheet: which rows get pruned, and that pruned rows are copied to
the 'pruned' tab before they're removed. Fake players/handles; no network."""
import pytest
from gspread.exceptions import WorksheetNotFound

from cfb_offers import sheets
from cfb_offers.main import plan_prune, plan_resync
from cfb_offers.models import OfferRecord


def _rec(tweet_id, school="Alabama", event_type="commit", key="fake player|alabama|commit"):
    return OfferRecord(
        event_key=key, event_type=event_type, is_flip=False, school=school,
        player_name="Fake Player", player_handle="", class_year="", position="",
        height="", weight="", high_school="", state="", source_type="reporter",
        source_handle=f"Reporter{tweet_id}", tweet_id=tweet_id,
        tweet_date=f"2026-09-2{tweet_id}T00:00:00+00:00", tweet_url="", tweet_text="",
    )


def _row(tweet_id, school="Alabama", event_type="commit", key="old-key", also=""):
    return {
        "tweet_id": tweet_id, "school": school, "event_type": event_type, "event_key": key,
        "tweet_date": f"2026-09-2{tweet_id}T00:00:00+00:00",
        "source_handle": f"Reporter{tweet_id}", "also_reported_by": also,
    }


def test_rows_the_rules_now_reject_are_pruned():
    rows = [(2, _row("1")), (3, _row("2", school="LSU", event_type="offer"))]
    prune, also = plan_prune(rows, [_rec("1")], known_tweet_ids={"1", "2"})
    assert [(n, reason) for n, _, reason in prune] == [(3, "rejected by current rules")]
    assert also == {}


def test_rows_for_tweets_not_in_the_raw_file_are_left_alone():
    rows = [(2, _row("1")), (3, _row("9"))]
    prune, _ = plan_prune(rows, [_rec("1")], known_tweet_ids={"1"})
    assert prune == []


def test_same_event_reported_twice_keeps_earliest_and_merges_source():
    rows = [(2, _row("3", key="rivals|alabama|commit")), (3, _row("1", key="outlet|alabama|commit"))]
    prune, also = plan_prune(rows, [_rec("1"), _rec("3")], known_tweet_ids={"1", "3"})
    assert [(n, reason) for n, _, reason in prune] == [(2, "duplicate of tweet 1")]
    assert also == {3: "Reporter3"}


class FakePrunedTab:
    row_count = 1000

    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def row_values(self, n):
        return self.rows[n - 1] if len(self.rows) >= n else []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_rows(self, rows, value_input_option=None):
        self.rows.extend(rows)

    def update(self, rows, rng, value_input_option=None):
        if self.fail:
            raise RuntimeError("write failed")
        assert rng == f"A{len(self.rows) + 1}"
        self.rows.extend(rows)

    def freeze(self, rows=None):
        pass


class FakeSpreadsheet:
    def __init__(self, pruned):
        self.pruned, self.batches = pruned, []

    def worksheet(self, name):
        if self.pruned is None:
            raise WorksheetNotFound(name)
        return self.pruned

    def add_worksheet(self, title, rows, cols):
        self.pruned = FakePrunedTab()
        return self.pruned

    def batch_update(self, body):
        self.batches.append(body)


class FakeOffersTab:
    id = 0

    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet


def test_move_copies_to_pruned_tab_then_deletes_bottom_up():
    sh = FakeSpreadsheet(pruned=None)
    ws = FakeOffersTab(sh)
    sheets.move_to_pruned(ws, [(2, _row("1"), "rejected"), (5, _row("2"), "duplicate")], "2026-09-25T00:00:00+00:00")

    assert sh.pruned.rows[0] == sheets.PRUNED_HEADER
    assert sh.pruned.rows[1][-2:] == ["2026-09-25T00:00:00+00:00", "rejected"]
    assert len(sh.pruned.rows) == 3
    starts = [r["deleteDimension"]["range"]["startIndex"] for r in sh.batches[0]["requests"]]
    assert starts == [4, 1]  # row 5 then row 2 (0-indexed), bottom-up


def test_rows_are_not_deleted_if_copying_to_pruned_tab_fails():
    sh = FakeSpreadsheet(pruned=FakePrunedTab(fail=True))
    sh.pruned.rows = [sheets.PRUNED_HEADER]
    with pytest.raises(RuntimeError):
        sheets.move_to_pruned(FakeOffersTab(sh), [(2, _row("1"), "rejected")], "now")
    assert sh.batches == []


def test_wrong_school_row_is_pruned_and_the_corrected_one_added():
    # the sheet credited the commit to Penn State; current rules say Auburn
    rows = [(2, _row("1", school="Penn State", key="fake player|penn state|commit"))]
    fixed = _rec("1", school="Auburn", key="fake player|auburn|commit")
    plan = plan_resync(rows, [fixed], known_tweet_ids={"1"})
    assert [(n, reason) for n, _, reason in plan.prune] == [(2, "rejected by current rules")]
    assert [r.event_key for r in plan.adds] == ["fake player|auburn|commit"]


def test_events_missing_from_the_sheet_are_added_once():
    rows = [(2, _row("1", key="fake player|alabama|commit"))]
    replayed = [_rec("1"), _rec("2", school="Kentucky", event_type="offer", key="other|kentucky|offer"),
                _rec("3", school="Kentucky", event_type="offer", key="other|kentucky|offer")]
    plan = plan_resync(rows, replayed, known_tweet_ids={"1", "2", "3"})
    assert plan.prune == []
    assert [(r.event_key, r.tweet_id) for r in plan.adds] == [("other|kentucky|offer", "2")]  # earliest report


def test_kept_rows_get_the_current_event_key():
    rows = [(2, _row("1", key="rivals|alabama|commit"))]
    plan = plan_resync(rows, [_rec("1")], known_tweet_ids={"1"})
    assert plan.key_updates == {2: "fake player|alabama|commit"}
    assert plan.adds == []  # the kept row covers the event under its new key


def test_rows_not_in_raw_file_block_duplicate_adds():
    rows = [(2, _row("9", key="fake player|alabama|commit"))]
    plan = plan_resync(rows, [_rec("1")], known_tweet_ids={"1"})
    assert plan.adds == []
