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

from cfb_offers.dedupe import add_completed_note, add_source, canonical_key
from cfb_offers.models import OfferRecord, VisitRecord

HEADER = OfferRecord.columns()
WORKSHEET_NAME = "offers"
# Rows removed by --prune-sheet are moved here (never just deleted), with
# when and why, so any prune can be reviewed and undone by hand.
PRUNED_WORKSHEET_NAME = "pruned"
PRUNED_HEADER = HEADER + ["pruned_at", "prune_reason"]
# Completed recruit visits get their own tab with their own (trimmed) schema.
VISITS_WORKSHEET_NAME = "visits"
VISIT_HEADER = VisitRecord.columns()

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


def _header_matches(found: list[str], header: list[str] = HEADER) -> bool:
    """The header is fine if every column is where the code expects it,
    whatever its display label ("Player Name" for player_name, "HS" for
    high_school). Columns are read by position, so a moved, inserted or
    deleted column is the thing this must catch."""
    found = _trim(found)
    found = found + [""] * (len(header) - len(found))
    return len(found) == len(header) and all(_header_cell_ok(f, h) for f, h in zip(found, header))


def _trim(cells: list[str]) -> list[str]:
    """Drops trailing empty cells (get_all_values pads every row to the
    widest row in the sheet)."""
    cells = list(cells)
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _only_blanked(found: list[str], header: list[str] = HEADER) -> bool:
    """True if `found` is the expected header with some cells emptied."""
    found = _trim(found)
    found = found + [""] * (len(header) - len(found))
    return len(found) == len(header) and all(
        f == "" or _header_cell_ok(f, h) for f, h in zip(found, header)
    )


