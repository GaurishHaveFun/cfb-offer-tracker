# GitHub Actions setup

This repo runs `.github/workflows/scrape.yml` on a schedule (every 6 hours,
staggered at `:17` past the hour) plus `workflow_dispatch` for manual runs,
and `.github/workflows/tests.yml` on every push/PR (no secrets involved).

## 1. Add the three secrets

The scraper needs `X_COOKIES`, `GOOGLE_SERVICE_ACCOUNT_JSON`, and `SHEET_ID`
(see the main [README](../README.md) for how to obtain each value). Add them
as encrypted repo secrets with the `gh` CLI, run from your machine — never
paste secret values into a workflow file or a commit:

```sh
gh secret set X_COOKIES < x_cookies.txt
gh secret set GOOGLE_SERVICE_ACCOUNT_JSON < sa.json
gh secret set SHEET_ID
```

The first two read the secret value from a local file (so it never touches
your shell history); the third has no file argument, so `gh` will prompt you
to type/paste the sheet ID interactively. You can also do this from Repo →
Settings → Secrets and variables → Actions → "New repository secret".

Neither `x_cookies.txt` nor `sa.json` should ever be committed — both are
already covered by `.gitignore`. Run `scripts/check_no_secrets.sh` before
any `git push` as a final check (see below).

## 2. Trigger and watch a manual run

```sh
gh workflow run scrape.yml
gh run watch
```

`gh run watch` with no arguments attaches to the most recent run; pass a
specific run ID (`gh run list --workflow=scrape.yml` to find it) if another
run started in the meantime. You can also pass the optional inputs the
workflow exposes, e.g. `gh workflow run scrape.yml -f since_days=1` to
override the lookback window for one run, or `-f max_pages=5` to cap pages
per query.

## 3. What a failure email means

GitHub emails the repo owner automatically when a scheduled or manual run
fails. The most common cause is **expired cookies**: `main.py` calls
`require_active_accounts` after scraping, which fails the run loudly (a
non-zero exit) if twscrape has marked every pool account inactive, rather
than silently returning zero results. If you see that failure:

1. Log into the burner X account in a browser.
2. DevTools → Application → Cookies → `https://x.com`, copy fresh
   `auth_token` and `ct0` values.
3. Update the secret: `gh secret set X_COOKIES < x_cookies.txt` (overwrites
   the old value).
4. Re-run: `gh workflow run scrape.yml` and `gh run watch`.

Other failure causes: a missing/empty secret (the workflow's "Verify
required secrets are set" step fails fast with a clear message, before any
scraping happens), or a Google Sheets API error (check the service
account still has Editor access to the sheet, and that the Sheets API is
still enabled on the GCP project).

## 4. The 60-day inactivity rule

GitHub automatically **disables scheduled (`schedule:`) workflows after 60
days with no activity on the repository** (pushes, merges, etc. — not
scheduled runs themselves). If the sheet suddenly stops getting updated and
there's no failure email, check Actions → "Scrape CFB offers" for a banner
saying the schedule was disabled, and re-enable it there, or via:

```sh
gh workflow enable scrape.yml
```

There's no auto-commit "keepalive" workflow here on purpose (a bot commit
just to keep the schedule alive is unnecessary noise on a public repo, and
it's easy to forget about and lose track of why it exists). Instead, treat
`gh workflow enable scrape.yml` as a normal part of picking the project back
up after a long pause. If you know upfront the repo will regularly go 60+
days without any other activity, the simplest deliberate option is a
separate, very-low-frequency (e.g. monthly) scheduled workflow that does
nothing but touch a `KEEPALIVE` file and open/merge a PR — set that up only
if you actually hit this in practice.

## 5. Why logs are counts-only

Recruits tracked here are often minors, and this repo is public — anyone can
read the Actions logs. `main.py` only ever prints summary counts
(`appended=`, `updated=`, `tweets_seen=`, `noise_dropped=`,
`unclassified_dropped=`, `events=`), never tweet text, player names,
handles, or bios. The job summary step in `scrape.yml` parses only those
count lines out of the run's output — it never echoes raw scraper output,
and nothing from a run is ever uploaded as a build artifact.
