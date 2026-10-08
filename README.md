# CFB Offer Tracker

Scrapes X/Twitter every 6 hours for high-school football players announcing
**offers, commitments, and decommitments** (including flips) from 34
CFB programs (including all 16 SEC schools), parses the player's bio, dedupes multi-source
reports into one row per event, and appends new rows to a private Google
Sheet.

Scraping uses [twscrape](https://github.com/vladkens/twscrape) (login-based,
no official API key) via **dedicated burner X account(s)**, so nothing here
ever touches your real account. Classification is regex/rules-based, entirely
in `src/cfb_offers/classify.py`, `sources.py`, and `profile.py` — see the
tests in `tests/` for the exact cases it handles.

## Setup

### 1. Create a burner X account

Don't use a personal or real account — this repo runs unattended from a
datacenter IP on a fixed schedule, which X's anti-automation systems don't
like. Use a throwaway account, solve any phone/email verification once
manually, and leave it logged in. You can add more burner accounts later
(see step 4) so twscrape has spares to rotate to when one gets rate-limited.

### 2. Copy your cookies

In a browser where the burner account is logged in, open DevTools →
Application → Cookies → `https://x.com`, and copy the values of `auth_token`
and `ct0`. Build a cookie string from them:

```
auth_token=<value>; ct0=<value>
```

Save it as the `X_COOKIES` GitHub secret. Because it's cookie-based (not a
fresh login every run), Actions never has to log in again, which is what
keeps the account from getting flagged.

If cookies ever go stale (a "no active accounts - cookies expired" error in
the Actions log), repeat this step with a fresh `auth_token`/`ct0` pair and
update the secret.

### 3. Create a Google Sheet + service account

Create a Google Cloud service account (JSON key), share a Google Sheet with
it as Editor, and set the `GOOGLE_SERVICE_ACCOUNT_JSON` / `SHEET_ID`
secrets — no `gcloud` CLI needed, it's all done by clicking through the
Console and Sheets UI. Full click-by-click steps, plus how to verify it
worked with `python -m cfb_offers --check-sheet`, are in
**[docs/google-sheets-setup.md](docs/google-sheets-setup.md)**.

### 4. GitHub Actions setup

The scraper runs every 6 hours via `.github/workflows/scrape.yml` (staggered
at `:17` past the hour), plus `.github/workflows/tests.yml` on every
push/PR. See **[docs/github-actions-setup.md](docs/github-actions-setup.md)**
for exactly how to add the three secrets, trigger and watch a manual run,
what a failure email means (usually expired cookies) and how to fix it, and
GitHub's 60-day scheduled-workflow inactivity rule.

Actions runs never do the 30-day backfill — a scheduled run is capped at 30
minutes, which isn't enough headroom for a burner account to safely clear a
30-day window across every query. Instead, a scheduled run only looks back
to the last row's tweet date (with a day of overlap — dedupe handles the
repeats), and if the sheet is still empty or that gap is larger than 3 days
it's clamped to 3 days, with a counts-only warning in the Actions log
telling you to run the backfill locally (see below). **Run the one-time
30-day backfill locally before turning on the schedule**, so the sheet
isn't stuck re-clamping to 3 days forever.

To rotate between multiple burner accounts (recommended — twscrape falls
back to another account when one hits a rate limit instead of just waiting,
and each extra account adds throughput since twscrape splits requests
across the whole pool), put one cookie string per line in `X_COOKIES`:

```
auth_token=<value1>; ct0=<value1>
auth_token=<value2>; ct0=<value2>
```

Each line becomes its own pool account.

### One-time local backfill

Run this once, locally, before the schedule takes over. It writes straight
to the real sheet, saving after every search:

```
X_COOKIES="$(cat x_cookies.txt)" GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)" SHEET_ID=... \
  python -m cfb_offers --backfill-days 90 --dump-raw backfill.jsonl
```

`--backfill-days N` searches the last N days **one week at a time**, paging
each week until it runs out, so a busy recent week can't crowd older weeks
out (a single long search stops after its page cap, which for busy schools
covers only 2-3 weeks). Expect a few hundred requests - hours, not minutes,
with one or two accounts; add accounts to `X_COOKIES` to speed it up.

