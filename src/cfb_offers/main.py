"""CLI entry point: scrapes X for offer/commit/decommit tweets (and completed
recruit visits, for the 'visits' tab) and writes them to the configured
Google Sheet (or a local CSV with --dry-run).

Never logs tweet text or player info — this repo is public. Only counts.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import sys
from dataclasses import dataclass

from cfb_offers import classify, sheets, sources
from cfb_offers.client import (
    PAGES_PER_QUERY_BACKFILL,
    PAGES_PER_QUERY_DEFAULT,
    build_api,
    jitter,
    require_active_accounts,
    search_limit,
)
from cfb_offers.config import School, env, load_schools
from cfb_offers.dedupe import add_source, canonicalize, dedupe_events, make_event_key
from cfb_offers.models import OfferRecord, VisitRecord
from cfb_offers.profile import parse_bio
from cfb_offers.queries import (
    VISIT_PHRASE_CLAUSE,
    backfill_slices,
    build_all_queries,
    build_slice_queries,
    build_visit_queries,
)

BLANK_PROFILE = {
    "name": "", "handle": "", "class_year": "", "position": "", "height": "",
    "weight": "", "high_school": "", "state": "",
}

BACKFILL_DAYS = 30
# Scheduled Actions runs are time-capped and can't afford a 30-day window.
# If we'd otherwise end up scraping more than this many days (empty sheet,
# or a big gap since the last row), clamp to this and tell the user to run
# --backfill locally instead.
CI_MAX_SINCE_DAYS = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape X for CFB offer/commit/decommit tweets.")
    p.add_argument("--since-days", type=int, default=None, help="override the lookback window")
    p.add_argument(
        "--repair-shifted",
        action="store_true",
        help="move rows that were written one column to the right back into place",
    )
    p.add_argument(
        "--setup-tabs",
        action="store_true",
        help="create/refresh the read-only position, Blank and '7 states' tabs (live filters over 'offers')",
    )
    p.add_argument(
        "--backfill-days",
        type=int,
        default=None,
        metavar="N",
        help=(
            "local long backfill: search the last N days one week at a time, paging each "
            "week until it runs out (e.g. --backfill-days 90). Not allowed in CI"
        ),
    )
    p.add_argument(
        "--only-schools",
        default=None,
        metavar="NAMES",
        help=(
            'search only these schools, comma-separated (e.g. "Auburn,Ole Miss"); '
            "classification still considers every school"
        ),
    )
    only = p.add_mutually_exclusive_group()
    only.add_argument(
        "--visits-only",
        action="store_true",
        help=(
            "search only the visit queries and write only the 'visits' tab; the "
            "lookback window comes from the 'visits' tab's newest tweet"
        ),
    )
    only.add_argument(
        "--offers-only",
        action="store_true",
        help="search only the offer/commit/decommit queries and write only the 'offers' tab",
    )
    p.add_argument(
        "--resume-from-slice",
        type=int,
        default=1,
        metavar="K",
        help="with --backfill-days: skip weeks before slice K (as printed in the progress lines)",
    )
    p.add_argument(
        "--prune-sheet",
        metavar="RAW_JSONL",
        default=None,
        help=(
            "re-check sheet rows against the current rules using the tweets saved in a "
            "--dump-raw file; reports counts only unless --apply is given"
        ),
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="with --prune-sheet: move rejected/duplicate rows to the 'pruned' tab",
    )
    p.add_argument("--dry-run", action="store_true", help="write to a local CSV instead of the sheet")
    p.add_argument("--out", default="sample.csv", help="CSV path for --dry-run")
    p.add_argument("--max-pages", type=int, default=None, help="override pages-per-query cap")
    p.add_argument(
        "--backfill",
        action="store_true",
        help=(
            f"force the {BACKFILL_DAYS}-day backfill window with a larger page cap "
            "(run this locally, not in CI - see README)"
        ),
    )
    p.add_argument(
        "--dump-raw",
        default=None,
        metavar="PATH",
        help=(
            "also write every fetched tweet (text, author bio, mentions, "
            "resolved player profiles) as JSONL to PATH, for offline tuning "
            "with --from-raw - requires network, like a normal run"
        ),
    )
    p.add_argument(
        "--from-raw",
        default=None,
        metavar="PATH",
        help=(
            "reclassify a --dump-raw JSONL file with no network calls "
            "(implies --dry-run: writes --out, never touches the sheet)"
        ),
    )
    p.add_argument(
        "--check-sheet",
        action="store_true",
        help=(
            "verify the Google Sheet is reachable and the service account "
            "has editor access, then exit (no X_COOKIES needed)"
        ),
    )
    return p.parse_args(argv)


def is_ci() -> bool:
    return env("GITHUB_ACTIONS") == "true"


def resolve_window(
    *,
    explicit_since_days: int | None,
    backfill_flag: bool,
    ci: bool,
    latest_tweet_date: str | None,
    now: dt.datetime,
) -> tuple[int, bool, str | None]:
    """Picks the lookback window. Pure function (no sheet/env access) so it's
    easy to unit test. Returns (since_days, is_backfill, warning):

    - an explicit --since-days always wins, no matter what else is true.
    - --backfill forces the BACKFILL_DAYS window and the larger page cap,
      but only outside CI: a scheduled run must never do the 30-day sweep.
    - otherwise the window comes from the sheet's latest tweet date (or
      BACKFILL_DAYS if the sheet is empty).
    - in CI, an empty sheet or a >CI_MAX_SINCE_DAYS window gets clamped to
      CI_MAX_SINCE_DAYS, with a warning to run --backfill locally instead.
    """
    if explicit_since_days is not None:
        return explicit_since_days, False, None

    if backfill_flag and not ci:
        return BACKFILL_DAYS, True, None

    if latest_tweet_date is None:
        since_days, empty_sheet = BACKFILL_DAYS, True
    else:
        latest_dt = dt.datetime.fromisoformat(latest_tweet_date.replace("Z", "+00:00"))
        since_days, empty_sheet = (now - latest_dt).days + 1, False

    if ci and (empty_sheet or since_days > CI_MAX_SINCE_DAYS):
        warning = (
            f"since_days={since_days} clamped to {CI_MAX_SINCE_DAYS} in CI "
            "(sheet is empty or the gap is large - run `python -m cfb_offers "
            "--backfill` locally to fully backfill)"
        )
        return CI_MAX_SINCE_DAYS, False, warning

    return since_days, False, None


def resolve_max_pages(explicit_max_pages: int | None, is_backfill: bool) -> int:
    """--max-pages always wins; otherwise backfill runs get the larger cap."""
    if explicit_max_pages is not None:
        return explicit_max_pages
    return PAGES_PER_QUERY_BACKFILL if is_backfill else PAGES_PER_QUERY_DEFAULT


def _all_school_handles(schools_cfg: list[School]) -> list[str]:
    return [h for s in schools_cfg for h in s.mention_handles]


async def _resolve_player_profile(api, cache: dict, handle: str, name: str) -> dict:
    """Returns {name, handle, class_year, position, height, weight, high_school, state}."""
    if not handle:
        return {**BLANK_PROFILE, "name": name}
    if handle.lower() in cache:
        return cache[handle.lower()]
    try:
        user = await api.user_by_login(handle)
        if user is None:
            raise ValueError("no such user")
        bio = parse_bio(user.rawDescription, user.location)
        profile = {"name": user.displayname, "handle": user.username, **bio}
    except Exception:
        profile = {**BLANK_PROFILE, "name": name, "handle": handle}
    cache[handle.lower()] = profile
    return profile


async def process_tweet(
    *,
    handle: str,
    text: str,
    author_desc: str,
    author_location: str,
    author_displayname: str,
    mentions: list[str],
    tweet_id: str,
    tweet_date: str,
    tweet_url: str,
    schools_cfg: list[School],
    school_handles: list[str],
    resolve_profile,
) -> tuple[str, list[OfferRecord | VisitRecord], dict | None]:
    """The classification + record-building pipeline for one tweet, shared
    by the live run and --from-raw replay (only `resolve_profile` differs
    between them - a network lookup live, a lookup into recorded data
    offline).

    Returns (status, records, player_profile). records are OfferRecords,
    or VisitRecords for a completed visit (a tweet is never both - see
    classify.classify_visit). status is one of
    "school_account", "unclassified", "noise", "ok" - used for the
    tweets_seen/dropped counters and for what --dump-raw records.
    player_profile is the resolved player info (for --dump-raw), or None
    when nothing was resolved.
    """
    if classify.is_school_account(handle, schools_cfg):
        return "school_account", [], None

    # A configured coach handle is never eligible for the first-person
    # override below (a coach's own bio shouldn't get read as a player's).
    is_known_coach = sources.is_coach_handle(handle, schools_cfg)
    author_type = sources.classify_author(
        author_desc, schools_cfg, text="" if is_known_coach else text
    )
    if author_type is None and is_known_coach:
        author_type = "coach"
    if author_type is None:
        return "unclassified", [], None

    events = classify.classify_tweet(text, schools_cfg, bio=author_desc)
    visits = [] if events else classify.classify_visit(text, schools_cfg, bio=author_desc)
    if not events and not visits:
        return "noise", [], None

    if author_type == "player":
        bio = parse_bio(author_desc, author_location)
        player_profile = {"name": author_displayname, "handle": handle, **bio}
    else:
        exclude = school_handles + [handle]
        mention = sources.resolve_player_mention(text, mentions, exclude)
        player_profile = await resolve_profile(mention.handle, mention.name)
        if not player_profile.get("handle") and not player_profile.get("name"):
            # A reporter/coach tweet with no resolvable player is a roundup,
            # not an announcement - never worth a row.
            return "noise", [], player_profile

    shared = dict(
        player_name=player_profile["name"],
        player_handle=player_profile["handle"],
        class_year=player_profile["class_year"],
        position=player_profile["position"],
        height=player_profile["height"],
        weight=player_profile["weight"],
        high_school=player_profile["high_school"],
        state=player_profile["state"],
        source_type=author_type,
        source_handle=handle,
        tweet_id=tweet_id,
        tweet_date=tweet_date,
        tweet_url=tweet_url,
        tweet_text=text,
        scraped_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )
    records: list[OfferRecord | VisitRecord] = []
    for ev in events:
        key = make_event_key(player_profile["handle"], player_profile["name"], ev.school, ev.event_type)
        records.append(
            OfferRecord(
                event_key=key,
                event_type=ev.event_type,
                is_flip=ev.is_flip,
                school=ev.school,
                notes=ev.notes,
                **shared,
            )
        )
    for v in visits:
        key = make_event_key(player_profile["handle"], player_profile["name"], v.school, "visit")
        records.append(
            VisitRecord(event_key=key, visit_type=v.visit_type, school=v.school, visit_status=v.status, **shared)
        )
    return "ok", records, player_profile


def split_records(
    records: list[OfferRecord | VisitRecord],
) -> tuple[list[OfferRecord], list[VisitRecord]]:
    """(offer/commit/decommit records, visit records)."""
    return (
        [r for r in records if isinstance(r, OfferRecord)],
        [r for r in records if isinstance(r, VisitRecord)],
    )


def visits_csv_path(out: str) -> str:
    """Where --dry-run/--from-raw write visits: next to --out, "_visits" added."""
    stem, dot, ext = out.rpartition(".")
    return f"{stem}_visits.{ext}" if dot else f"{out}_visits"


def _write_csv(path: str, columns: list[str], records) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for r in records:
            writer.writerow(r.as_row())


def _write_jsonl(path: str, rows: list[dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _append_jsonl(path: str, rows: list[dict]) -> None:
    with open(path, "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _read_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# Pages per week-slice query in a --backfill-days run: high enough that a
# week "runs out" long before the cap for all but the very busiest groups.
SLICE_MAX_PAGES = 25


def select_schools(schools_cfg: list[School], only: str | None) -> list[School]:
    """The schools to search for. --only-schools narrows the searches (e.g.
    to backfill newly added schools); matching is case-insensitive and an
    unknown name is an error rather than a silently empty run."""
    if not only:
        return schools_cfg
    by_name = {s.name.lower(): s for s in schools_cfg}
    wanted = [n.strip() for n in only.split(",") if n.strip()]
    unknown = [n for n in wanted if n.lower() not in by_name]
    if unknown:
        raise SystemExit(
            f"--only-schools: unknown school(s): {', '.join(unknown)}. "
            f"Known: {', '.join(s.name for s in schools_cfg)}"
        )
    return [by_name[n.lower()] for n in wanted]


def search_plan(
    schools_cfg: list[School],
    since_days: int,
    backfill_days: int | None,
    resume_from_slice: int = 1,
    offers: bool = True,
    visits: bool = True,
) -> list[tuple[str, str]]:
    """The (progress_label, query) list for a run: the offer queries and/or
    the visit queries. A normal run is one query per school group over the
    whole window (no labels). A --backfill-days run is every group once per
    week, newest week first, labelled with counts/dates only, so it can be
    resumed with --resume-from-slice."""
    if not backfill_days:
        queries = build_all_queries(schools_cfg, since_days) if offers else []
        if visits:
            queries += build_visit_queries(schools_cfg, since_days)
        return [("", q) for q in queries]
    slices = backfill_slices(backfill_days)
    plan = []
    for k, (since, until) in enumerate(slices, start=1):
        if k < resume_from_slice:
            continue
        queries = build_slice_queries(schools_cfg, since, until) if offers else []
        if visits:
            queries += build_slice_queries(schools_cfg, since, until, VISIT_PHRASE_CLAUSE)
        for j, q in enumerate(queries, start=1):
            label = f"slice {k}/{len(slices)} ({since}..{until}) query {j}/{len(queries)}"
            plan.append((label, q))
    return plan


async def _replay(raw_tweets: list[dict], schools_cfg: list[School], school_handles: list[str]):
    """Runs saved tweets through process_tweet offline. Returns (records
    before dedupe, tweets_seen, noise_dropped, unclassified_dropped)."""
    records: list[OfferRecord] = []
    tweets_seen = 0
    tweets_dropped_noise = 0
    tweets_dropped_unclassified = 0

    for rt in raw_tweets:
        tweets_seen += 1
        author = rt.get("author", {})
        recorded_profile = rt.get("player_profile")

        async def _offline_resolve(handle, name, _recorded=recorded_profile):
            # Reuse the looked-up profile only if the (possibly re-tuned)
            # resolution still picks the same account; otherwise the replay
            # would silently keep the old player choice.
            if _recorded and handle and _recorded.get("handle", "").lower() == handle.lower():
                return _recorded
            return {**BLANK_PROFILE, "name": name, "handle": handle}

        status, recs, _ = await process_tweet(
            handle=author.get("username", ""),
            text=rt.get("text", ""),
            author_desc=author.get("rawDescription", ""),
            author_location=author.get("location", ""),
            author_displayname=author.get("displayname", ""),
            mentions=rt.get("mentions", []),
            tweet_id=rt.get("id", ""),
            tweet_date=rt.get("date", ""),
            tweet_url=f"https://x.com/{author.get('username', '')}/status/{rt.get('id', '')}",
            schools_cfg=schools_cfg,
            school_handles=school_handles,
            resolve_profile=_offline_resolve,
        )
        if status == "unclassified":
            tweets_dropped_unclassified += 1
        elif status == "noise":
            tweets_dropped_noise += 1
        records.extend(recs)

    return records, tweets_seen, tweets_dropped_noise, tweets_dropped_unclassified


@dataclass
class ResyncPlan:
    prune: list[tuple[int, dict[str, str], str]]  # (row_num, row, reason)
    also_updates: dict[int, str]  # kept row_num -> merged also_reported_by
    key_updates: dict[int, str]  # kept row_num -> current event_key
    adds: list[OfferRecord]  # events the current rules find that the sheet lacks


def plan_resync(
    sheet_rows: list[tuple[int, dict[str, str]]],
    replayed: list[OfferRecord],
    known_tweet_ids: set[str],
) -> ResyncPlan:
    """Brings the sheet in line with the current rules, given a fresh replay
    of every tweet in a --dump-raw file. Pure, so it's testable without a
    sheet.

    - A row whose tweet isn't in the raw file is left alone (can't re-check).
    - A row the current rules no longer produce is pruned (e.g. a commit
      that was credited to the wrong school).
    - Rows the current rules say are the same event keep only the earliest
      tweet; the rest are pruned and their sources folded into the kept
      row's also_reported_by. Kept rows get the current event_key, so later
      scrapes dedupe against it.
    - Events the current rules find in the raw file that no remaining row
      covers are added (the corrected school for a pruned commit, or tweets
      the old rules missed).
    """
    replayed = canonicalize(replayed)
    by_tweet = {(r.tweet_id, r.school, r.event_type): r for r in replayed}
    prune: list[tuple[int, dict[str, str], str]] = []
    groups: dict[str, list[tuple[int, dict[str, str]]]] = {}
    covered: set[str] = set()
    for row_num, row in sheet_rows:
        if row.get("tweet_id") not in known_tweet_ids:
            covered.add(row.get("event_key", ""))
            continue
        rec = by_tweet.get((row.get("tweet_id"), row.get("school"), row.get("event_type")))
        if rec is None:
            prune.append((row_num, row, "rejected by current rules"))
            continue
        groups.setdefault(rec.event_key, []).append((row_num, row))

    also_updates: dict[int, str] = {}
    key_updates: dict[int, str] = {}
    for key, rows in groups.items():
        covered.add(key)
        rows.sort(key=lambda x: (x[1].get("tweet_date", ""), x[1].get("tweet_id", "")))
        keep_num, keep = rows[0]
        if keep.get("event_key") != key:
            key_updates[keep_num] = key
        also = keep.get("also_reported_by", "")
        for row_num, row in rows[1:]:
            prune.append((row_num, row, f"duplicate of tweet {keep.get('tweet_id')}"))
            for h in [row.get("source_handle", "")] + row.get("also_reported_by", "").split(","):
                h = h.strip()
                if h and h != keep.get("source_handle"):
                    also = add_source(also, h)
        if also != keep.get("also_reported_by", ""):
            also_updates[keep_num] = also

    adds = [r for r in dedupe_events(replayed) if r.event_key not in covered]
    return ResyncPlan(prune, also_updates, key_updates, adds)


def plan_prune(sheet_rows, replayed, known_tweet_ids):
    """(prune, also_updates) part of plan_resync."""
    plan = plan_resync(sheet_rows, replayed, known_tweet_ids)
    return plan.prune, plan.also_updates


async def _prune_sheet(args: argparse.Namespace) -> list[OfferRecord]:
    """--prune-sheet: re-checks the sheet against the current rules using a
    --dump-raw file. Counts only are printed. With --apply: pruned rows are
    moved to the 'pruned' tab (copied there first, then removed from
    'offers'), and events the current rules find that the sheet lacks are
    added."""
    schools_cfg = load_schools()
    school_handles = _all_school_handles(schools_cfg)
    raw_tweets = _read_jsonl(args.prune_sheet)
    known_ids = {rt.get("id", "") for rt in raw_tweets}

    ws = sheets.open_sheet(
        env("GOOGLE_SERVICE_ACCOUNT_JSON", required=True), env("SHEET_ID", required=True)
    )
    sheet_rows = sheets.read_rows(ws)
    replayed, *_ = await _replay(raw_tweets, schools_cfg, school_handles)
    replayed, _visits = split_records(replayed)  # the visits tab isn't pruned
    plan = plan_resync(sheet_rows, replayed, known_ids)

    rejected = sum(1 for *_, reason in plan.prune if reason.startswith("rejected"))
    unchecked = sum(1 for _, row in sheet_rows if row.get("tweet_id") not in known_ids)
    print(
        f"prune_sheet: rows={len(sheet_rows)} to_prune={len(plan.prune)} "
        f"(rejected={rejected} duplicates={len(plan.prune) - rejected}) "
        f"to_add={len(plan.adds)} also_reported_by_updates={len(plan.also_updates)} "
        f"key_updates={len(plan.key_updates)} not_in_raw_file={unchecked}"
    )
    if not args.apply:
        print("prune_sheet: report only - rerun with --apply to make these changes")
        return plan.adds

    # Cell updates first: pruning deletes rows, which shifts row numbers.
    if plan.also_updates:
        sheets.update_column(ws, "also_reported_by", plan.also_updates)
    if plan.key_updates:
        sheets.update_column(ws, "event_key", plan.key_updates)
    sheets.move_to_pruned(ws, plan.prune, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    if plan.adds:
        sheets.append_at_column_a(ws, [r.as_row() for r in plan.adds])
    print(f"prune_sheet: moved {len(plan.prune)} rows to the 'pruned' tab, added {len(plan.adds)} rows")
    return plan.adds


async def _run_from_raw(args: argparse.Namespace) -> list[OfferRecord]:
    """Reclassifies a --dump-raw JSONL file, no network calls at all - for
    offline tuning of the classify/sources/profile rules without burning
    rate limits. Always writes --out (like --dry-run); never touches the
    sheet.
    """
    schools_cfg = load_schools()
    school_handles = _all_school_handles(schools_cfg)
    raw_tweets = _read_jsonl(args.from_raw)

    records, tweets_seen, tweets_dropped_noise, tweets_dropped_unclassified = await _replay(
        raw_tweets, schools_cfg, school_handles
    )

    records, visit_records = (dedupe_events(rs) for rs in split_records(records))

    _write_csv(args.out, OfferRecord.columns(), records)
    _write_csv(visits_csv_path(args.out), VisitRecord.columns(), visit_records)

    print(
        f"from_raw={args.from_raw} tweets_seen={tweets_seen} "
        f"noise_dropped={tweets_dropped_noise} "
        f"unclassified_dropped={tweets_dropped_unclassified} events={len(records)} "
        f"visits={len(visit_records)}",
        file=sys.stderr,
    )
    return records


async def run(argv: list[str] | None = None) -> list[OfferRecord]:
    args = parse_args(argv)
    if args.repair_shifted:
        ws = sheets.open_sheet(
            env("GOOGLE_SERVICE_ACCOUNT_JSON", required=True), env("SHEET_ID", required=True)
        )
        print(f"repair_shifted: moved {sheets.repair_shifted_rows(ws)} rows back into place")
        return []
    if args.setup_tabs:
        ws = sheets.open_sheet(
            env("GOOGLE_SERVICE_ACCOUNT_JSON", required=True), env("SHEET_ID", required=True)
        )
        titles = sheets.setup_view_tabs(ws)
        print(f"setup_tabs: {len(titles)} tabs ready: {', '.join(titles)}")
        return []
    if args.check_sheet:
        sa_json = env("GOOGLE_SERVICE_ACCOUNT_JSON", required=True)
        sheet_id = env("SHEET_ID", required=True)
        sheets.check_sheet(sa_json, sheet_id)
        return []
    if args.from_raw:
        return await _run_from_raw(args)
    if args.prune_sheet:
        return await _prune_sheet(args)

    schools_cfg = load_schools()
    school_handles = _all_school_handles(schools_cfg)

    ws = None
    ci = is_ci()
    if args.backfill_days and ci:
        raise SystemExit("--backfill-days is for local runs only (it can take hours)")
    now = dt.datetime.now(dt.timezone.utc)
    if not args.dry_run:
        cookies_text = env("X_COOKIES", required=True)
        sa_json = env("GOOGLE_SERVICE_ACCOUNT_JSON", required=True)
        sheet_id = env("SHEET_ID", required=True)
        ws = sheets.open_sheet(sa_json, sheet_id)
        visits_ws = sheets.visits_worksheet(ws)
        # The window starts from the newest tweet in the tab this run writes,
        # so offers and visits can run on separate schedules.
        if args.since_days is not None:
            latest = None
        elif args.visits_only:
            latest = sheets.latest_tweet_date(visits_ws, sheets.VISIT_HEADER)
        else:
            latest = sheets.latest_tweet_date(ws)
        since_days, is_backfill, warning = resolve_window(
            explicit_since_days=args.since_days,
            backfill_flag=args.backfill,
            ci=ci,
            latest_tweet_date=latest,
            now=now,
        )
        if warning:
            print(warning, file=sys.stderr)
    else:
        # --dry-run never touches a sheet, so there's no "latest tweet date"
        # / empty-sheet clamp to apply - just --since-days, --backfill, or a
        # small 2-day default.
        cookies_text = env("X_COOKIES", required=True)
        if args.since_days is not None:
            since_days, is_backfill = args.since_days, False
        elif args.backfill and not ci:
            since_days, is_backfill = BACKFILL_DAYS, True
        else:
            since_days, is_backfill = 2, False

    if args.backfill_days:
        since_days, is_backfill = args.backfill_days, True
    max_pages = (
        args.max_pages or SLICE_MAX_PAGES
        if args.backfill_days
        else resolve_max_pages(args.max_pages, is_backfill)
    )
    limit = search_limit(max_pages)
    search_schools = select_schools(schools_cfg, args.only_schools)
    plan = search_plan(
        search_schools, since_days, args.backfill_days, args.resume_from_slice,
        offers=not args.visits_only,
        visits=not args.offers_only,
    )

    api = await build_api(cookies_text)
    profile_cache: dict[str, dict] = {}
    records: list[OfferRecord] = []
    visit_records: list[VisitRecord] = []
    tweets_seen = 0
    tweets_dropped_noise = 0
    tweets_dropped_unclassified = 0

    # A tweet about e.g. an Alabama/LSU cross-rivalry story can surface from
    # more than one packed query; dedupe by tweet id so it isn't processed
    # (and counted) twice.
    seen_ids: set[str] = set()
    appended_total = updated_total = 0
    visits_appended_total = visits_updated_total = 0
    if args.dump_raw and args.resume_from_slice <= 1:
        open(args.dump_raw, "w").close()  # truncate; rows are appended per query

    # Process and save after every query rather than once at the end, so a
    # crash, Ctrl+C, or sleep mid-backfill only loses the in-flight query.
    for progress, query in plan:
        if progress:
            print(progress, file=sys.stderr, flush=True)
        batch = []
        async for tweet in api.search(query, limit=limit):
            tweets_seen += 1
            if tweet.id_str not in seen_ids:
                seen_ids.add(tweet.id_str)
                batch.append(tweet)

        batch_records: list[OfferRecord | VisitRecord] = []
        dump_rows: list[dict] = []
        for tweet in batch:
            author = tweet.user
            handle = author.username
            text = tweet.rawContent
            # twscrape exposes @mentions as structured UserRefs (mentionedUsers)
            # instead of making us regex-scan the tweet text for @handles.
            mentions = [u.username for u in tweet.mentionedUsers]

            async def _resolve(h, n):
                return await _resolve_player_profile(api, profile_cache, h, n)

            status, recs, player_profile = await process_tweet(
                handle=handle,
                text=text,
                author_desc=author.rawDescription,
                author_location=author.location,
                author_displayname=author.displayname,
                mentions=mentions,
                tweet_id=tweet.id_str,
                tweet_date=tweet.date.isoformat(),
                tweet_url=f"https://x.com/{handle}/status/{tweet.id_str}",
                schools_cfg=schools_cfg,
                school_handles=school_handles,
                resolve_profile=_resolve,
            )
            if status == "unclassified":
                tweets_dropped_unclassified += 1
            elif status == "noise":
                tweets_dropped_noise += 1
            batch_records.extend(recs)

            if args.dump_raw:
                dump_rows.append(
                    {
                        "id": tweet.id_str,
                        "text": text,
                        "date": tweet.date.isoformat(),
                        "author": {
                            "username": handle,
                            "displayname": author.displayname,
                            "rawDescription": author.rawDescription,
                            "location": author.location,
                        },
                        "mentions": mentions,
                        "player_profile": player_profile,
                    }
                )

        if args.dump_raw and dump_rows:
            _append_jsonl(args.dump_raw, dump_rows)
        batch_offers, batch_visits = split_records(batch_records)
        if args.visits_only:
            batch_offers = []
        if args.offers_only:
            batch_visits = []
        records.extend(batch_offers)
        visit_records.extend(batch_visits)
        # sync_records dedupes against rows already in the sheet (including
        # ones written by earlier queries in this run), so per-query syncs
        # never duplicate an event.
        if ws is not None and batch_offers:
            appended, updated = sheets.sync_records(ws, dedupe_events(batch_offers))
            appended_total += appended
            updated_total += updated
        if ws is not None and batch_visits:
            appended, updated = sheets.sync_records(
                visits_ws, dedupe_events(batch_visits), sheets.VISIT_HEADER
            )
            visits_appended_total += appended
            visits_updated_total += updated
        await jitter()

    # Stale cookies fail an individual request silently (twscrape just marks
    # the account inactive and moves on); catch that here so the Actions run
    # fails loudly instead of quietly returning zero results.
    await require_active_accounts(api.pool)

    records = dedupe_events(records)
    visit_records = dedupe_events(visit_records)

    if args.dry_run:
        _write_csv(args.out, OfferRecord.columns(), records)
        _write_csv(visits_csv_path(args.out), VisitRecord.columns(), visit_records)
    else:
        print(
            f"appended={appended_total} updated={updated_total} "
            f"visits_appended={visits_appended_total} visits_updated={visits_updated_total}"
        )

    # counts only — never tweet text or player info (public repo / Actions log).
    print(
        f"tweets_seen={tweets_seen} noise_dropped={tweets_dropped_noise} "
        f"unclassified_dropped={tweets_dropped_unclassified} events={len(records)} "
        f"visits={len(visit_records)}",
        file=sys.stderr,
    )
    return records


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
