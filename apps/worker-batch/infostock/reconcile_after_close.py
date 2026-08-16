#!/usr/bin/env python3
"""Reconcile intraday events with same-day Infostock Daily confirmations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

_PERCENT = re.compile(r"(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*%")
_UP_WORDS = ("상승", "강세", "급등")


def _today_kst() -> date:
    return (datetime.now(UTC) + timedelta(hours=9)).date()


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run D-13 after-close reconciliation.")
    parser.add_argument("--market-date", default=_today_kst().isoformat())
    parser.add_argument("--database-url-env", default="INFOSTOCK_DATABASE_URL")
    return parser.parse_args(argv)


def _json_object(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("stored Event JSON must be an object")
    return cast(dict[str, object], decoded)


def _events(connection: Any, market_date: date) -> tuple[Any, ...]:
    event_module = import_module("packages.events")
    db = connection.cursor()
    try:
        db.execute(
            "SELECT event_json FROM event.events WHERE market_date = %s "
            "ORDER BY event_id",
            (market_date,),
        )
        return tuple(
            event_module.CanonicalEvent.from_dict(_json_object(row[0]))
            for row in db.fetchall()
        )
    finally:
        db.close()


def _is_up(theme_name: str, description: str, raw_rows: tuple[str, ...]) -> bool:
    if any(word in description for word in _UP_WORDS):
        return True
    for raw in raw_rows:
        columns = tuple(part.strip() for part in raw.split("\t"))
        if not columns or columns[0] != theme_name:
            continue
        for value in columns[1:3]:
            match = _PERCENT.search(value)
            if match is not None:
                return float(match.group(1)) > 0
    return False


def _confirmations(connection: Any, market_date: date) -> tuple[Any, ...]:
    reconciliation = import_module("packages.events.reconciliation")
    event_module = import_module("packages.events")
    db = connection.cursor()
    try:
        db.execute(
            """
            SELECT theme.source_theme_id, theme.current_name,
                   post.source_post_key, post.current_title,
                   revision.normalized_hash, relation.source_order,
                   COALESCE(relation.source_theme_name, theme.current_name),
                   relation.description, relation.raw_text
              FROM core.infostock_daily_posts AS post
              JOIN core.infostock_daily_post_revisions AS revision
                ON revision.daily_post_id = post.daily_post_id
               AND revision.observed_to IS NULL
              JOIN core.infostock_daily_relations AS relation
                ON relation.daily_post_revision_id = revision.daily_post_revision_id
              JOIN core.infostock_themes AS theme
                ON theme.theme_id = relation.theme_id
             WHERE post.published_date = %s
               AND post.visibility_status = 'VISIBLE'
               AND revision.visibility_status = 'VISIBLE'
             ORDER BY theme.source_theme_id, post.source_post_key,
                      relation.source_order
            """,
            (market_date,),
        )
        rows = db.fetchall()
    finally:
        db.close()
    grouped: dict[tuple[str, str], list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row[0]), str(row[2]))].append(tuple(row))
    confirmations: list[Any] = []
    for (source_theme_id, source_post_key), group in sorted(grouped.items()):
        theme_name = str(group[0][6])
        description = next(
            (str(row[7]).strip() for row in group if str(row[7]).strip()),
            "",
        )
        raw_rows = tuple(str(row[8]) for row in group)
        if not _is_up(theme_name, description, raw_rows):
            continue
        title = str(group[0][3])
        content_hash = str(group[0][4])
        confirmations.append(
            reconciliation.AfterCloseConfirmation(
                canonical_theme_id=f"thm_{source_theme_id}",
                event_date=market_date,
                direction="UP",
                summary=description or title,
                lineage=event_module.LineageRef(
                    kind="INFOSTOCK_DAILY_FEATURED_THEME",
                    identifier=f"{source_post_key}:{source_theme_id}",
                    content_hash=content_hash,
                ),
            )
        )
    return tuple(confirmations)


def _review_id(review_type: str, target_id: str, market_date: date) -> str:
    digest = hashlib.sha256(
        f"{review_type}:{target_id}:{market_date.isoformat()}".encode("utf-8")
    ).hexdigest()[:32]
    return f"rev_{digest}"


def _resolve_review(repository: Any, review_id: str, *, now: datetime) -> None:
    operator = import_module("packages.operator")
    review = repository.get_review(review_id)
    if review is not None and review.review_status is operator.ReviewStatus.PENDING:
        repository.resolve_review(
            review_id,
            resolution={"source": "AFTER_CLOSE_RECONCILIATION"},
            now=now,
        )


def _open_reviews(
    repository: Any,
    run: Any,
    events: tuple[Any, ...],
    confirmations: tuple[Any, ...],
    *,
    now: datetime,
) -> None:
    operator = import_module("packages.operator")
    for event in events:
        if event.reconciliation_status.value == "MATCHED":
            _resolve_review(
                repository,
                _review_id(
                    "AFTER_CLOSE_UNMATCHED", event.event_id, run.market_date
                ),
                now=now,
            )
    event_themes = {event.canonical_theme_id for event in events}
    for confirmation in confirmations:
        if confirmation.canonical_theme_id in event_themes:
            _resolve_review(
                repository,
                _review_id(
                    "INTRADAY_EVENT_MISSING",
                    confirmation.lineage.identifier,
                    run.market_date,
                ),
                now=now,
            )
    for result in run.unmatched:
        event_id = result.event.event_id
        repository.open_review(
            operator.OperatorReview(
                review_id=_review_id(
                    "AFTER_CLOSE_UNMATCHED", event_id, run.market_date
                ),
                review_type="AFTER_CLOSE_UNMATCHED",
                review_status=operator.ReviewStatus.PENDING,
                target_id=event_id,
                reason_code="NO_INFOSTOCK_CONFIRMATION",
                version=1,
                created_at=now,
                resolved_at=None,
                internal_context={"marketDate": run.market_date.isoformat()},
            )
        )
    for confirmation in run.unmatched_confirmations:
        target_id = confirmation.canonical_theme_id
        repository.open_review(
            operator.OperatorReview(
                review_id=_review_id(
                    "INTRADAY_EVENT_MISSING",
                    confirmation.lineage.identifier,
                    run.market_date,
                ),
                review_type="INTRADAY_EVENT_MISSING",
                review_status=operator.ReviewStatus.PENDING,
                target_id=target_id,
                reason_code="INFOSTOCK_WITHOUT_INTRADAY_EVENT",
                version=1,
                created_at=now,
                resolved_at=None,
                internal_context={
                    "marketDate": run.market_date.isoformat(),
                    "lineage": confirmation.lineage.identifier,
                },
            )
        )


def _record_after_close_evidence(
    repository: Any,
    events: tuple[Any, ...],
    confirmations: tuple[Any, ...],
    *,
    now: datetime,
) -> None:
    catalyst = import_module("packages.catalyst")
    summary_by_theme = {
        confirmation.canonical_theme_id: confirmation.summary
        for confirmation in confirmations
    }
    for event in events:
        if (
            event.canonical_theme_id not in summary_by_theme
            or event.reconciliation_status.value != "MATCHED"
        ):
            continue
        loaded = repository.load(event.event_id)
        evidence = () if loaded is None else loaded[1]
        context = catalyst.ThemeContext(
            event_id=event.event_id,
            theme_id=event.canonical_theme_id,
            display_name=event.classification.display_name,
            market_date=event.market_date,
            activated_at=event.first_detected_at,
        )
        decision = catalyst.decide(
            context,
            evidence,
            now=now,
            previous=repository.current(event.event_id),
            after_close_summary=summary_by_theme[event.canonical_theme_id],
        )
        repository.record(event.event_id, decision, now=now)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    market_date = date.fromisoformat(args.market_date)
    database_url = os.environ.get(str(args.database_url_env), "").strip()
    if not database_url:
        raise RuntimeError(f"{args.database_url_env} is required")
    psycopg = import_module("psycopg")
    event_module = import_module("packages.events")
    reconciliation = import_module("packages.events.reconciliation")
    catalyst = import_module("packages.catalyst")
    operator = import_module("packages.operator")
    connection = psycopg.connect(database_url)
    now = datetime.now(UTC)
    run_id = f"after-close-{market_date.isoformat()}"
    repository = operator.PostgresOperatorRepository(cast(Any, connection))
    try:
        repository.set_job_status(
            run_id=run_id,
            job_type="AFTER_CLOSE_RECONCILIATION",
            status=operator.JobStatus.RUNNING,
            now=now,
            internal_context={"marketDate": market_date.isoformat()},
        )
        confirmations = _confirmations(connection, market_date)
        run = reconciliation.reconcile_after_close(
            writer=event_module.EventWriter(
                event_module.PostgresEventStore(cast(Any, connection))
            ),
            events=_events(connection, market_date),
            confirmations=confirmations,
            market_date=market_date,
            now=now,
        )
        reconciled_events = _events(connection, market_date)
        _open_reviews(
            repository,
            run,
            reconciled_events,
            confirmations,
            now=now,
        )
        _record_after_close_evidence(
            catalyst.PostgresEvidenceRepository(cast(Any, connection)),
            reconciled_events,
            confirmations,
            now=now,
        )
        repository.set_job_status(
            run_id=run_id,
            job_type="AFTER_CLOSE_RECONCILIATION",
            status=operator.JobStatus.SUCCEEDED,
            now=datetime.now(UTC),
            internal_context={
                "marketDate": market_date.isoformat(),
                "confirmations": len(confirmations),
                "matched": len(run.matched),
                "unmatched": len(run.unmatched),
                "skipped": len(run.skipped_event_ids),
                "missedIntraday": len(run.unmatched_confirmations),
            },
        )
        print(
            json.dumps(
                {
                    "status": "SUCCEEDED",
                    "marketDate": market_date.isoformat(),
                    "confirmations": len(confirmations),
                    "matched": len(run.matched),
                    "unmatched": len(run.unmatched),
                    "skipped": len(run.skipped_event_ids),
                    "missedIntraday": len(run.unmatched_confirmations),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        repository.set_job_status(
            run_id=run_id,
            job_type="AFTER_CLOSE_RECONCILIATION",
            status=operator.JobStatus.FAILED,
            now=datetime.now(UTC),
            error_code=type(exc).__name__,
            internal_context={"message": str(exc)[:500]},
        )
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"after-close reconciliation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
