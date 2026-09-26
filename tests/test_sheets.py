"""Tests for cfb_offers.sheets - all gspread calls are mocked, no network.

Covers: header bootstrap, the header-mismatch error, dedupe + append,
the batched also_reported_by update, RAW value_input_option, retry-on-429,
and the --check-sheet permission-error message.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from gspread.exceptions import APIError, WorksheetNotFound

from cfb_offers import sheets
from cfb_offers.models import OfferRecord

SA_JSON = json.dumps({"client_email": "bot@my-project.iam.gserviceaccount.com"})


def make_record(event_key="qb1|alabama|offer", source_handle="reporter1", also="") -> OfferRecord:
    return OfferRecord(
        event_key=event_key,
        event_type="offer",
        is_flip=False,
        school="Alabama",
        player_name="Some Player",
        player_handle="qb1",
        class_year="2027",
        position="QB",
        height="",
        weight="",
        high_school="",
        state="",
        source_type="reporter",
        source_handle=source_handle,
        tweet_id="1",
        tweet_date="2026-09-01T00:00:00Z",
        tweet_url="https://x.com/reporter1/status/1",
        tweet_text="offers Some Player",
        also_reported_by=also,
    )


def make_api_error(code: int, message: str = "boom") -> APIError:
    response = Mock()
    response.json.return_value = {"error": {"code": code, "message": message}}
    response.text = message
    return APIError(response)


class FakeWorksheet:
    """Minimal stand-in for gspread.Worksheet covering the calls sheets.py
    makes: get_all_values, append_rows, freeze, row_values, batch_update,
    delete_rows.
    """

    def __init__(self, rows: list[list[str]] | None = None, title: str = "offers"):
        self.rows = rows or []  # includes header as rows[0] if present
        self.title = title
        self.freeze_calls = 0
        self.append_calls: list[tuple] = []
        self.batch_update_calls: list[tuple] = []
        self.delete_rows_calls: list[int] = []
        self._fail_next: dict[str, list[Exception]] = {}

    def fail_next(self, method: str, exc: Exception, times: int = 1) -> None:
        self._fail_next.setdefault(method, []).extend([exc] * times)

    def _maybe_fail(self, method: str) -> None:
        queue = self._fail_next.get(method)
        if queue:
            raise queue.pop(0)

    def get_all_values(self):
        self._maybe_fail("get_all_values")
        return [list(r) for r in self.rows]

    def append_rows(self, values, value_input_option=None):
        self._maybe_fail("append_rows")
        self.append_calls.append((values, value_input_option))
        start = len(self.rows) + 1
        for v in values:
            self.rows.append(list(v))
        end = len(self.rows)
        return {"updates": {"updatedRange": f"offers!A{start}:Z{end}"}}

    def freeze(self, rows=None, cols=None):
        self._maybe_fail("freeze")
        self.freeze_calls += 1
        return {}

    def row_values(self, row):
        self._maybe_fail("row_values")
        idx = row - 1
        return list(self.rows[idx]) if idx < len(self.rows) else []

    def batch_update(self, data, value_input_option=None):
        self._maybe_fail("batch_update")
        self.batch_update_calls.append((data, value_input_option))
        for item in data:
            rng = item["range"]
            col_letters = "".join(c for c in rng if c.isalpha())
            row_num = int("".join(c for c in rng if c.isdigit()))
            col_idx = 0
            for c in col_letters:
                col_idx = col_idx * 26 + (ord(c) - ord("A") + 1)
            row_idx = row_num - 1
            while len(self.rows[row_idx]) < col_idx:
                self.rows[row_idx].append("")
            self.rows[row_idx][col_idx - 1] = item["values"][0][0]
        return {}

    def update(self, values, rng="A1", value_input_option=None):
        assert rng == "A1"
        for i, v in enumerate(values):
            if i < len(self.rows):
                self.rows[i] = list(v)
            else:
                self.rows.append(list(v))
        return {}

    def delete_rows(self, start_index, end_index=None):
        self._maybe_fail("delete_rows")
        self.delete_rows_calls.append(start_index)
        del self.rows[start_index - 1]
        return {}


class FakeSpreadsheet:
    def __init__(self, worksheet: FakeWorksheet | None = None, worksheet_not_found=False):
        self._worksheet = worksheet
        self._worksheet_not_found = worksheet_not_found
        self.added: list[FakeWorksheet] = []

    def worksheet(self, title):
        if self._worksheet_not_found or self._worksheet is None:
            raise WorksheetNotFound(title)
        return self._worksheet

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title=title)
        self._worksheet = ws
        self.added.append(ws)
        return ws


class FakeClient:
    def __init__(self, spreadsheet=None, open_error: Exception | None = None):
        self._spreadsheet = spreadsheet
        self._open_error = open_error
        self.open_calls = 0

    def open_by_key(self, sheet_id):
        self.open_calls += 1
        if self._open_error:
            raise self._open_error
        return self._spreadsheet


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    # retries would otherwise really sleep (1s, 2s, ...) - keep tests fast.
    monkeypatch.setattr(sheets.time, "sleep", lambda _: None)


def _patch_client(monkeypatch, client: FakeClient):
    monkeypatch.setattr(sheets.gspread, "service_account_from_dict", lambda creds: client)


# --- header bootstrap -------------------------------------------------


def test_open_sheet_creates_header_on_empty_sheet(monkeypatch):
    ws = FakeWorksheet(rows=[])
    _patch_client(monkeypatch, FakeClient(FakeSpreadsheet(ws)))

    result = sheets.open_sheet(SA_JSON, "sheet123")

    assert result is ws
    assert ws.rows[0] == sheets.HEADER
    assert ws.append_calls[0][1] == "RAW"
    assert ws.freeze_calls == 1


def test_open_sheet_creates_missing_offers_worksheet(monkeypatch):
    sh = FakeSpreadsheet(worksheet_not_found=True)
    _patch_client(monkeypatch, FakeClient(sh))

    result = sheets.open_sheet(SA_JSON, "sheet123")

    assert result.title == "offers"
    assert len(sh.added) == 1
    assert result.rows[0] == sheets.HEADER


# --- header mismatch ----------------------------------------------------


def test_open_sheet_raises_on_header_mismatch(monkeypatch):
    bad_header = ["event_key", "event_type", "school"]  # not the real schema
    ws = FakeWorksheet(rows=[bad_header])
    _patch_client(monkeypatch, FakeClient(FakeSpreadsheet(ws)))

    with pytest.raises(sheets.SheetSchemaError) as exc_info:
        sheets.open_sheet(SA_JSON, "sheet123")

    assert "does not match" in str(exc_info.value)


# --- dedupe + append ------------------------------------------------------


def test_sync_records_appends_new_event():
    ws = FakeWorksheet(rows=[sheets.HEADER])
    record = make_record()

    appended, updated = sheets.sync_records(ws, [record])

    assert (appended, updated) == (1, 0)
    assert ws.rows[1][0] == record.event_key


def test_sync_records_skips_duplicate_event_key_within_batch():
    ws = FakeWorksheet(rows=[sheets.HEADER])
    r1 = make_record(source_handle="reporter1")
    r2 = make_record(source_handle="reporter1")  # same key, same source

    appended, updated = sheets.sync_records(ws, [r1, r2])

    assert appended == 1
    assert len(ws.rows) == 2  # header + one row


# --- batched also_reported_by update --------------------------------------


def test_sync_records_batches_also_reported_by_updates():
    header = sheets.HEADER
    existing_row = make_record(source_handle="reporter1").as_row()
    ws = FakeWorksheet(rows=[header, existing_row])

    r_new_source_1 = make_record(source_handle="reporter2")
    updates = [
        make_record(event_key="qb1|alabama|offer", source_handle="reporter2"),
    ]
    # add a second, distinct event that also needs an also_reported_by update
    second_existing = make_record(event_key="wr1|georgia|offer", source_handle="reporterA").as_row()
    ws.rows.append(second_existing)
    updates.append(make_record(event_key="wr1|georgia|offer", source_handle="reporterB"))

    appended, updated = sheets.sync_records(ws, updates)

    assert appended == 0
    assert updated == 2
    # exactly one batch_update call covering both cell writes
    assert len(ws.batch_update_calls) == 1
    data, value_input_option = ws.batch_update_calls[0]
    assert len(data) == 2
    assert value_input_option == "RAW"


# --- RAW value_input_option -------------------------------------------------


def test_append_rows_uses_raw_value_input_option():
    ws = FakeWorksheet(rows=[sheets.HEADER])
    formula_like = make_record(event_key="k2", source_handle="rep")
    formula_like.tweet_text = "=cmd|'/C calc'!A1"

    sheets.sync_records(ws, [formula_like])

    assert ws.append_calls[-1][1] == "RAW"
    # the raw formula-looking text made it into the row unmolested
    assert "=cmd" in ws.rows[-1][sheets.HEADER.index("tweet_text")]


# --- retry on 429 --------------------------------------------------------


def test_get_all_values_retries_on_429_then_succeeds():
    ws = FakeWorksheet(rows=[sheets.HEADER])
    ws.fail_next("get_all_values", make_api_error(429), times=2)

    result = sheets._with_retry(ws.get_all_values)

    assert result == [sheets.HEADER]


def test_with_retry_raises_immediately_on_non_retryable_error():
    ws = FakeWorksheet(rows=[sheets.HEADER])
    ws.fail_next("get_all_values", make_api_error(400, "bad request"))

    with pytest.raises(APIError):
        sheets._with_retry(ws.get_all_values)


def test_with_retry_gives_up_after_max_retries(monkeypatch):
    ws = FakeWorksheet(rows=[sheets.HEADER])
    ws.fail_next("get_all_values", make_api_error(500), times=sheets._MAX_RETRIES)

    with pytest.raises(APIError):
        sheets._with_retry(ws.get_all_values)


# --- --check-sheet permission error message --------------------------------


def test_check_sheet_reports_clear_error_on_403(monkeypatch, capsys):
    client = FakeClient(open_error=make_api_error(403, "The caller does not have permission"))
    _patch_client(monkeypatch, client)

    with pytest.raises(sheets.SheetAccessError) as exc_info:
        sheets.check_sheet(SA_JSON, "sheet123")

    message = str(exc_info.value)
    assert "bot@my-project.iam.gserviceaccount.com" in message
    assert "Editor" in message or "share" in message.lower()


def test_check_sheet_passes_and_prints_counts(monkeypatch, capsys):
    ws = FakeWorksheet(rows=[sheets.HEADER, make_record().as_row()])
    _patch_client(monkeypatch, FakeClient(FakeSpreadsheet(ws)))

    sheets.check_sheet(SA_JSON, "sheet123")

    out = capsys.readouterr().out
    assert "check_sheet: PASSED" in out
    assert "existing rows: 1" in out
    # the test row was appended then deleted again
    assert not any(row and row[0] == "__check_sheet_test__" for row in ws.rows)
    assert ws.delete_rows_calls


def test_blank_header_cell_is_restored_not_fatal():
    blanked = [""] + sheets.HEADER[1:]
    ws = FakeWorksheet(rows=[blanked, ["k1"] + [""] * (len(sheets.HEADER) - 1)])
    sheets._ensure_header(ws)
    assert ws.rows[0] == sheets.HEADER
    assert ws.rows[1][0] == "k1"  # data untouched


def test_existing_keys_are_found_even_if_header_is_blanked_mid_run():
    ws = FakeWorksheet(rows=[[""] + sheets.HEADER[1:], ["k1"], ["k2"]])
    assert sheets.load_existing_event_keys(ws) == {"k1": 2, "k2": 3}
