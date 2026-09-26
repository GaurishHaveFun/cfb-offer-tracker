# CFB Offer Tracker

Scrapes X/Twitter every 6 hours for high-school football players announcing
**offers, commitments, and decommitments** (including flips) from 26
CFB programs, parses the player's bio, dedupes multi-source
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
listing several positions (e.g. `WR/DB`) appears on each matching tab. Don't
edit these tabs by hand; edit `offers` instead. Re-running `--setup-tabs` is
safe.

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
copied. Rows whose tweets aren't in the raw file are left alone. Stop any
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

One row per `(player, school, event_type)`. If the same event is reported by
multiple sources (e.g. the player and a recruiting reporter both post it),
the earliest tweet is kept and the other sources are appended to
`also_reported_by`.

## Privacy

This repo is public. Nothing here commits `x_cookies.txt`, the twscrape
accounts DB, the service account key, or any CSV output (see `.gitignore`) —
the accounts DB is written to a tempdir, not the repo. GitHub Actions logs
print counts only (`tweets_seen`, `events`, etc.) — never tweet text or
player names/handles.
