"""client.py tests use a real twscrape AccountsPool against a tmp sqlite file
- no network. add_account() with cookies is a pure local DB write (no login
request), so this exercises the real cookie-parsing/activation logic instead
of re-implementing it in a mock.
"""
import asyncio

import pytest
from twscrape import AccountsPool

from cfb_offers.client import add_accounts, build_api, require_active_accounts


def _pool(tmp_path) -> AccountsPool:
    return AccountsPool(db_file=str(tmp_path / "accounts.db"))


def test_add_accounts_parses_single_cookie_line(tmp_path):
    pool = _pool(tmp_path)
    n = asyncio.run(add_accounts(pool, "auth_token=abc123; ct0=def456"))
    assert n == 1

    accounts = asyncio.run(pool.get_all())
    assert len(accounts) == 1
    assert accounts[0].username == "acct1"
    assert accounts[0].active is True
    assert accounts[0].cookies == {"auth_token": "abc123", "ct0": "def456"}


def test_add_accounts_handles_multiple_lines(tmp_path):
    pool = _pool(tmp_path)
    cookies_text = "\n".join(
        [
            "auth_token=aaa; ct0=111",
            "auth_token=bbb; ct0=222",
            "auth_token=ccc; ct0=333",
        ]
    )
    n = asyncio.run(add_accounts(pool, cookies_text))
    assert n == 3

    accounts = sorted(asyncio.run(pool.get_all()), key=lambda a: a.username)
    assert [a.username for a in accounts] == ["acct1", "acct2", "acct3"]
    assert all(a.active for a in accounts)


def test_add_accounts_skips_blank_lines(tmp_path):
    pool = _pool(tmp_path)
    cookies_text = "\nauth_token=aaa; ct0=111\n\n  \nauth_token=bbb; ct0=222\n"
    n = asyncio.run(add_accounts(pool, cookies_text))
    assert n == 2


def test_add_accounts_raises_on_empty_input(tmp_path):
    pool = _pool(tmp_path)
    with pytest.raises(RuntimeError, match="X_COOKIES is empty"):
        asyncio.run(add_accounts(pool, "   \n  \n"))


def test_require_active_accounts_passes_when_one_active(tmp_path):
    pool = _pool(tmp_path)
    asyncio.run(add_accounts(pool, "auth_token=abc; ct0=def"))
    asyncio.run(require_active_accounts(pool))  # should not raise


def test_require_active_accounts_raises_on_stale_cookies(tmp_path):
    pool = _pool(tmp_path)
    # missing ct0 -> has_required_cookies() is False -> account stays inactive
    asyncio.run(add_accounts(pool, "auth_token=abc-only"))
    with pytest.raises(RuntimeError, match="cookies expired"):
        asyncio.run(require_active_accounts(pool))


def test_require_active_accounts_raises_when_no_accounts_at_all(tmp_path):
    pool = _pool(tmp_path)
    with pytest.raises(RuntimeError, match="cookies expired"):
        asyncio.run(require_active_accounts(pool))


def test_build_api_wires_accounts_into_pool(tmp_path):
    db_path = str(tmp_path / "accounts.db")
    api = asyncio.run(build_api("auth_token=abc; ct0=def\nauth_token=ghi; ct0=jkl", db_path=db_path))

    accounts = sorted(asyncio.run(api.pool.get_all()), key=lambda a: a.username)
    assert [a.username for a in accounts] == ["acct1", "acct2"]
    assert all(a.active for a in accounts)


def test_build_api_raises_on_stale_cookies(tmp_path):
    db_path = str(tmp_path / "accounts.db")
    # a well-formed cookie string missing ct0 parses fine but never activates
    with pytest.raises(RuntimeError, match="cookies expired"):
        asyncio.run(build_api("auth_token=abc-only", db_path=db_path))


def test_build_api_uses_tempdir_when_no_db_path_given(monkeypatch, tmp_path):
    # never write the accounts DB into the repo/cwd - confirm it lands under
    # a tempdir instead of the current default "accounts.db".
    api = asyncio.run(build_api("auth_token=abc; ct0=def"))
    assert str(tmp_path) not in api.pool._db_file  # sanity: it's not our fixture dir either
    assert api.pool._db_file != "accounts.db"
    assert api.pool._db_file.endswith("accounts.db")


def test_jitter_sleeps_within_range(monkeypatch):
    from cfb_offers import client

    slept = {}

    async def fake_sleep(seconds):
        slept["seconds"] = seconds

    monkeypatch.setattr(client.asyncio, "sleep", fake_sleep)
    asyncio.run(client.jitter())
    assert client.JITTER_RANGE[0] <= slept["seconds"] <= client.JITTER_RANGE[1]
