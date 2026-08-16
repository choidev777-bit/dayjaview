#!/usr/bin/env python3
"""Live news collection, matching, grounded extraction, and persistence worker."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as datetime_time
from importlib import import_module
from os import environ
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

_SEOUL = timezone(timedelta(hours=9))
_LLM_RETRY_INTERVAL = timedelta(minutes=5)


def _default_window_start(now: datetime) -> datetime:
    """Use the prior weekday's 15:30 KST close as a calendar fallback."""

    local = now.astimezone(_SEOUL)
    previous_day = (local - timedelta(days=1)).date()
    while previous_day.weekday() >= 5:
        previous_day -= timedelta(days=1)
    return datetime.combine(previous_day, datetime_time(15, 30), tzinfo=_SEOUL)


def _database_window_start(connection: Any, now: datetime) -> datetime:
    local_date = now.astimezone(_SEOUL).date()
    db = connection.cursor()
    try:
        db.execute(
            """
            SELECT market_date, session_close
              FROM core.reference_trading_calendar_revisions
             WHERE market = 'KRX' AND market_date < %s
               AND is_trading_day AND known_to IS NULL
             ORDER BY market_date DESC
             LIMIT 1
            """,
            (local_date,),
        )
        row = db.fetchone()
    finally:
        db.close()
    if row is None:
        return _default_window_start(now)
    return datetime.combine(row[0], row[1], tzinfo=_SEOUL)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect, match, ground, and persist featured-stock news."
    )
    parser.add_argument(
        "--stock-directory",
        type=Path,
        default=None,
        help='Optional {"stock name": "stock_id"} JSON; PostgreSQL is the default.',
    )
    parser.add_argument(
        "--entity-vocabulary",
        type=Path,
        default=None,
        help="Optional JSON string array.",
    )
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument(
        "--window-start",
        default=None,
        help="Fixed ISO 8601 boundary; default recalculates prior 15:30 KST.",
    )
    parser.add_argument(
        "--market-date",
        default=None,
        help="Fixed YYYY-MM-DD event date; default is current KST date.",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--database-url-env",
        default="NEWS_DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL.",
    )
    return parser.parse_args(argv)


