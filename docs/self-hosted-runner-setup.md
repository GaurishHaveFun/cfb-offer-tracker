# Running the scrape on a friend's computer (self-hosted GitHub runner)

X blocks searches from GitHub's own cloud runners (HTTP 403), so the
scheduled scrape has to run from a home internet connection. A
**self-hosted runner** is GitHub's small background app installed on a
home computer (Windows, Linux or Mac): GitHub keeps the schedule, the
secrets, the logs and the failure emails, and the computer just runs the
job from its home IP.

Two people are involved:

- **Owner** - you, the repo owner (needs admin on the GitHub repo).
- **Host** - your friend, whose computer runs the jobs.

The schedule (same as the Mac setup, Eastern time): offers at 12:17am,
6:17am, 12:17pm, 6:17pm; visits 3 hours later at 3:17am, 9:17am, 3:17pm,
9:17pm. GitHub's cron is in UTC, so these shift by an hour when daylight
saving time ends - harmless.

---

## Step 1 - Make the repo private (owner)

The repo is public, and GitHub warns against self-hosted runners on
public repos: anyone could open a pull request from a fork whose workflow
runs code **on your friend's computer**. A private repo closes that hole.

GitHub → repo → **Settings → General → Danger Zone → Change visibility →
Make private**.

Self-hosted runner minutes are free on private repos.

## Step 2 - Merge the visits work to `main` (owner)

Scheduled workflows only run from the default branch. Commit and push the
`visits-tab` branch, open a PR, and merge it into `main`.

## Step 3 - Add the secrets (owner)

From the repo folder on your Mac (skip any that are already set):

```sh
gh secret set X_COOKIES < x_cookies.txt
gh secret set GOOGLE_SERVICE_ACCOUNT_JSON < sa.json
gh secret set SHEET_ID < sheet_id.txt
```

The secrets live in GitHub, not on your friend's computer - nothing needs
to be copied to them by hand.

## Step 4 - Update `.github/workflows/scrape.yml` (owner)

Replace the whole file with the version below, commit, and push to
`main`. Changes from the current file:

- `runs-on: [self-hosted, cfb-home]` instead of `ubuntu-latest`.
- Two schedules: one for offers, one for visits, plus a `mode` choice for
  manual runs.
- `timeout-minutes: 60`, because a rate-limit wait can take 15+ minutes.
- The existing `concurrency` group makes a late run wait for the other one
  to finish, so offers and visits never overlap.
- `shell: bash` everywhere, and `python` instead of `python3`, so it also
  works on a Windows host.

```yaml
name: Scrape CFB offers

# Runs on a self-hosted runner on a home connection - X answers searches
# from GitHub's (data-center) IPs with 403. See docs/self-hosted-runner-setup.md.
# Cron is UTC: offers 04/10/16/22:17 UTC = 00/06/12/18:17 EDT; visits 3 hours later.
on:
  schedule:
    - cron: "17 4,10,16,22 * * *"  # offers
    - cron: "17 1,7,13,19 * * *"   # visits
  workflow_dispatch:
    inputs:
      mode:
        description: "What to scrape"
        required: true
        default: offers
        type: choice
        options: [offers, visits, both]
      since_days:
        description: "Override the lookback window in days (leave blank for the default sheet-based window, clamped to 3 days in CI)"
        required: false
        type: string
      max_pages:
        description: "Override the pages-per-query cap (leave blank for 5)"
        required: false
        type: string

permissions:
  contents: read

concurrency:
  group: cfb-offers-scrape
  cancel-in-progress: false

defaults:
  run:
    shell: bash

jobs:
  scrape:
    runs-on: [self-hosted, cfb-home]
    timeout-minutes: 60
    steps:
      - name: Check out repository
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Set up Python
        uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b # v5.3.0
        with:
          python-version: "3.12"
          cache: "pip"
          cache-dependency-path: pyproject.toml

      - name: Verify required secrets are set
        env:
          X_COOKIES: ${{ secrets.X_COOKIES }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
          SHEET_ID: ${{ secrets.SHEET_ID }}
        run: |
          missing=()
          [ -z "${X_COOKIES}" ] && missing+=("X_COOKIES")
          [ -z "${GOOGLE_SERVICE_ACCOUNT_JSON}" ] && missing+=("GOOGLE_SERVICE_ACCOUNT_JSON")
          [ -z "${SHEET_ID}" ] && missing+=("SHEET_ID")
          if [ "${#missing[@]}" -gt 0 ]; then
            echo "::error::Missing required secret(s): ${missing[*]}. Set them with 'gh secret set <NAME>' - see docs/github-actions-setup.md."
            exit 1
          fi

      - name: Mask derived secret values
        env:
          SHEET_ID: ${{ secrets.SHEET_ID }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
        run: |
          echo "::add-mask::${SHEET_ID}"
          client_email=$(python -c "
          import json, os
          try:
              print(json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']).get('client_email', ''))
          except Exception:
              pass
          ")
          if [ -n "${client_email}" ]; then
            echo "::add-mask::${client_email}"
          fi

      - name: Install package
        run: pip install .

      - name: Pick offers or visits
        id: mode
        env:
          SCHEDULE: ${{ github.event.schedule }}
          INPUT_MODE: ${{ inputs.mode }}
        run: |
          if [ "${SCHEDULE}" = "17 1,7,13,19 * * *" ]; then mode=visits
          elif [ -n "${SCHEDULE}" ]; then mode=offers
          else mode="${INPUT_MODE:-offers}"; fi
          echo "mode=${mode}" >> "$GITHUB_OUTPUT"

      # Counts only are ever logged - never tweet text or player info.
      - name: Run scraper
        env:
          X_COOKIES: ${{ secrets.X_COOKIES }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
          SHEET_ID: ${{ secrets.SHEET_ID }}
          MODE: ${{ steps.mode.outputs.mode }}
          SINCE_DAYS: ${{ inputs.since_days }}
          MAX_PAGES: ${{ inputs.max_pages }}
        run: |
          set -o pipefail
          args=(--max-pages "${MAX_PAGES:-5}")
          if [ "${MODE}" != "both" ]; then args+=("--${MODE}-only"); fi
          if [ -n "${SINCE_DAYS}" ]; then args+=(--since-days "${SINCE_DAYS}"); fi
          python -m cfb_offers "${args[@]}" 2>&1 | tee scrape_output.txt

      - name: Write job summary (counts only)
        if: always()
        env:
          MODE: ${{ steps.mode.outputs.mode }}
        run: |
          {
            echo "### CFB ${MODE} scrape"
            echo
            echo '```'
            if [ -f scrape_output.txt ]; then
              grep -E '^(appended=|tweets_seen=|since_days=.*clamped)' scrape_output.txt || echo "no counts line found in scraper output"
            else
              echo "scraper did not produce output (see job logs)"
            fi
            echo '```'
          } >> "$GITHUB_STEP_SUMMARY"
          rm -f scrape_output.txt
