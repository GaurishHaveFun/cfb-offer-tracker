"""gspread storage: read existing event_keys for dedupe, append/update rows.

The sheet is private but this repo is public, so nothing here ever logs
tweet text or player info - callers only print counts. Values are always
written with value_input_option='RAW' so a tweet starting with '=', '+', or
'-' can never be interpreted as a formula (formula-injection guard).
"""
from __future__ import annotations

import json
import time

import gspread
from gspread.exceptions import APIError, WorksheetNotFound

from cfb_offers.dedupe import add_source
from cfb_offers.models import OfferRecord

HEADER = OfferRecord.columns()
WORKSHEET_NAME = "offers"
# Rows removed by --prune-sheet are moved here (never just deleted), with
# when and why, so any prune can be reviewed and undone by hand.
PRUNED_WORKSHEET_NAME = "pruned"
PRUNED_HEADER = HEADER + ["pruned_at", "prune_reason"]

# Retry gspread calls that fail with a rate-limit (429) or server error
# (5xx) response - both are transient and worth a backoff-and-retry instead
# of failing the whole run.
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_BASE_DELAY_SECONDS = 1.0


class SheetSchemaError(RuntimeError):
    """Raised when the sheet's existing header row doesn't match OfferRecord."""


class SheetAccessError(RuntimeError):
    """Raised when the sheet can't be opened / the service account lacks access."""


def _with_retry(func, *args, **kwargs):
    """Calls func(*args, **kwargs), retrying on APIError 429/5xx with
    exponential backoff. Re-raises immediately on any other error.
    """
    delay = _BASE_DELAY_SECONDS
    for attempt in range(_MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except APIError as e:
            code = getattr(e, "code", None)
            if code not in _RETRY_STATUS_CODES or attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2
    # unreachable, but keeps type-checkers happy
    return func(*args, **kwargs)


def _service_account_email(service_account_json: str) -> str:
    try:
        return json.loads(service_account_json).get("client_email", "<unknown>")
    except (json.JSONDecodeError, AttributeError):
        return "<unknown>"


def _open_worksheet(gc: gspread.Client, sheet_id: str, sa_email: str) -> gspread.Worksheet:
    try:
        sh = _with_retry(gc.open_by_key, sheet_id)
    except APIError as e:
        code = getattr(e, "code", None)
        if code in (403, 404):
            raise SheetAccessError(
                f"cannot open sheet {sheet_id!r} (HTTP {code}). Make sure the "
                f"sheet exists and is shared as Editor with the service "
                f"account: {sa_email}"
            ) from e
        raise

    try:
        ws = _with_retry(sh.worksheet, WORKSHEET_NAME)
    except WorksheetNotFound:
        ws = _with_retry(sh.add_worksheet, title=WORKSHEET_NAME, rows=1000, cols=len(HEADER))
    return ws


def _ensure_header(ws: gspread.Worksheet) -> None:
    """Creates the header row if the worksheet is empty; otherwise verifies
    the existing header row matches OfferRecord's schema exactly, raising
    SheetSchemaError if it doesn't.
    """
    values = _with_retry(ws.get_all_values)
    if not values or not values[0]:
        _with_retry(ws.append_rows, [HEADER], value_input_option="RAW")
        _with_retry(ws.freeze, rows=1)
        return

    existing_header = values[0]
    if existing_header != HEADER:
        raise SheetSchemaError(
            "sheet header does not match the OfferRecord schema.\n"
            f"  expected: {HEADER}\n"
            f"  found:    {existing_header}\n"
            "Fix the sheet's header row (or the schema) before running again."
        )
    # Freezing is idempotent - harmless to call every time, and it heals a
    # sheet that had its freeze cleared out from under it.
    _with_retry(ws.freeze, rows=1)


def open_sheet(service_account_json: str, sheet_id: str) -> gspread.Worksheet:
    creds = json.loads(service_account_json)
    sa_email = creds.get("client_email", "<unknown>")
    gc = gspread.service_account_from_dict(creds)
    ws = _open_worksheet(gc, sheet_id, sa_email)
    _ensure_header(ws)
    return ws


def load_existing_event_keys(ws: gspread.Worksheet) -> dict[str, int]:
    """Returns {event_key: row_number} for every row already in the sheet
    (row_number is 1-indexed, including the header row).
    """
    values = _with_retry(ws.get_all_values)
    if not values:
        return {}
    header = values[0]
    key_col = header.index("event_key")
    out = {}
    for i, row in enumerate(values[1:], start=2):
        if key_col < len(row) and row[key_col]:
            out[row[key_col]] = i
    return out


def latest_tweet_date(ws: gspread.Worksheet) -> str | None:
    values = _with_retry(ws.get_all_values)
    if len(values) <= 1:
        return None
    header = values[0]
    date_col = header.index("tweet_date")
    dates = [row[date_col] for row in values[1:] if date_col < len(row) and row[date_col]]
    return max(dates) if dates else None


def sync_records(ws: gspread.Worksheet, records: list[OfferRecord]) -> tuple[int, int]:
    """Appends brand-new events, and folds a new source into `also_reported_by`
    for events that already exist. Returns (appended_count, updated_count).
    """
    existing = load_existing_event_keys(ws)
    header = ws.row_values(1)
    also_col_idx = header.index("also_reported_by") + 1  # gspread cols are 1-indexed
    source_handle_col_idx = header.index("source_handle")

    to_append = []
    also_updates: list[dict] = []  # {"range": "H5", "values": [["a, b"]]}
    updated = 0
    for record in records:
        row_num = existing.get(record.event_key)
        if row_num is None:
            to_append.append(record.as_row())
            existing[record.event_key] = -1  # dedupe within this batch too
        else:
            if row_num == -1:
                continue
            current_row = ws.row_values(row_num)
            current_also = current_row[also_col_idx - 1] if len(current_row) >= also_col_idx else ""
            current_source_handle = (
                current_row[source_handle_col_idx] if len(current_row) > source_handle_col_idx else ""
            )
            if record.source_handle and record.source_handle != current_source_handle:
                new_also = add_source(current_also, record.source_handle)
                if new_also != current_also:
                    a1 = gspread.utils.rowcol_to_a1(row_num, also_col_idx)
                    also_updates.append({"range": a1, "values": [[new_also]]})
                    updated += 1

    if also_updates:
        _with_retry(ws.batch_update, also_updates, value_input_option="RAW")

    if to_append:
        _with_retry(ws.append_rows, to_append, value_input_option="RAW")
    return len(to_append), updated


def read_rows(ws: gspread.Worksheet) -> list[tuple[int, dict[str, str]]]:
    """Every data row as (sheet_row_number, {column: value}); row numbers
    are 1-indexed and count the header row."""
    values = _with_retry(ws.get_all_values)
    if len(values) <= 1:
        return []
    header = values[0]
    out = []
    for i, row in enumerate(values[1:], start=2):
        row = row + [""] * (len(header) - len(row))
        out.append((i, dict(zip(header, row))))
    return out


def _pruned_worksheet(ws: gspread.Worksheet) -> gspread.Worksheet:
    sh = ws.spreadsheet
    try:
        pruned = _with_retry(sh.worksheet, PRUNED_WORKSHEET_NAME)
    except WorksheetNotFound:
        pruned = _with_retry(
            sh.add_worksheet, title=PRUNED_WORKSHEET_NAME, rows=1000, cols=len(PRUNED_HEADER)
        )
    if not _with_retry(pruned.row_values, 1):
        _with_retry(pruned.append_rows, [PRUNED_HEADER], value_input_option="RAW")
        _with_retry(pruned.freeze, rows=1)
    return pruned


def update_also_reported_by(ws: gspread.Worksheet, updates: dict[int, str]) -> None:
    """Sets also_reported_by on the given rows ({row_number: value}) in one batch."""
    col = HEADER.index("also_reported_by") + 1
    data = [
        {"range": gspread.utils.rowcol_to_a1(row_num, col), "values": [[value]]}
        for row_num, value in updates.items()
    ]
    _with_retry(ws.batch_update, data, value_input_option="RAW")


def move_to_pruned(
    ws: gspread.Worksheet,
    rows: list[tuple[int, dict[str, str], str]],
    pruned_at: str,
) -> None:
    """Copies each (row_number, row, reason) to the `pruned` tab, then - only
    once that copy has succeeded - deletes those rows from `ws` in a single
    batch request (bottom-up, so earlier deletions don't shift later ones)."""
    if not rows:
        return
    pruned = _pruned_worksheet(ws)
    _with_retry(
        pruned.append_rows,
        [[row.get(c, "") for c in HEADER] + [pruned_at, reason] for _, row, reason in rows],
        value_input_option="RAW",
    )
    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": row_num - 1,
                    "endIndex": row_num,
                }
            }
        }
        for row_num in sorted({r for r, _, _ in rows}, reverse=True)
    ]
    _with_retry(ws.spreadsheet.batch_update, {"requests": requests})