def _decoded(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("stored JSON must be an object")
    return cast(dict[str, object], decoded)


def _stock_directory(connection: Any) -> dict[str, str]:
    db = connection.cursor()
    try:
        db.execute(
            """
            SELECT name, stock_code
              FROM (
                    SELECT current_name AS name, stock_code, 0 AS priority
                      FROM core.infostock_stocks
                    UNION ALL
                    SELECT observation.source_name AS name, stock.stock_code,
                           (observation.authority <> 'CURRENT_MEMBERSHIP')::int
                               AS priority
                      FROM core.infostock_stock_name_observations AS observation
                      JOIN core.infostock_stocks AS stock
                        ON stock.stock_id = observation.stock_id
                   ) AS names
             WHERE btrim(name) <> ''
             ORDER BY priority, name, stock_code
            """
        )
        result: dict[str, str] = {}
        for name, stock_code in db.fetchall():
            result.setdefault(str(name), f"KRX:{stock_code}")
        return result
    finally:
        db.close()


def _entity_vocabulary(connection: Any) -> tuple[str, ...]:
    db = connection.cursor()
    try:
        db.execute(
            "SELECT current_name FROM core.infostock_themes WHERE is_active "
            "ORDER BY current_name"
        )
        return tuple(str(row[0]) for row in db.fetchall())
    finally:
        db.close()


def _leader_map(connection: Any, market_date: date) -> dict[str, tuple[str, str]]:
    db = connection.cursor()
    try:
        db.execute(
            """
            SELECT payload
              FROM serving.realtime_snapshots
             WHERE topic = 'theme_rank_snapshot' AND market_date = %s
             ORDER BY generated_at DESC, sequence DESC
             LIMIT 1
            """,
            (market_date,),
        )
        row = db.fetchone()
    finally:
        db.close()
    if row is None:
        return {}
    items = _decoded(row[0]).get("items")
    if not isinstance(items, list):
        return {}
    leaders: dict[str, tuple[str, str]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("leader"), dict):
            continue
        event_id = str(item.get("eventId") or "")
        leader = item["leader"]
        stock_id = str(leader.get("stockId") or "")
        name = str(leader.get("name") or "")
        if event_id and stock_id and name:
            leaders[event_id] = (stock_id, name)
    return leaders


def _theme_metadata(
    connection: Any, canonical_theme_id: str
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    db = connection.cursor()
    try:
        db.execute(
            """
            SELECT theme.current_name, stock.stock_code, stock.current_name
              FROM core.infostock_themes AS theme
         LEFT JOIN core.infostock_theme_stock_memberships AS membership
                ON membership.theme_id = theme.theme_id
               AND membership.observed_to IS NULL
         LEFT JOIN core.infostock_stocks AS stock
                ON stock.stock_id = membership.stock_id
             WHERE %s = 'thm_' || theme.source_theme_id
             ORDER BY membership.source_rank, stock.stock_code
            """,
            (canonical_theme_id,),
        )
        rows = db.fetchall()
    finally:
        db.close()
    if not rows:
        return None, (), ()
    return (
        str(rows[0][0]),
        tuple(f"KRX:{row[1]}" for row in rows if row[1] is not None),
        tuple(str(row[2]) for row in rows if row[2] is not None),
    )


def _active_contexts(connection: Any, market_date: date) -> tuple[Any, ...]:
    catalyst = import_module("packages.catalyst")
    events = import_module("packages.events")
    leaders = _leader_map(connection, market_date)
    db = connection.cursor()
    try:
        db.execute(
            """
            SELECT current.event_json,
                   COALESCE(
                       (SELECT min(log.occurred_at)
                          FROM event.state_logs AS log
                         WHERE log.event_id = current.event_id
                           AND log.axis = 'LIFECYCLE'
                           AND log.to_status = 'ACTIVE'),
                       current.first_detected_at
                   ) AS activated_at,
                   ARRAY(
                       SELECT DISTINCT revision.catalyst_key
                         FROM news.evidence_revisions AS revision
                         JOIN event.events AS prior
                           ON prior.event_id = revision.event_id
                        WHERE prior.canonical_theme_id = current.canonical_theme_id
                          AND prior.market_date < current.market_date
                          AND revision.catalyst_key IS NOT NULL
                   ) AS known_catalyst_keys
              FROM event.events AS current
             WHERE current.market_date = %s
               AND current.lifecycle_status IN ('ACTIVE', 'WEAKENING')
             ORDER BY current.event_id
            """,
            (market_date,),
        )
        rows = db.fetchall()
    finally:
        db.close()
    contexts: list[Any] = []
    for raw_event, activated_at, known_keys in rows:
        event = events.CanonicalEvent.from_dict(_decoded(raw_event))
        source_name, stock_ids, stock_names = _theme_metadata(
            connection, event.canonical_theme_id
        )
        leader = leaders.get(event.event_id)
        leader_ids = () if leader is None else (leader[0],)
        leader_names = () if leader is None else (leader[1],)
        contexts.append(
            catalyst.ThemeContext(
                event_id=event.event_id,
                theme_id=event.canonical_theme_id,
                display_name=event.classification.display_name,
                market_date=market_date,
                activated_at=activated_at,
                synonyms=(
                    ()
                    if source_name is None
                    or source_name == event.classification.display_name
                    else (source_name,)
                ),
                leader_names=leader_names,
                leader_stock_ids=leader_ids,
                related_stock_ids=tuple(
                    stock_id for stock_id in stock_ids if stock_id not in leader_ids
                ),
                entities=stock_names,
                known_catalyst_keys=frozenset(str(value) for value in known_keys),
            )
        )
    return tuple(contexts)


def _fixed_window_start(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("window-start must include a timezone")
    return parsed


def _run_postgres(args: argparse.Namespace, database_url: str) -> int:
    news = import_module("packages.news")
    catalyst = import_module("packages.catalyst")
    llm = import_module("packages.llm")
    operator = import_module("packages.operator")
    worker_pipeline = import_module("pipeline")
    psycopg = import_module("psycopg")

    sources = news.create_live_news_sources(environ)
    if not sources:
        raise RuntimeError(
            "NEWS_RSS_SOURCES or NAVER_API_HUB_CLIENT_ID/SECRET is required"
        )
    client = llm.create_live_llm_client(environ)
    if client is None:
        raise RuntimeError("OPENAI_API_KEY is required for the production news worker")
    connection = psycopg.connect(database_url)
    job_id = f"news-live-{datetime.now(_SEOUL).date().isoformat()}"
    try:
        store = news.PostgresNewsStore(cast(Any, connection))
        revisions = catalyst.PostgresEvidenceRepository(cast(Any, connection))
        operator_repository = operator.PostgresOperatorRepository(cast(Any, connection))
        stock_directory = _stock_directory(connection)
        if not stock_directory:
            raise RuntimeError("PostgreSQL stock directory is empty")
        vocabulary = list(_entity_vocabulary(connection))
        if args.entity_vocabulary is not None:
            vocabulary.extend(
                json.loads(args.entity_vocabulary.read_text(encoding="utf-8"))
            )
        ingestor = news.NewsIngestor(
            store,
            stock_directory=stock_directory,
            entity_vocabulary=vocabulary,
        )
        pipeline = worker_pipeline.EvidencePipeline(
            store=store,
            ingestor=ingestor,
            grounding=llm.GroundingService(client),
            revisions=revisions,
            poller=news.SourcePoller(sources),
            supplemental_gate=catalyst.SupplementalSearchGate(),
            supplemental_source=news.create_supplemental_search_source(environ),
        )
        last_refresh: dict[str, datetime] = {}
        fixed_window = _fixed_window_start(args.window_start)
        fixed_market_date = (
            None if args.market_date is None else date.fromisoformat(args.market_date)
        )
        while True:
            now = datetime.now(UTC)
            window_start = fixed_window or _database_window_start(connection, now)
            market_date = fixed_market_date or now.astimezone(_SEOUL).date()
            try:
                collection = pipeline.collect(now=now, window_start=window_start)
                contexts = _active_contexts(connection, market_date)
                outcomes = list(
                    pipeline.on_news_created(
                        collection.report.stored,
                        contexts,
                        now=now,
                        window_start=window_start,
                        sources_degraded=collection.sources_degraded,
                    )
                )
                refreshed = {outcome.event_id for outcome in outcomes}
                for context in contexts:
                    previous = revisions.current(context.event_id)
                    due = (
                        previous is None
                        or (
                            previous.evidence_status
                            is catalyst.EvidenceStatus.SEARCHING
                            and not collection.sources_degraded
                            and now - context.activated_at
                            >= catalyst.NO_NEW_CATALYST_AFTER
                        )
                    )
                    recently_refreshed = (
                        context.event_id in last_refresh
                        and now - last_refresh[context.event_id]
                        < _LLM_RETRY_INTERVAL
                    )
                    if context.event_id in refreshed or not due or recently_refreshed:
                        continue
                    outcomes.append(
                        pipeline.refresh_event(
                            context,
                            now=now,
                            window_start=window_start,
                            sources_degraded=collection.sources_degraded,
                        )
                    )
                    refreshed.add(context.event_id)
                for outcome in outcomes:
                    revisions.save_supporting_records(
                        matches=outcome.matches,
                        evidence=outcome.evidence,
                        llm_record=outcome.llm_record,
                    )
                    last_refresh[outcome.event_id] = now
                final_status = (
                    operator.JobStatus.PARTIAL
                    if collection.sources_degraded
                    else (
                        operator.JobStatus.SUCCEEDED
                        if args.once
                        else operator.JobStatus.RUNNING
                    )
                )
                operator_repository.set_job_status(
                    run_id=job_id,
                    job_type="NEWS_LIVE",
                    status=final_status,
                    now=now,
                    internal_context={
                        "marketDate": market_date.isoformat(),
                        "stored": len(collection.report.stored),
                        "duplicates": len(collection.report.duplicates),
                        "rejected": len(collection.report.rejected),
                        "activeEvents": len(contexts),
                        "evidenceEvaluated": len(outcomes),
                        "degradedSources": list(collection.degraded_source_ids),
                    },
                )
                print(
                    json.dumps(
                        {
                            "at": now.isoformat(),
                            "status": final_status.value,
                            "stored": len(collection.report.stored),
                            "activeEvents": len(contexts),
                            "evidenceEvaluated": len(outcomes),
                            "degraded": list(collection.degraded_source_ids),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                operator_repository.set_job_status(
                    run_id=job_id,
                    job_type="NEWS_LIVE",
                    status=operator.JobStatus.FAILED,
                    now=now,
                    error_code=type(exc).__name__,
                    internal_context={"message": str(exc)[:500]},
                )
                raise
            if args.once:
                return 0 if not collection.sources_degraded else 2
            time.sleep(args.interval_seconds)
    finally:
        connection.close()


def _run_file_fixture(args: argparse.Namespace) -> int:
    if args.stock_directory is None:
        raise RuntimeError(
            "NEWS_DATABASE_URL or --stock-directory is required for news collection"
        )
    news = import_module("packages.news")
    sources = news.create_live_news_sources(environ)
    if not sources:
        raise RuntimeError(
            "NEWS_RSS_SOURCES or NAVER_API_HUB_CLIENT_ID/SECRET is required"
        )
    stock_directory = json.loads(args.stock_directory.read_text(encoding="utf-8"))
    vocabulary: list[str] = []
    if args.entity_vocabulary is not None:
        vocabulary = json.loads(args.entity_vocabulary.read_text(encoding="utf-8"))
    store = news.InMemoryNewsStore()
    ingestor = news.NewsIngestor(
        store, stock_directory=stock_directory, entity_vocabulary=vocabulary
    )
    poller = news.SourcePoller(sources)
    fixed_window = _fixed_window_start(args.window_start)
    while True:
        now = datetime.now(UTC)
        window_start = fixed_window or _default_window_start(now)
        cursors = {
            source_id: cursor
            for source_id in poller.source_ids
            if (cursor := store.get_cursor(source_id)) is not None
        }
        result = poller.poll(cursors, now=now)
        for cursor in result.cursors:
            store.put_cursor(cursor)
        report = ingestor.ingest(result.items, now=now, window_start=window_start)
        print(
            json.dumps(
                {
                    "at": now.isoformat(),
                    "stored": len(report.stored),
                    "duplicates": len(report.duplicates),
                    "rejected": len(report.rejected),
                    "degraded": list(result.degraded_source_ids),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        if args.once:
            return 0
        time.sleep(args.interval_seconds)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if args.interval_seconds <= 0:
        raise ValueError("interval-seconds must be positive")
    database_url = environ.get(str(args.database_url_env), "").strip()
    return (
        _run_postgres(args, database_url)
        if database_url
        else _run_file_fixture(args)
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"news worker failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
