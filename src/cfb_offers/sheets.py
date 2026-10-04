"""gspread storage: read existing event_keys for dedupe, append/update rows.

The sheet is private but this repo is public, so nothing here ever logs
tweet text or player info - callers only print counts. Values are always
written with value_input_option='RAW' so a tweet starting with '=', '+', or
'-' can never be interpreted as a formula (formula-injection guard).
"""
from __future__ import annotations

import json
import sys
import time

import gspread
import requests
from gspread.exceptions import APIError, WorksheetNotFound

from dataclasses import replace

from cfb_offers.dedupe import add_source, canonical_key
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
_REQUEST_TIMEOUT_SECONDS = 60


class SheetSchemaError(RuntimeError):
    """Raised when the sheet's existing header row doesn't match OfferRecord."""


class SheetAccessError(RuntimeError):
    """Raised when the sheet can't be opened / the service account lacks access."""


def _with_retry(func, *args, **kwargs):
    """Calls func(*args, **kwargs), retrying on APIError 429/5xx and on
    dropped/timed-out connections, with exponential backoff. Re-raises
    immediately on any other error.
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
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            # A connection left idle during a long X rate-limit wait can be
            # dropped ("Connection reset by peer"); a retry reconnects.
            if attempt == _MAX_RETRIES - 1:
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
    # gspread waits forever by default, so a connection that dies mid-request
    # (e.g. the Mac sleeping) hangs the run. With a timeout it raises a
    # requests Timeout instead, which _with_retry retries.
    gc.set_timeout(_REQUEST_TIMEOUT_SECONDS)
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


# Friendly header labels people use in the sheet, beyond the "same words,
# different case/spacing" rule in _header_cell_ok ("Player Name" is fine).
HEADER_ALIASES: dict[str, set[str]] = {
    "high_school": {"hs", "school (hs)", "high school"},
}


def _normalize_label(label: str) -> str:
    return "_".join(label.strip().lower().replace("-", " ").split())


def _header_cell_ok(found: str, expected: str) -> bool:
    return (
        _normalize_label(found) == expected
        or found.strip().lower() in HEADER_ALIASES.get(expected, set())
    )


def _header_matches(found: list[str]) -> bool:
    """The header is fine if every column is where the code expects it,
    whatever its display label ("Player Name" for player_name, "HS" for
    high_school). Columns are read by position, so a moved, inserted or
    deleted column is the thing this must catch."""
    found = _trim(found)
    found = found + [""] * (len(HEADER) - len(found))
    return len(found) == len(HEADER) and all(_header_cell_ok(f, h) for f, h in zip(found, HEADER))


def _trim(cells: list[str]) -> list[str]:
    """Drops trailing empty cells (get_all_values pads every row to the
    widest row in the sheet)."""
    cells = list(cells)
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _only_blanked(found: list[str]) -> bool:
    """True if `found` is the expected header with some cells emptied."""
    found = _trim(found)
    found = found + [""] * (len(HEADER) - len(found))
    return len(found) == len(HEADER) and all(f == "" or _header_cell_ok(f, h) for f, h in zip(found, HEADER))


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
    if not _header_matches(existing_header) and _only_blanked(existing_header):
        # Someone cleared a header cell (e.g. A1) by hand; every other cell
        # still matches, so fill in just the blank ones (keeping any
        # renamed labels as they are).
        print("warning: restored blank header cell(s) in the offers tab", file=sys.stderr)
        trimmed = _trim(existing_header)
        padded = trimmed + [""] * (len(HEADER) - len(trimmed))
        healed = [f or h for f, h in zip(padded, HEADER)]
        _with_retry(ws.update, [healed], "A1", value_input_option="RAW")
        existing_header = healed
    if not _header_matches(existing_header):
        raise SheetSchemaError(
            "sheet header does not match the OfferRecord schema.\n"
            f"  expected: {HEADER}\n"
            f"  found:    {existing_header}\n"
            "Header labels can be renamed, but columns must stay in this order "
            "(no moved, inserted or deleted columns). Fix the header row and run again."
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
    key_col = HEADER.index("event_key")
    out = {}
    for i, row in enumerate(values[1:], start=2):
        if key_col < len(row) and row[key_col]:
            out[row[key_col]] = i
    return out


def latest_tweet_date(ws: gspread.Worksheet) -> str | None:
    values = _with_retry(ws.get_all_values)
    if len(values) <= 1:
        return None
    date_col = HEADER.index("tweet_date")
    dates = [row[date_col] for row in values[1:] if date_col < len(row) and row[date_col]]
    return max(dates) if dates else None


def sync_records(ws: gspread.Worksheet, records: list[OfferRecord]) -> tuple[int, int]:
    """Appends brand-new events, and folds a new source into `also_reported_by`
    for events that already exist. Returns (appended_count, updated_count).
    """
    existing = load_existing_event_keys(ws)
    also_col_idx = HEADER.index("also_reported_by") + 1  # gspread cols are 1-indexed
    source_handle_col_idx = HEADER.index("source_handle")

    to_append = []
    also_updates: list[dict] = []  # {"range": "H5", "values": [["a, b"]]}
    updated = 0
    for record in records:
        key = canonical_key(record.event_key, existing.keys())
        if key != record.event_key:
            record = replace(record, event_key=key)
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
        append_at_column_a(ws, to_append)
    return len(to_append), updated


def append_at_column_a(ws: gspread.Worksheet, rows: list[list[str]]) -> int:
    """Writes `rows` directly below the last non-empty row, starting in
    column A. Returns the first row number written.

    Sheets' own "append" guesses where the table starts, and after a header
    cell was blanked it started writing at column B, shifting every value
    one column right. Writing to an explicit A-column range can't drift."""
    start = len(_with_retry(ws.get_all_values)) + 1
    end = start + len(rows) - 1
    if end > ws.row_count:
        _with_retry(ws.add_rows, end - ws.row_count + 500)
    _with_retry(ws.update, rows, f"A{start}", value_input_option="RAW")
    return start