def _ensure_header(ws: gspread.Worksheet, header: list[str] = HEADER) -> None:
    """Creates the header row if the worksheet is empty; otherwise verifies
    the existing header row matches `header` (OfferRecord's schema by
    default) exactly, raising SheetSchemaError if it doesn't.
    """
    values = _with_retry(ws.get_all_values)
    if not values or not values[0]:
        _with_retry(ws.append_rows, [header], value_input_option="RAW")
        _with_retry(ws.freeze, rows=1)
        return

    existing_header = values[0]
    if not _header_matches(existing_header, header) and _only_blanked(existing_header, header):
        # Someone cleared a header cell (e.g. A1) by hand, or the schema
        # gained a column at the end (visit_status); every other cell still
        # matches, so fill in just the blank ones (keeping any renamed
        # labels as they are).
        trimmed = _trim(existing_header)
        if len(trimmed) < len(header) and all(trimmed):
            added = ", ".join(header[len(trimmed):])
            print(f"note: added column(s) {added} to the {ws.title} tab", file=sys.stderr)
        else:
            print(f"warning: restored blank header cell(s) in the {ws.title} tab", file=sys.stderr)
        col_count = getattr(ws, "col_count", len(header))
        if col_count < len(header):
            _with_retry(ws.add_cols, len(header) - col_count)
        padded = trimmed + [""] * (len(header) - len(trimmed))
        healed = [f or h for f, h in zip(padded, header)]
        _with_retry(ws.update, [healed], "A1", value_input_option="RAW")
        existing_header = healed
    if not _header_matches(existing_header, header):
        schema = "VisitRecord" if header == VISIT_HEADER else "OfferRecord"
        raise SheetSchemaError(
            f"{ws.title} tab header does not match the {schema} schema.\n"
            f"  expected: {header}\n"
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


def visits_worksheet(ws: gspread.Worksheet) -> gspread.Worksheet:
    """The 'visits' tab of the same spreadsheet as `ws`, created (with its
    header) if missing and header-checked like the offers tab."""
    sh = ws.spreadsheet
    try:
        visits = _with_retry(sh.worksheet, VISITS_WORKSHEET_NAME)
    except WorksheetNotFound:
        visits = _with_retry(
            sh.add_worksheet, title=VISITS_WORKSHEET_NAME, rows=1000, cols=len(VISIT_HEADER)
        )
    _ensure_header(visits, VISIT_HEADER)
    return visits


def load_existing_event_keys(ws: gspread.Worksheet, header: list[str] = HEADER) -> dict[str, int]:
    """Returns {event_key: row_number} for every row already in the sheet
    (row_number is 1-indexed, including the header row).
    """
    values = _with_retry(ws.get_all_values)
    if not values:
        return {}
    key_col = header.index("event_key")
    out = {}
    for i, row in enumerate(values[1:], start=2):
        if key_col < len(row) and row[key_col]:
            out[row[key_col]] = i
    return out


def latest_tweet_date(ws: gspread.Worksheet, header: list[str] = HEADER) -> str | None:
    values = _with_retry(ws.get_all_values)
    if len(values) <= 1:
        return None
    date_col = header.index("tweet_date")
    dates = [row[date_col] for row in values[1:] if date_col < len(row) and row[date_col]]
    return max(dates) if dates else None


def sync_records(
    ws: gspread.Worksheet, records: list[OfferRecord] | list[VisitRecord], header: list[str] = HEADER
) -> tuple[int, int]:
    """Appends brand-new events, and folds a new source into `also_reported_by`
    for events that already exist (and turns an upcoming visit completed
    when its thank-you post arrives). Returns (appended_count, updated_count).
    `header` is the tab's schema (VISIT_HEADER for the visits tab).
    """
    existing = load_existing_event_keys(ws, header)
    also_col_idx = header.index("also_reported_by") + 1  # gspread cols are 1-indexed
    source_handle_col_idx = header.index("source_handle")
    status_col_idx = header.index("visit_status") + 1 if "visit_status" in header else None
    notes_col_idx = header.index("notes") + 1

    to_append = []
    also_updates: list[dict] = []  # {"range": "H5", "values": [["a, b"]]}; visit status upgrades too
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
            if status_col_idx and _completes_upcoming(record, current_row, status_col_idx):
                current_notes = current_row[notes_col_idx - 1] if len(current_row) >= notes_col_idx else ""
                also_updates += [
                    {"range": gspread.utils.rowcol_to_a1(row_num, status_col_idx), "values": [["completed"]]},
                    {
                        "range": gspread.utils.rowcol_to_a1(row_num, notes_col_idx),
                        "values": [[add_completed_note(current_notes, record.tweet_url)]],
                    },
                ]
                updated += 1

    if also_updates:
        _with_retry(ws.batch_update, also_updates, value_input_option="RAW")

    if to_append:
        append_at_column_a(ws, to_append)
    return len(to_append), updated


def _completes_upcoming(record, current_row: list[str], status_col_idx: int) -> bool:
    """True if `record` is a completed visit for a row still marked upcoming
    (a blank status is an older row, so already completed)."""
    current = current_row[status_col_idx - 1] if len(current_row) >= status_col_idx else ""
    return getattr(record, "visit_status", "") == "completed" and current == "upcoming"


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


def _col(name: str, header: list[str] = HEADER) -> str:
    return gspread.utils.rowcol_to_a1(1, header.index(name) + 1).rstrip("1")


def view_tab_formulas() -> dict[str, str]:
    """{tab title: formula for cell A2} for every view tab (row 1 holds a
    header formula). Pure, so it's testable without a sheet."""
    src = f"'{WORKSHEET_NAME}'"
    last = _col(HEADER[-1])
    data = f"{src}!A2:{last}"
    pos = f"{src}!{_col('position')}2:{_col('position')}"
    state = f"{src}!{_col('state')}2:{_col('state')}"
    key = f"{src}!A2:A"

    # newest tweet first (tweet_date is an ISO string, so text order is date order)
    date_idx = HEADER.index("tweet_date") + 1

    def view(*conditions: str) -> str:
        return f'=IFERROR(SORT(FILTER({data}, {", ".join(conditions)}), {date_idx}, FALSE), "")'

    def token_match(codes: list[str]) -> str:
        # whole tokens of a "/"-separated list: "OT/OG" matches OL, "C/PF" can't
        return f'REGEXMATCH(UPPER({pos}), "(^|[/ ,])({"|".join(codes)})($|[/ ,])")'

    formulas = {tab: view(token_match(codes)) for tab, codes in POSITION_GROUPS.items()}
    formulas[BLANK_POSITION_TAB] = view(f'{key}<>""', f'{pos}=""')
    formulas[SEVEN_STATES_TAB] = view(f'REGEXMATCH(UPPER({state}), "^({"|".join(SEVEN_STATES)})$")')
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


# --- look and feel ------------------------------------------------------------
# --setup-tabs also styles the sheet: a Home tab linking to every tab, tab
# colors by group, colored header rows, alternating row colors, commit /
# decommit / upcoming-visit highlights, column widths and friendly header
# labels. Only
# formatting and the row 1 labels change (labels may be renamed - see
# _header_cell_ok), never column order or data, so the scraper is unaffected.
# Re-running replaces the banding, conditional formats and "Newest first"
# filter view on these tabs instead of stacking duplicates.
HOME_TAB = "Home"
NEWEST_FIRST_VIEW = "Newest first"

_OFFENSE, _DEFENSE = "#3C78D8", "#CC0000"
# {tab: (group, tab color, what's in it)}, in the order the tabs are arranged
TAB_STYLE: dict[str, tuple[str, str, str]] = {
    HOME_TAB: ("Start here", "#434343", "This page"),
    WORKSHEET_NAME: ("All offers", "#1F3864", "Every offer, commit and decommit (the scraper writes here)"),
    VISITS_WORKSHEET_NAME: ("Visits", "#00838F", "Recruit visits, upcoming and completed (the scraper writes here)"),
    SEVEN_STATES_TAB: ("Region", "#E69138", "Players from GA, NC, SC, TN, AL, FL and VA"),
    "QB": ("Offense", _OFFENSE, "Quarterbacks"),
    "RB": ("Offense", _OFFENSE, "Running backs"),
    "WR": ("Offense", _OFFENSE, "Wide receivers"),
    "TE": ("Offense", _OFFENSE, "Tight ends"),
    "OL": ("Offense", _OFFENSE, "Offensive line (OL, OT, OG, IOL, C)"),
    "DL": ("Defense", _DEFENSE, "Defensive line (DL, DE, DT, EDGE)"),
    "LB": ("Defense", _DEFENSE, "Linebackers"),
    "DB": ("Defense", _DEFENSE, "Defensive backs (DB, CB, S)"),
    "ATH": ("Athlete", "#674EA7", "Athletes"),
    "K/P": ("Special teams", "#38761D", "Kickers and punters"),
    BLANK_POSITION_TAB: ("Needs review", "#7F6000", "Rows with no position listed"),
    PRUNED_WORKSHEET_NAME: ("Archive", "#999999", "Rows removed by --prune-sheet, with when and why"),
}
COMMIT_FILL = "#D9EAD3"
DECOMMIT_FILL = "#F4CCCC"
FLIP_TEXT = "#B45F06"
UPCOMING_FILL = "#FFF2CC"
# Internal bookkeeping columns, hidden (not removed) to keep rows readable.
HIDDEN_COLUMNS = ("event_key", "tweet_id", "scraped_at")
COLUMN_WIDTHS = {
    "event_type": 90, "is_flip": 60, "school": 130, "player_name": 160,
    "player_handle": 130, "class_year": 70, "position": 80, "height": 60,
    "weight": 60, "high_school": 190, "state": 55, "source_type": 90,
    "source_handle": 130, "tweet_date": 160, "tweet_url": 120,
    "tweet_text": 420, "also_reported_by": 170, "notes": 220,
    "pruned_at": 160, "prune_reason": 240, "visit_type": 90, "visit_status": 100,
}
# Data tabs whose columns aren't HEADER; every other styled data tab is
# offers or a view of it.
TAB_COLUMNS = {PRUNED_WORKSHEET_NAME: PRUNED_HEADER, VISITS_WORKSHEET_NAME: VISIT_HEADER}


def display_label(column: str) -> str:
    """"player_name" -> "Player Name", "tweet_url" -> "Tweet URL". Always
    normalizes back to the column name, so the header check still passes."""
    return " ".join(w.upper() if w in ("id", "url") else w.capitalize() for w in column.split("_"))


def _rgb(hex_color: str, tint: float = 0.0) -> dict:
    """Sheets color for "#RRGGBB", optionally mixed toward white by `tint`."""
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return {"red": r + (1 - r) * tint, "green": g + (1 - g) * tint, "blue": b + (1 - b) * tint}


_WHITE = "#FFFFFF"


def _cell_format(rng: dict, fmt: dict) -> dict:
    """repeatCell that sets only the given userEnteredFormat fields, so
    values and formulas in the range are left alone."""
    return {
        "repeatCell": {
            "range": rng,
            "cell": {"userEnteredFormat": fmt},
            "fields": f"userEnteredFormat({','.join(fmt)})",
        }
    }


def _size(sid: int, dimension: str, start: int, pixels: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": dimension, "startIndex": start, "endIndex": start + 1},
            "properties": {"pixelSize": pixels},
            "fields": "pixelSize",
        }
    }