Progress is printed as `slice K/13 (dates) query J/14`. If it stops, rerun
with `--resume-from-slice K` to skip the weeks already done (rows already in
the sheet are never duplicated, and `--dump-raw` is appended to instead of
restarted). The older `--backfill` flag still does a single 30-day search per
group. Neither runs in GitHub Actions, which always uses a short window.

### Expected rate limits

twscrape's SearchTimeline endpoint rate-limits per account; one burner
account can burn through its limit in well under a minute of steady
querying, after which twscrape waits out X's reset window (tens of
minutes) before that account can search again. Query packing and the
lower default page cap (see below) keep a normal scheduled run well under
that limit with even a single account, but a 30-day `--backfill` run is
long enough that you should expect at least one rate-limit wait unless you
add more than one cookie line to `X_COOKIES` — each additional account
gives twscrape somewhere else to rotate to instead of waiting.

### Running on a schedule (Mac)

X blocks searches coming from cloud/CI servers (GitHub Actions gets HTTP
403), so the every-6-hours scrape runs on a Mac from a home connection:

```
echo "<your sheet id>" > sheet_id.txt      # gitignored, next to x_cookies.txt and sa.json
scripts/install_mac_schedule.sh            # installs two launchd jobs (below)
```

Offers run at 00:17, 06:17, 12:17 and 18:17 (`--offers-only`); visits run
3 hours later at 03:17, 09:17, 15:17 and 21:17 (`--visits-only`), so the two
never search X at the same time and each run stays well under the rate
limit. Each run's lookback window starts from the newest tweet in its own
tab. Rerun the install script after pulling this change - the old single
job would otherwise keep running offers only.

Each run appends to `logs/scrape.log`, saves its tweets to `runs/` (kept 60
days, for `--prune-sheet`), and shows a macOS notification if it fails. Runs
missed while the Mac is asleep run once when it wakes; the next run's window
starts from the newest tweet already in the sheet, so nothing is skipped.
The GitHub `scrape.yml` workflow is manual-only.

### Position and "7 states" tabs

`offers` is the main tab the scraper writes to. To add read-only views of
it, run once:

```
GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)" SHEET_ID=... python -m cfb_offers --setup-tabs
```

This creates one tab per position group (QB, RB, WR, TE, OL, DL, LB, DB, ATH,
K/P), a `Blank` tab for rows with no position, and a `7 states` tab for
players from GA, NC, SC, TN, AL, FL and VA. Each is a live `FILTER` formula
over `offers`, so it updates instantly as rows are added or pruned. A player
listing several positions (e.g. `WR/DB`) appears on each matching tab, and
each tab lists the newest tweets first. Don't edit these tabs by hand; edit
`offers` instead. Re-running `--setup-tabs` is safe.

`--setup-tabs` also styles the sheet (formatting only, never data or column
order):