def check_sheet(service_account_json: str, sheet_id: str) -> None:
    """Diagnostic for `python -m cfb_offers --check-sheet`: verifies the
    service account can authenticate, open the sheet, and read/write it, and
    that the header matches the schema. Never needs X_COOKIES. Writes and
    then deletes a throwaway test row. Prints counts only - never sheet
    content, matching the rest of the tool's no-PII-in-logs policy.
    """
    sa_email = _service_account_email(service_account_json)
    print(f"service account: {sa_email}")

    creds = json.loads(service_account_json)
    gc = gspread.service_account_from_dict(creds)
    ws = _open_worksheet(gc, sheet_id, sa_email)
    print(f"sheet reachable: worksheet {ws.title!r} opened")

    _ensure_header(ws)
    print("header: OK (matches OfferRecord schema)")

    values = _with_retry(ws.get_all_values)
    row_count = max(len(values) - 1, 0)
    print(f"existing rows: {row_count}")

    test_row = ["__check_sheet_test__"] + [""] * (len(HEADER) - 1)
    resp = _with_retry(ws.append_rows, [test_row], value_input_option="RAW")
    try:
        updated_range = resp.get("updates", {}).get("updatedRange", "")
        test_row_num = int(updated_range.split("!")[-1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    except (ValueError, IndexError, AttributeError):
        test_row_num = len(values) + 1
    _with_retry(ws.delete_rows, test_row_num)
    print("write/delete test row: OK (editor access confirmed)")
    print("check_sheet: PASSED")