```

## Step 5 - Prepare the computer (host)

- **Windows:** install [Git for Windows](https://git-scm.com/download/win)
  (it provides the `bash` the workflow uses).
- **Linux:** Ubuntu or Debian works best; `actions/setup-python` downloads
  prebuilt Python for those. On other distros, install Python 3.12
  yourself first.
- **Mac:** nothing extra.
- **Power settings:** the computer must be **on, awake and online** at the
  run times. Turn off sleep while plugged in (Windows: Settings → System →
  Power → "When plugged in, put my device to sleep after: Never"). A run
  that comes due while the computer is off waits in GitHub's queue and
  starts when the computer is back (if that's within 24 hours). Only one
  run waits at a time - a newer one replaces an older waiting one - and
  that's fine: the lookback starts from the newest tweet already in the
  sheet (up to 3 days), so nothing is missed.

## Step 6 - Register the runner (owner + host, together)

1. **Owner:** GitHub → repo → **Settings → Actions → Runners → New
   self-hosted runner**. Pick the host's OS and architecture. GitHub shows
   a block of download and `config` commands with a one-time token.
2. **Owner:** send those commands to the host privately (not in a public
   place). **The token expires after 1 hour**, so do this when your friend
   is ready - if it expires, just reload the page for a new one.
3. **Host:** run the download commands in a new folder (e.g.
   `C:\actions-runner` or `~/actions-runner`), then the `config` command.
   When it asks:
   - **Runner group:** press Enter (default).
   - **Runner name:** press Enter, or something like `friend-pc`.
   - **Additional labels:** type `cfb-home` - the workflow only runs on a
     runner with this label.
   - **Work folder:** press Enter (default).
   - **Windows only - "run as service?":** answer **Y**, so it starts
     automatically with the computer.
4. **Host, Linux/Mac only:** install it as a service so it survives
   reboots, from the runner folder:

   ```sh
   sudo ./svc.sh install
   sudo ./svc.sh start
   ```

5. **Owner:** back in **Settings → Actions → Runners**, the runner should
   show as **Idle** (green).

## Step 7 - Test it (owner)

```sh
gh workflow run scrape.yml -f mode=visits
gh run watch
```

Then once more with `-f mode=offers`. Each run's summary shows the counts
(`appended=… visits_appended=…`). Check that new rows showed up in the
sheet.

## Step 8 - Turn off the Mac schedule (owner)

Only after Step 7 succeeds - otherwise both machines scrape the same
accounts and write the same sheet:

```sh
launchctl bootout gui/$(id -u)/com.cfboffers.scrape
launchctl bootout gui/$(id -u)/com.cfboffers.visits
rm ~/Library/LaunchAgents/com.cfboffers.{scrape,visits}.plist
```

---

## Day to day

- **Failure emails** go to the owner automatically. The usual cause is
  expired X cookies - see
  [github-actions-setup.md](github-actions-setup.md#3-what-a-failure-email-means)
  for the fix (`gh secret set X_COOKIES < x_cookies.txt`). Nothing needs
  to change on the host's computer.
- **Runner offline** (computer off, or the runner service stopped): runs
  sit queued and fail after 24 hours if it doesn't come back. Check
  Settings → Actions → Runners.
- **60-day rule:** GitHub disables scheduled workflows after 60 days with
  no pushes to the repo. Re-enable with `gh workflow enable scrape.yml`.
- **Backfills** (`--backfill-days`) still run locally, never in Actions -
  the code refuses them in CI.
- **`--prune-sheet`** needs raw tweet files, which Actions runs don't keep
  (logs stay counts-only). Make a raw file locally with `--dump-raw` when
  you need one.

## Removing the runner later

**Host**, from the runner folder (Linux/Mac: `sudo ./svc.sh stop` and
`sudo ./svc.sh uninstall` first):

```sh
./config.sh remove --token <TOKEN>     # Windows: .\config.cmd remove --token <TOKEN>
```

**Owner:** get the removal token from Settings → Actions → Runners → the
runner → **Remove**.