def _row_rule(sid: int, n: int, formula: str, fmt: dict) -> dict:
    return {
        "addConditionalFormatRule": {
            "index": 0,
            "rule": {
                "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": n}],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                    "format": fmt,
                },
            },
        }
    }


def _cell_rule(sid: int, columns: list[str], column: str, formula: str, fmt: dict) -> dict:
    """Conditional format on one column only (rows 2+)."""
    i = columns.index(column)
    rule = _row_rule(sid, len(columns), formula, fmt)
    rule["addConditionalFormatRule"]["rule"]["ranges"][0].update(startColumnIndex=i, endColumnIndex=i + 1)
    return rule


def _highlight_rules(sid: int, columns: list[str]) -> list[dict]:
    """Offer tabs: commits green, decommits red, flips bold orange. The
    visits tab: upcoming visits yellow, official visits bold."""
    def ref(column: str) -> str:
        return f"${_col(column, columns)}2"

    def fill(color: str) -> dict:
        return {"backgroundColorStyle": {"rgbColor": _rgb(color)}}

    n = len(columns)
    reqs = []
    if "event_type" in columns:
        reqs += [
            _row_rule(sid, n, f'={ref("event_type")}="commit"', fill(COMMIT_FILL)),
            _row_rule(sid, n, f'={ref("event_type")}="decommit"', fill(DECOMMIT_FILL)),
            _cell_rule(sid, columns, "is_flip", f'={ref("is_flip")}="True"',
                       {"textFormat": {"bold": True, "foregroundColorStyle": {"rgbColor": _rgb(FLIP_TEXT)}}}),
        ]
    if "visit_status" in columns:
        reqs += [
            _row_rule(sid, n, f'={ref("visit_status")}="upcoming"', fill(UPCOMING_FILL)),
            _cell_rule(sid, columns, "visit_type", f'={ref("visit_type")}="official"', {"textFormat": {"bold": True}}),
        ]
    return reqs


