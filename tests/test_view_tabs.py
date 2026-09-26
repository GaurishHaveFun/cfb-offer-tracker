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
