"""Position / Blank / "7 states" view tabs: live FILTER formulas over the
offers tab. The regexes inside the formulas are checked with Python's re,
which agrees with Sheets' RE2 for these simple patterns."""
import re

from gspread.exceptions import WorksheetNotFound

from cfb_offers import sheets


def _regex(tab):
    return re.search(r'"(\^[^"]*|\(\^[^"]*)"', sheets.view_tab_formulas()[tab]).group(1)


def _tabs_for(position):
    return [
        tab for tab in sheets.POSITION_GROUPS
        if re.search(_regex(tab), position.upper())
    ]


def test_every_listed_position_lands_on_its_group_tab():
    assert _tabs_for("WR/DB") == ["WR", "DB"]
    assert _tabs_for("OT/OG") == ["OL"]
    assert _tabs_for("DE/DT") == ["DL"]
    assert _tabs_for("EDGE") == ["DL"]
    assert _tabs_for("S/OL/DL") == ["OL", "DL", "DB"]
    assert _tabs_for("K/P") == ["K/P"]
    assert _tabs_for("QB") == ["QB"]


def test_tokens_match_whole_positions_only():
    assert _tabs_for("ATH") == ["ATH"]
    assert _tabs_for("TE/WR") == ["WR", "TE"]
    assert _tabs_for("") == []


def test_seven_states_tab_covers_ga_carolinas_tn_al_fl_va():
    rx = _regex(sheets.SEVEN_STATES_TAB)
    for st in ["GA", "NC", "SC", "TN", "AL", "FL", "VA"]:
        assert re.search(rx, st)
    for st in ["TX", "MS", "", "GAA", "WV"]:
        assert not re.search(rx, st)


def test_formulas_point_at_the_offers_tab_columns():
    f = sheets.view_tab_formulas()
    assert "'offers'!A2:U" in f["QB"] and "'offers'!H2:H" in f["QB"]  # position column
    assert "'offers'!L2:L" in f["7 states"]  # state column
    assert "'offers'!H2:H=\"\"" in f["Blank"]


class FakeTab:
    def __init__(self):
        self.cells, self.frozen = None, False

    def update(self, values, rng, value_input_option=None):
        self.cells = (rng, values, value_input_option)

    def freeze(self, rows=None):
        self.frozen = True


class FakeSpreadsheet:
    def __init__(self):
        self.tabs = {}

    def worksheet(self, title):
        if title not in self.tabs:
            raise WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeTab()
        return self.tabs[title]


class FakeOffers:
    def __init__(self, sh):
        self.spreadsheet = sh


def test_setup_creates_every_tab_and_is_safe_to_rerun():
    sh = FakeSpreadsheet()
    titles = sheets.setup_view_tabs(FakeOffers(sh))
    assert titles == [*sheets.POSITION_GROUPS, "Blank", "7 states"]
    assert set(sh.tabs) == set(titles)
    rng, values, mode = sh.tabs["QB"].cells
    assert rng == "A1:A2" and mode == "USER_ENTERED"
    assert values[0] == ["={'offers'!A1:U1}"]
    assert sh.tabs["QB"].frozen

    sheets.setup_view_tabs(FakeOffers(sh))  # re-run: no duplicate tabs
    assert set(sh.tabs) == set(titles)


def test_view_tabs_list_newest_tweets_first():
    for formula in sheets.view_tab_formulas().values():
        assert formula.startswith("=IFERROR(SORT(FILTER(")
        assert formula.endswith(", 16, FALSE), \"\")")  # tweet_date is column 16 (P)
    assert sheets.HEADER[15] == "tweet_date"


# --- styling ------------------------------------------------------------------


def test_display_labels_still_pass_the_header_check():
    labels = [sheets.display_label(c) for c in sheets.HEADER]
    assert labels[4] == "Player Name" and labels[16] == "Tweet URL" and labels[14] == "Tweet ID"
    assert sheets._header_matches(labels)


def test_every_tab_the_code_creates_has_a_style():
    tabs = [*sheets.view_tab_formulas(), sheets.WORKSHEET_NAME, sheets.PRUNED_WORKSHEET_NAME]
    assert all(t in sheets.TAB_STYLE for t in tabs)


def _meta(*titles, extras=None):
    extras = extras or {}
    return {
        "sheets": [
            {"properties": {"sheetId": 100 + i, "title": t}, **extras.get(t, {})}
            for i, t in enumerate(titles)
        ]
    }


def test_home_links_only_to_tabs_that_exist():
    rows, marks = sheets.home_rows({"offers": 0, "QB": 7})
    assert list(marks["tabs"].values()) == ["offers", "QB"]
    qb = rows[[r for r, t in marks["tabs"].items() if t == "QB"][0]]
    assert qb[0] == '=HYPERLINK("#gid=7", "QB")'
    assert qb[3] == "=COUNTIF('QB'!A2:A, \"?*\")"
    # USER_ENTERED would strip a leading apostrophe or evaluate a leading = / + / -
    plain = [c for r in rows for c in r if not c.startswith(("=HYPERLINK", "=COUNTIF"))]
    assert not any(c.startswith(("'", "=", "+", "-")) for c in plain)