def _data_tab_requests(sid: int, color: str, columns: list[str]) -> list[dict]:
    n = len(columns)
    header = {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": n}
    body = {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": n}
    reqs = [
        _cell_format(header, {
            "backgroundColorStyle": {"rgbColor": _rgb(color)},
            "textFormat": {"bold": True, "foregroundColorStyle": {"rgbColor": _rgb(_WHITE)}},
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }),
        _cell_format(body, {"verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP"}),
        _size(sid, "ROWS", 0, 36),
        {
            "addBanding": {
                "bandedRange": {
                    "range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": n},
                    "rowProperties": {
                        "headerColorStyle": {"rgbColor": _rgb(color)},
                        "firstBandColorStyle": {"rgbColor": _rgb(_WHITE)},
                        "secondBandColorStyle": {"rgbColor": _rgb(color, tint=0.9)},
                    },
                }
            }
        },
    ]
    reqs += _highlight_rules(sid, columns)
    for i, column in enumerate(columns):
        if column in COLUMN_WIDTHS:
            reqs.append(_size(sid, "COLUMNS", i, COLUMN_WIDTHS[column]))
        if column in HIDDEN_COLUMNS:
            reqs.append({
                "updateDimensionProperties": {
                    "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                    "properties": {"hiddenByUser": True},
                    "fields": "hiddenByUser",
                }
            })
    return reqs


def home_rows(gids: dict[str, int]) -> tuple[list[list[str]], dict]:
    """The Home tab's cells (USER_ENTERED) and where each part landed, for
    styling. `gids` is {tab title: sheetId}; tabs that don't exist yet
    (e.g. pruned, before the first prune) are left out."""
    rows: list[list[str]] = [
        ["CFB Offer Tracker"],
        ["Click a tab name to jump to it. Position and region tabs update on their own "
         "from offers, newest tweets first."],
        [""],
        ["Tab", "Group", "What's in it", "Rows"],
    ]
    marks: dict = {"table_header": 3, "tabs": {}, "sections": [], "legend": {}}
    for title, (group, _, description) in TAB_STYLE.items():
        if title == HOME_TAB or title not in gids:
            continue
        marks["tabs"][len(rows)] = title
        rows.append([
            f'=HYPERLINK("#gid={gids[title]}", "{title}")',
            group,
            description,
            f"=COUNTIF('{title}'!A2:A, \"?*\")",
        ])
    rows.append([""])
    marks["sections"].append(len(rows))
    rows.append(["Row colors"])
    for label, meaning in [
        ("Commit", "The player committed to this school"),
        ("Decommit", "The player backed out of a commitment"),
        ("Flip", "Is Flip column: a commit that flipped from another school"),
        ("Upcoming", "Visits tab: a visit that hasn't happened yet"),
        ("Official", "Visits tab: an official visit (unofficial ones aren't bold)"),
    ]:
        marks["legend"][label] = len(rows)
        rows.append([label, "", meaning])
    rows.append([""])
    marks["sections"].append(len(rows))
    rows.append(["Tips"])
    rows.append(["Sort offers or visits without changing them for anyone else: Data > Filter views > Newest first."])
    rows.append(["Don't rename tabs, or insert, move or delete columns on offers or visits: the scraper relies on them. "
                 "Colors, widths and header wording are fine to change."])
    return rows, marks


def _home_requests(sid: int, marks: dict) -> list[dict]:
    def cells(row: int, col: int = 0, end_col: int | None = None) -> dict:
        return {"sheetId": sid, "startRowIndex": row, "endRowIndex": row + 1,
                "startColumnIndex": col, "endColumnIndex": end_col if end_col is not None else col + 1}

    white_bold = {"bold": True, "foregroundColorStyle": {"rgbColor": _rgb(_WHITE)}}
    reqs = [
        # reset last run's formatting, then lay it out fresh
        {"repeatCell": {"range": {"sheetId": sid}, "cell": {}, "fields": "userEnteredFormat"}},
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sid, "gridProperties": {"hideGridlines": True}},
                "fields": "gridProperties.hideGridlines",
            }
        },
        _size(sid, "COLUMNS", 0, 150),
        _size(sid, "COLUMNS", 1, 130),
        _size(sid, "COLUMNS", 2, 460),
        _size(sid, "COLUMNS", 3, 70),
        _cell_format(cells(0), {"textFormat": {"bold": True, "fontSize": 20}}),
        _cell_format(cells(1), {"textFormat": {"italic": True, "foregroundColorStyle": {"rgbColor": _rgb("#666666")}}}),
        _cell_format(cells(marks["table_header"], 0, 4), {
            "backgroundColorStyle": {"rgbColor": _rgb(TAB_STYLE[HOME_TAB][1])},
            "textFormat": white_bold,
        }),
    ]
    for row, title in marks["tabs"].items():
        color = TAB_STYLE[title][1]
        reqs.append(_cell_format(cells(row), {"textFormat": {"bold": True, "fontSize": 11}}))
        reqs.append(_cell_format(cells(row, 1), {
            "backgroundColorStyle": {"rgbColor": _rgb(color)},
            "textFormat": white_bold,
            "horizontalAlignment": "CENTER",
        }))
    for row in marks["sections"]:
        reqs.append(_cell_format(cells(row), {"textFormat": {"bold": True, "fontSize": 12}}))
    legend = marks["legend"]
    reqs.append(_cell_format(cells(legend["Commit"]), {"backgroundColorStyle": {"rgbColor": _rgb(COMMIT_FILL)}}))
    reqs.append(_cell_format(cells(legend["Decommit"]), {"backgroundColorStyle": {"rgbColor": _rgb(DECOMMIT_FILL)}}))
    reqs.append(_cell_format(cells(legend["Flip"]), {
        "textFormat": {"bold": True, "foregroundColorStyle": {"rgbColor": _rgb(FLIP_TEXT)}},
    }))
    reqs.append(_cell_format(cells(legend["Upcoming"]), {"backgroundColorStyle": {"rgbColor": _rgb(UPCOMING_FILL)}}))
    reqs.append(_cell_format(cells(legend["Official"]), {"textFormat": {"bold": True}}))
    return reqs


