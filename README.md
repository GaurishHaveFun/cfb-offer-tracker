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

Run this once, locally, before the schedule takes over — it forces the
30-day lookback window (with a larger per-query page cap, since it isn't
time-capped like Actions) and writes straight to the real sheet:

```
X_COOKIES="$(cat x_cookies.txt)" \
GOOGLE_SERVICE_ACCOUNT_JSON="$(cat service-account.json)" \
SHEET_ID=... \
  python -m cfb_offers --backfill
```

This needs the same three secrets as Actions (`X_COOKIES`,
`GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`), set locally rather than as
GitHub secrets. `--backfill` is a no-op if the environment looks like a
GitHub Actions runner (`GITHUB_ACTIONS=true`) — it always clamps to a
short window there instead, so the 30-day sweep only ever runs on your
machine.

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
