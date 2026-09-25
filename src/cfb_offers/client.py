"""twscrape API wrapper: cookie auth from X_COOKIES, jittered pacing.

twscrape handles rate limits and account rotation itself: it locks an
account per endpoint until its X-provided reset time, then either waits or
rotates to another pool account. We only add a ceiling on that wait (so a
single rate-limited account can't eat the whole GitHub Actions timeout) and
a loud failure when no account is left active (stale/invalid cookies).
"""
from __future__ import annotations

import asyncio
import os
import random
import tempfile

from twscrape import API, AccountsPool

SEARCH_PAGE_SIZE = 20  # twscrape's SearchTimeline page size
# Scheduled/incremental runs are time-capped (30-min Actions timeout) and
# only need to cover a few hours' worth of tweets, so they default to a low
# page cap. Backfill runs (--backfill, 30-day window) run locally with no
# time cap, so they get a much larger one.
PAGES_PER_QUERY_DEFAULT = 2
PAGES_PER_QUERY_BACKFILL = 10
JITTER_RANGE = (2, 6)

# get_for_queue_or_wait polls for up to wait_timeout seconds before giving up
# on a locked account and moving to the next one (or failing if none are
# left). 20 minutes leaves headroom under the 30-minute Actions timeout for
# the rest of the run.
ACCOUNT_WAIT_TIMEOUT = 20 * 60.0


def search_limit(max_pages: int) -> int:
    """Converts our pages-per-query cap into the tweet-count `limit` that
    `api.search` expects (it paginates internally, unlike twikit's
    page-at-a-time `result.next()`).
    """
    return max_pages * SEARCH_PAGE_SIZE


async def jitter() -> None:
    await asyncio.sleep(random.uniform(*JITTER_RANGE))


async def add_accounts(pool: AccountsPool, cookies_text: str) -> int:
    """Adds one pool account per non-blank line of `cookies_text` (the
    X_COOKIES secret), each a cookie string like `auth_token=...; ct0=...`.
    Accounts are named acct1, acct2, ... so twscrape can rotate between them
    when one gets rate-limited. password/email are dummies: twscrape only
    uses them for password-based relogin, which cookie accounts never do -
    a cookie with auth_token and ct0 marks the account active immediately.
    """
    lines = [line.strip() for line in cookies_text.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("X_COOKIES is empty")
    for i, cookie_line in enumerate(lines, start=1):
        username = f"acct{i}"
        await pool.add_account(
            username,
            password="unused",
            email=f"{username}@example.invalid",
            email_password="unused",
            cookies=cookie_line,
        )
    return len(lines)


async def require_active_accounts(pool: AccountsPool) -> None:
    """Raises loudly if no pool account is active. Covers both stale cookies
    up front (an added account failed `has_required_cookies`) and accounts
    that twscrape marked inactive mid-run (e.g. a 401 on every account).
    Actions failure = the alert, so this must not fail silently.
    """
    infos = await pool.accounts_info()
    active = [i for i in infos if i["active"]]
    if not active:
        raise RuntimeError(
            "twscrape: no active accounts - cookies expired. Re-copy "
            "auth_token/ct0 from browser DevTools and update the X_COOKIES "
            "secret."
        )


async def build_api(
    cookies_text: str,
    db_path: str | None = None,
    wait_timeout: float = ACCOUNT_WAIT_TIMEOUT,
) -> API:
    """Builds an authenticated API from X_COOKIES. The accounts DB lives in a
    tempdir (never the repo) unless `db_path` is given explicitly.
    """
    if db_path is None:
        db_path = os.path.join(tempfile.mkdtemp(prefix="cfb-offers-"), "accounts.db")

    api = API(db_path, wait_timeout=wait_timeout)
    await add_accounts(api.pool, cookies_text)
    await require_active_accounts(api.pool)
    return api