def style_requests(meta: dict, home_marks: dict) -> list[dict]:
    """Every formatting request for one batch_update, given the spreadsheet
    metadata (fetch_sheet_metadata) and home_rows' marks. Pure, so it's
    testable without a sheet."""
    by_title = {s["properties"]["title"]: s for s in meta.get("sheets", [])}
    styled = [(title, by_title[title]) for title in TAB_STYLE if title in by_title]
    reqs: list[dict] = []
    # Drop what a previous run added, so re-running doesn't stack duplicates.
    for _, s in styled:
        sid = s["properties"]["sheetId"]
        reqs += [{"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}} for b in s.get("bandedRanges", [])]
        reqs += [{"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}} for _ in s.get("conditionalFormats", [])]
        reqs += [
            {"deleteFilterView": {"filterId": f["filterViewId"]}}
            for f in s.get("filterViews", [])
            if f.get("title") == NEWEST_FIRST_VIEW
        ]
    for index, (title, s) in enumerate(styled):
        reqs.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": s["properties"]["sheetId"],
                    "index": index,
                    "tabColorStyle": {"rgbColor": _rgb(TAB_STYLE[title][1])},
                },
                "fields": "index,tabColorStyle",
            }
        })
    for title, s in styled:
        sid = s["properties"]["sheetId"]
        if title == HOME_TAB:
            reqs += _home_requests(sid, home_marks)
            continue
        columns = TAB_COLUMNS.get(title, HEADER)
        reqs += _data_tab_requests(sid, TAB_STYLE[title][1], columns)
        if title in (WORKSHEET_NAME, VISITS_WORKSHEET_NAME):
            reqs.append({
                "addFilterView": {
                    "filter": {
                        "title": NEWEST_FIRST_VIEW,
                        "range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0,
                                  "endColumnIndex": len(columns)},
                        "sortSpecs": [{"dimensionIndex": columns.index("tweet_date"), "sortOrder": "DESCENDING"}],
                    }
                }
            })
    return reqs


def style_sheet(ws: gspread.Worksheet) -> None:
    """Applies the look above to every tab and (re)builds the Home tab.
    Safe to re-run."""
    sh = ws.spreadsheet
    _with_retry(ws.update, [[display_label(c) for c in HEADER]], "A1", value_input_option="RAW")
    for title, columns in TAB_COLUMNS.items():
        # only tabs the scraper already made; labels go on an existing header
        try:
            tab = _with_retry(sh.worksheet, title)
        except WorksheetNotFound:
            continue
        if _with_retry(tab.row_values, 1):
            _with_retry(tab.update, [[display_label(c) for c in columns]], "A1", value_input_option="RAW")
    try:
        home = _with_retry(sh.worksheet, HOME_TAB)
    except WorksheetNotFound:
        home = _with_retry(sh.add_worksheet, title=HOME_TAB, rows=100, cols=6)

    meta = _with_retry(sh.fetch_sheet_metadata)
    gids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    rows, marks = home_rows(gids)
    _with_retry(home.batch_clear, ["A:F"])
    _with_retry(home.update, rows, "A1", value_input_option="USER_ENTERED")
    _with_retry(sh.batch_update, {"requests": style_requests(meta, marks)})


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
