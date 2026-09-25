# Google Sheets setup

The scraper writes to a private Google Sheet via a service account (no
`gcloud` CLI required — everything below is done by clicking through the
Google Cloud Console and Google Sheets in a browser).

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/).
2. Top bar → project dropdown → **New Project**.
3. Give it any name (e.g. `cfb-offers`) and click **Create**.
4. Once created, make sure it's selected in the project dropdown.

No billing account is required — the Sheets/Drive APIs and this project's
call volume (a handful of requests every few hours) are covered by the free
tier.

## 2. Enable the APIs

The scraper's library (`gspread`) authenticates with a scope that covers
both Sheets and Drive, so enable both:

1. In the Console, go to **APIs & Services → Library**.
2. Search for **Google Sheets API** → click it → **Enable**.
3. Search for **Google Drive API** → click it → **Enable**.

## 3. Create a service account and JSON key

1. **APIs & Services → Credentials → Create Credentials → Service account**.
2. Give it a name (e.g. `cfb-offers-bot`) and click **Create and Continue**.
3. Skip the optional "grant access" / "grant users access" steps → **Done**.
4. Click the new service account in the list → **Keys** tab → **Add Key →
   Create new key** → choose **JSON** → **Create**.
5. A `.json` file downloads automatically — this is your
   `GOOGLE_SERVICE_ACCOUNT_JSON` secret. It contains a private key, so
   handle it like a password (see **Security notes** below).
6. Note the service account's email address, shown on its details page and
   inside the JSON as `client_email` — it looks like
   `cfb-offers-bot@<project-id>.iam.gserviceaccount.com`. You'll share the
   sheet with this address next.

## 4. Create the Google Sheet and share it

1. Go to [sheets.google.com](https://sheets.google.com/) and create a new,
   blank spreadsheet (any name — the scraper creates/uses a worksheet
   tab named `offers` inside it, regardless of the spreadsheet's title).
2. Click **Share** (top right) → paste the service account's email from
   step 3.6 → set its role to **Editor** → **Send** (no notification email
   actually goes out to a service account, that's fine). The sheet stays
   private to everyone else — only accounts you explicitly share it with
   can see it.
3. Copy the **SHEET_ID** out of the sheet's URL:
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit` — it's the long
   string between `/d/` and `/edit`.

## 5. Run the local check

From the repo root, with the JSON key saved locally as `sa.json` (see the
security note below before doing this):

```
GOOGLE_SERVICE_ACCOUNT_JSON="$(cat sa.json)" SHEET_ID=<your sheet id> \
  .venv/bin/python -m cfb_offers --check-sheet
```

This authenticates, opens the sheet, creates the `offers` worksheet/header
row if needed (or checks the existing header matches the schema), writes
and deletes a throwaway test row to confirm editor access, and prints
counts only (never sheet contents). On success it ends with
`check_sheet: PASSED`. If the service account can't access the sheet, the
error message names the service account's email and tells you to share the
sheet with it as Editor — go back to step 4.2.

`--check-sheet` never requires `X_COOKIES`.

## 6. Add both values as GitHub secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**, and add:

- `GOOGLE_SERVICE_ACCOUNT_JSON` — the full contents of `sa.json`
- `SHEET_ID` — the ID copied in step 4.3

(`X_COOKIES` is a separate secret covered in the main README.)

## Security notes

- **Never commit the JSON key.** `.gitignore` already excludes `sa.json`
  and `*service-account*.json` — keep your local copy named to match one of
  those patterns (or add your own filename to `.gitignore` if you name it
  something else) and double check `git status` before committing anything
  after you've saved it locally.
- If the key is ever exposed (committed, pasted somewhere public, leaked
  in a log), **rotate it immediately**: Console → the service account →
  **Keys** → delete the old key → **Add Key → Create new key** → update
  the `GOOGLE_SERVICE_ACCOUNT_JSON` secret with the new JSON. The old key
  stops working the moment it's deleted.
- The service account only ever has access to sheets you explicitly share
  with it — deleting its access from a sheet (or deleting the service
  account) revokes that immediately too.