def test_tabs_are_ordered_and_colored_by_group():
    meta = _meta("pruned", "QB", "offers", "My notes", "Home", "DL")
    reqs = sheets.style_requests(meta, sheets.home_rows({})[1])
    moves = [r["updateSheetProperties"]["properties"] for r in reqs
             if "index" in r.get("updateSheetProperties", {}).get("properties", {})]
    ids = {s["properties"]["sheetId"]: s["properties"]["title"] for s in meta["sheets"]}
    assert [ids[m["sheetId"]] for m in moves] == ["Home", "offers", "QB", "DL", "pruned"]
    assert [m["index"] for m in moves] == [0, 1, 2, 3, 4]
    color = {ids[m["sheetId"]]: m["tabColorStyle"]["rgbColor"] for m in moves}
    assert color["QB"] != color["DL"]
    # a tab the user made is left alone
    assert "'sheetId': 103" not in str(reqs)  # "My notes"


def test_rerun_replaces_instead_of_stacking():
    meta = _meta("offers", extras={"offers": {
        "bandedRanges": [{"bandedRangeId": 5}],
        "conditionalFormats": [{}, {}, {}],
        "filterViews": [{"filterViewId": 9, "title": "Newest first"}, {"filterViewId": 10, "title": "Mine"}],
    }})
    reqs = sheets.style_requests(meta, sheets.home_rows({})[1])
    kinds = [next(iter(r)) for r in reqs]
    assert kinds.count("deleteBanding") == 1 and kinds.count("addBanding") == 1
    assert kinds.count("deleteConditionalFormatRule") == 3
    assert kinds.count("addConditionalFormatRule") == 3
    assert [r["deleteFilterView"]["filterId"] for r in reqs if "deleteFilterView" in r] == [9]
    assert kinds.count("addFilterView") == 1
    # every delete runs before any add
    assert max(i for i, k in enumerate(kinds) if k.startswith("delete")) < kinds.index("addBanding")


def test_styling_never_writes_values():
    meta = _meta("Home", "offers", "QB", "pruned")
    for r in sheets.style_requests(meta, sheets.home_rows({"offers": 101})[1]):
        if "repeatCell" in r:
            assert r["repeatCell"]["fields"].startswith("userEnteredFormat")
        assert not ({"updateCells", "appendCells", "deleteDimension", "insertDimension"} & set(r))


def test_internal_columns_are_hidden_on_data_tabs():
    reqs = sheets.style_requests(_meta("offers"), sheets.home_rows({})[1])
    hidden = [r["updateDimensionProperties"]["range"]["startIndex"] for r in reqs
              if r.get("updateDimensionProperties", {}).get("properties", {}).get("hiddenByUser")]
    assert [sheets.HEADER[i] for i in hidden] == ["event_key", "tweet_id", "scraped_at"]


def _rules(reqs, sid):
    return [r["addConditionalFormatRule"]["rule"] for r in reqs
            if "addConditionalFormatRule" in r
            and r["addConditionalFormatRule"]["rule"]["ranges"][0]["sheetId"] == sid]


def test_visits_tab_gets_its_own_highlights_and_filter_view():
    meta = _meta("offers", "visits")  # sheetIds 100, 101
    reqs = sheets.style_requests(meta, sheets.home_rows({})[1])
    visit_formulas = [r["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in _rules(reqs, 101)]
    status, vtype = sheets._col("visit_status", sheets.VISIT_HEADER), sheets._col("visit_type", sheets.VISIT_HEADER)
    assert visit_formulas == [f'=${status}2="upcoming"', f'=${vtype}2="official"']
    # no commit/flip rules on visits, no visit rules on offers
    offer_formulas = [r["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in _rules(reqs, 100)]
    assert all("upcoming" not in f and "official" not in f for f in offer_formulas)
    views = {r["addFilterView"]["filter"]["range"]["sheetId"]: r["addFilterView"]["filter"]
             for r in reqs if "addFilterView" in r}
    assert set(views) == {100, 101}
    assert views[101]["sortSpecs"][0]["dimensionIndex"] == sheets.VISIT_HEADER.index("tweet_date")
    assert views[101]["range"]["endColumnIndex"] == len(sheets.VISIT_HEADER)


def test_visits_tab_sits_after_offers_and_its_labels_pass_the_header_check():
    order = list(sheets.TAB_STYLE)
    assert order.index("visits") == order.index("offers") + 1
    labels = [sheets.display_label(c) for c in sheets.VISIT_HEADER]
    assert sheets._header_matches(labels, sheets.VISIT_HEADER)


def test_home_legend_covers_visits():
    rows, marks = sheets.home_rows({"visits": 3})
    assert {"Upcoming", "Official"} <= set(marks["legend"])
    assert ['=HYPERLINK("#gid=3", "visits")'] == [r[0] for r in rows if r and "visits\")" in r[0]]
