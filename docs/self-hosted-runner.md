# Self-hosted runner (a Mac at home)

X answers searches from GitHub's own servers with HTTP 403, but not from a
home internet connection. So `scrape.yml` runs on a **self-hosted runner**:
GitHub's runner program installed on a Mac at home. GitHub still does the
scheduling (every 6 hours), keeps the logs and run history, emails on
failure, and holds the secrets; the Mac just does the work, from its home IP.

`tests.yml` stays on GitHub's servers.

The Mac has to be on and awake for a run to happen. A run scheduled while it
sleeps waits in the queue and starts when it wakes (GitHub drops a queued run
after 24 hours). Each run starts from the newest tweet already in the sheet,
so a missed run is caught up by the next one.

## 1. Install the runner on the Mac

On the Mac that will do the scraping, signed in as the account that will
own the runner:

1. Repo → **Settings → Actions → Runners → New self-hosted runner**, choose
   **macOS** and the Mac's architecture (Apple silicon = ARM64, Intel = x64).
2. Run the **Download** commands GitHub shows, into e.g. `~/actions-runner`.
3. Run the **Configure** command GitHub shows, adding a name and the
   `cfb-scraper` label (the workflow only runs on a runner with that label):
   ```sh
   ./config.sh --url https://github.com/<owner>/cfb-offer-tracker \
     --token <token from that page> --name cfb-mac --labels cfb-scraper --unattended
   ```
4. Install it as a service, so it starts on its own after a reboot or login:
   ```sh
   ./svc.sh install
   ./svc.sh start
   ```
5. Back on **Settings → Actions → Runners**, the runner should show as
   **Idle** with the labels `self-hosted`, `macOS`, `cfb-scraper`.

The workflow installs Python 3.12 itself (`actions/setup-python`), so the Mac
needs nothing else. Raw tweets from each run are kept on the Mac in
`~/cfb-offer-tracker-data/runs/` for 60 days (for `--prune-sheet`); they're
never uploaded to GitHub.

Keep the Mac from sleeping when it's plugged in: **System Settings →
Battery (or Energy) → Options → Prevent automatic sleeping when the display
is off**.

## 2. Add the secrets

Add `X_COOKIES`, `GOOGLE_SERVICE_ACCOUNT_JSON` and `SHEET_ID` as repo
secrets - see [github-actions-setup.md](github-actions-setup.md#1-add-the-three-secrets).

## 3. Test a run, then turn off launchd

```sh
gh workflow run scrape.yml
gh run watch
```

Once a run succeeds, remove the old launchd job from any Mac that has it, so
two scrapers never edit the sheet at the same time:

```sh
launchctl bootout gui/$(id -u)/com.cfboffers.scrape
rm ~/Library/LaunchAgents/com.cfboffers.scrape.plist
```

## 4. Protections (this repo is public)

A self-hosted runner runs whatever job GitHub sends it, and on a public repo
anyone can open a pull request whose workflow asks for `runs-on:
self-hosted`. These settings keep strangers' code off the Mac:

- **Approve every outside pull request's workflows.** Settings → Actions →
  General → *Approval for running fork pull request workflows from
  contributors* → **Require approval for all external contributors**. Or:
  ```sh
  gh api -X PUT repos/<owner>/cfb-offer-tracker/actions/permissions/fork-pr-contributor-approval \
    -f approval_policy=all_external_contributors
  ```
  Then **never approve** a pull request run that changes anything under
  `.github/workflows/` unless you've read the change.
- **Only `scrape.yml` targets the runner**, and it only runs on `schedule`
  and manual `workflow_dispatch`, which only people with write access can
  start. Never add `pull_request` or `push` triggers to it, and never
  point `tests.yml` at `self-hosted`.
- **Workflow token is read-only** (Settings → Actions → General → Workflow
  permissions → *Read repository contents*), and both workflows ask for
  only `contents: read`.
- **Only give write access to people you trust.** Anyone who can push can
  change a workflow and run code on the Mac.
- **Optional:** run the runner under a separate, standard (non-admin) macOS
  user, so a bad job can't reach the owner's own files.

## 5. Scheduled runs stop after 60 days without a commit

See [github-actions-setup.md](github-actions-setup.md#4-the-60-day-inactivity-rule):
re-enable with `gh workflow enable scrape.yml`.

## Moving it to another Mac

Remove the runner on the old Mac (`./svc.sh stop && ./svc.sh uninstall &&
./config.sh remove --token <token>`, with a removal token from the Runners
page), then do step 1 on the new one. The secrets live in GitHub, so nothing
else has to be copied. To hand over the repo as well, transfer it (Settings
→ General → Transfer ownership); the new owner then registers the runner
against the repo's new URL.