def repair_shifted_rows(ws: gspread.Worksheet) -> int:
    """Moves rows that were written one column to the right (empty column A,
    an extra value past the last column) back into place. Returns how many
    rows were fixed."""
    values = _with_retry(ws.get_all_values)
    width = len(HEADER)
    fixes = []
    for i, row in enumerate(values[1:], start=2):
        if len(row) > width and not row[0] and any(row[width:]):
            fixed = row[1 : width + 1]
            fixes.append({"range": f"A{i}", "values": [fixed + [""] * (len(row) - len(fixed))]})
    if fixes:
        _with_retry(ws.batch_update, fixes, value_input_option="RAW")
    return len(fixes)


def read_rows(ws: gspread.Worksheet) -> list[tuple[int, dict[str, str]]]:
    """Every data row as (sheet_row_number, {column: value}); row numbers
    are 1-indexed and count the header row."""
    values = _with_retry(ws.get_all_values)
    if len(values) <= 1:
        return []
    out = []
    for i, row in enumerate(values[1:], start=2):
        row = row + [""] * (len(HEADER) - len(row))
        out.append((i, dict(zip(HEADER, row))))
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


def update_column(ws: gspread.Worksheet, column: str, updates: dict[int, str]) -> None:
    """Sets `column` on the given rows ({row_number: value}) in one batch."""
    col = HEADER.index(column) + 1
    data = [
        {"range": gspread.utils.rowcol_to_a1(row_num, col), "values": [[value]]}
        for row_num, value in updates.items()
    ]
    _with_retry(ws.batch_update, data, value_input_option="RAW")


def update_also_reported_by(ws: gspread.Worksheet, updates: dict[int, str]) -> None:
    update_column(ws, "also_reported_by", updates)


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
    append_at_column_a(
        pruned, [[row.get(c, "") for c in HEADER] + [pruned_at, reason] for _, row, reason in rows]
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


# --- read-only view tabs ------------------------------------------------------
# Each view tab is a live FILTER() formula over the offers tab, so it updates
# the moment offers changes (new rows, pruning) with no extra writes from the
# scraper. A player listing several positions ("WR/DB") appears on each
# matching group's tab.
POSITION_GROUPS: dict[str, list[str]] = {
    "QB": ["QB"],
    "RB": ["RB"],
    "WR": ["WR"],
    "TE": ["TE"],
    "OL": ["OL", "OT", "OG", "IOL", "C"],
    "DL": ["DL", "DE", "DT", "EDGE"],
    "LB": ["LB"],
    "DB": ["DB", "CB", "S"],
    "ATH": ["ATH"],
    "K/P": ["K", "P"],
}
BLANK_POSITION_TAB = "Blank"
SEVEN_STATES_TAB = "7 states"
# GA, the Carolinas, TN, AL, FL and VA.
SEVEN_STATES = ["GA", "NC", "SC", "TN", "AL", "FL", "VA"]


def _col(name: str) -> str:
    return gspread.utils.rowcol_to_a1(1, HEADER.index(name) + 1).rstrip("1")


def view_tab_formulas() -> dict[str, str]:
    """{tab title: formula for cell A2} for every view tab (row 1 holds a
    header formula). Pure, so it's testable without a sheet."""
    src = f"'{WORKSHEET_NAME}'"
    last = _col(HEADER[-1])
    data = f"{src}!A2:{last}"
    pos = f"{src}!{_col('position')}2:{_col('position')}"
    state = f"{src}!{_col('state')}2:{_col('state')}"
    key = f"{src}!A2:A"

    def token_match(codes: list[str]) -> str:
        # whole tokens of a "/"-separated list: "OT/OG" matches OL, "C/PF" can't
        return f'REGEXMATCH(UPPER({pos}), "(^|[/ ,])({"|".join(codes)})($|[/ ,])")'

    formulas = {tab: f'=IFERROR(FILTER({data}, {token_match(codes)}), "")' for tab, codes in POSITION_GROUPS.items()}
    formulas[BLANK_POSITION_TAB] = f'=IFERROR(FILTER({data}, {key}<>"", {pos}=""), "")'
    formulas[SEVEN_STATES_TAB] = (
        f'=IFERROR(FILTER({data}, REGEXMATCH(UPPER({state}), "^({"|".join(SEVEN_STATES)})$")), "")'
    )
    return formulas


def setup_view_tabs(ws: gspread.Worksheet) -> list[str]:
    """Creates (or refreshes) every view tab. Safe to re-run. Returns the
    tab titles."""
    sh = ws.spreadsheet
    header_formula = f"={{'{WORKSHEET_NAME}'!A1:{_col(HEADER[-1])}1}}"
    titles = []
    for title, formula in view_tab_formulas().items():
        try:
            tab = _with_retry(sh.worksheet, title)
        except WorksheetNotFound:
            tab = _with_retry(sh.add_worksheet, title=title, rows=1000, cols=len(HEADER))
        _with_retry(
            tab.update,
            [[header_formula], [formula]],
            "A1:A2",
            value_input_option="USER_ENTERED",
        )
        _with_retry(tab.freeze, rows=1)
        titles.append(title)
    return titles


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