- a `Home` tab with a link and live row count for every tab, a color legend
  and tips (rebuilt on each run, so don't type in it)
- tabs ordered and colored by group: `offers` navy, `visits` teal, offense
  blue, defense red, athletes purple, special teams green, `7 states` orange,
  `pruned` gray and last
- friendly header labels (`Player Name`, `Tweet URL`), a colored header row
  and alternating row colors
- commit rows highlighted green, decommits red, flips in bold orange; on
  `visits`, upcoming visits yellow and official visits bold
- column widths set; `event_key`, `tweet_id` and `scraped_at` hidden (not
  removed - unhide them from the column menu)
- a `Newest first` filter view on `offers` and `visits` (Data > Filter views), which sorts
  for you without reordering the rows the scraper reads

Each run replaces the banding and conditional formatting on these tabs, so
put any of your own on a separate tab.

### Visits tab

The visits job searches for recruit visits and writes them to a `visits`
tab, created automatically on the first run. `visit_status` says which kind:

- `completed` - the thank-you post after a visit ("thank you @Coach for
  having me", "thanks for letting me visit", "great official visit at ...").
- `upcoming` - a visit happening now or still to come ("I will be visiting
  Georgia today", "on campus at Clemson", "I'll be at the Michigan game",
  "OV set for 6/12", a game day / junior day / visit invite). Past-tense
  wording ("had a great game day visit", "yesterday", "thanks for the
  invite" after a visit) makes it `completed` instead.

The opponent in "X vs Y", "against Y", "win over Y" or "beat Y" never gets
a visit row.

One row per player + school (a repeat visit folds its source into
`also_reported_by`). When a thank-you post follows an upcoming row, the row
turns `completed` and the thank-you post's URL goes in `notes`
("completed: <url>"); the row keeps its original tweet. Rows from before
`visit_status` existed are left blank, which means completed. `visit_type`
is `official` for "official visit"/"#OV"/"OV" wording, else `unofficial`.
Camp invites aren't visits, and a tweet that mentions an offer or commitment
is never a visit (it goes to `offers` instead, if it qualifies).

To backfill only visits (e.g. right after this tab was added):

```
X_COOKIES="$(cat x_cookies.txt)" GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)" SHEET_ID=... \
  python -m cfb_offers --backfill-days 5 --visits-only --dump-raw visits_backfill.jsonl
```

`--dry-run` and `--from-raw` write visits to `<out>_visits.csv` next to
`--out`. `--prune-sheet` doesn't touch the `visits` tab.

### Cleaning up the sheet after a rule change

Rows already in the sheet aren't re-checked when the filters improve. To
re-check them against the current rules, point `--prune-sheet` at the
`--dump-raw` file from the run that wrote them:

```
X_COOKIES="$(cat x_cookies.txt)" GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)" SHEET_ID=... \
  python -m cfb_offers --prune-sheet backfill.jsonl           # report counts only
  python -m cfb_offers --prune-sheet backfill.jsonl --apply   # move them
```

With `--apply`, rejected rows and duplicate reports of the same event are
**moved to a `pruned` tab** (with `pruned_at` and `prune_reason` columns),
never just deleted - a row is only removed from `offers` after it has been
copied. Events the current rules find in the raw file that the sheet lacks
(e.g. a commit re-credited to the right school, or a newly tracked school)
are **added**. Rows whose tweets aren't in the raw file are left alone.

To backfill only some schools (e.g. after adding new ones to
`config/schools.yaml`), add `--only-schools "Auburn,Ole Miss"` to a
`--backfill-days` run. Stop any
running scrape first so the two don't edit the sheet at the same time.

## Local development

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

`classify.py`, `sources.py`, `profile.py`, and `dedupe.py` are pure functions
tested against fixtures in `tests/fixtures/` — no network calls in tests, and
twscrape is always mocked.

To try the scraper against real X data without touching the sheet, first get
your cookie string (see step 2 above). Keep it out of shell history by
reading it from a gitignored file (`x_cookies.txt` is already in
`.gitignore`) rather than typing it inline:

```
echo 'auth_token=...; ct0=...' > x_cookies.txt
X_COOKIES=$(cat x_cookies.txt) \
  python -m cfb_offers --dry-run --since-days 2 --out sample.csv
```

Eyeball `sample.csv` for false positives/negatives and tune the regex in
`classify.py`/`sources.py` as needed.

## Row schema

`event_key, event_type, is_flip, school, player_name, player_handle,
class_year, position, height, weight, high_school, state, source_type,
source_handle, tweet_id, tweet_date, tweet_url, tweet_text,
also_reported_by, notes, scraped_at`

The `visits` tab: `event_key, visit_type, school, player_name, player_handle,
class_year, position, height, weight, high_school, state, source_type,
source_handle, tweet_id, tweet_date, tweet_url, tweet_text,
also_reported_by, notes, scraped_at, visit_status`

One row per `(player, school, event_type)` (or `(player, school)` for a visit). If the same event is reported by
multiple sources (e.g. the player and a recruiting reporter both post it),
the earliest tweet is kept and the other sources are appended to
`also_reported_by`.

## Privacy

This repo is public. Nothing here commits `x_cookies.txt`, the twscrape
accounts DB, the service account key, or any CSV output (see `.gitignore`) —
the accounts DB is written to a tempdir, not the repo. GitHub Actions logs
print counts only (`tweets_seen`, `events`, etc.) — never tweet text or
player names/handles.
