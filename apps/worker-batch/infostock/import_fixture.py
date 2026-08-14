#!/usr/bin/env python3
"""Audit or import a committed fixture / explicitly supplied existing collection."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

if TYPE_CHECKING:
    from infostock import ImportBundle
else:
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))


class _ClosableConnection(Protocol):
    def close(self) -> None: ...


class _PsycopgModule(Protocol):
    def connect(self, connection_string: str) -> _ClosableConnection: ...


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Infostock 기존 수집본 또는 tracked fixture를 감사·적재합니다."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--fixture",
        type=Path,
        help="tests/infostock/fixtures 아래의 tracked JSON",
    )
    source.add_argument(
        "--collection-dir",
        type=Path,
        help="명시적으로 지정한 기존 collector output 디렉터리(기본값 없음)",
    )
    parser.add_argument(
        "--daily-backfill-dir",
        type=Path,
        help="collect_daily.py가 만든 Daily 전체 backfill 디렉터리",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="DB를 변경하지 않고 machine-readable 품질 보고만 출력합니다.",
    )
    parser.add_argument(
        "--quality-json",
        type=Path,
        help="선택한 경로에 machine-readable 품질 보고를 기록합니다.",
    )
    parser.add_argument(
        "--quality-markdown",
        type=Path,
        help="선택한 경로에 한국어 품질 보고를 기록합니다.",
    )
    parser.add_argument(
        "--database-url-env",
        default="INFOSTOCK_DATABASE_URL",
        help="PostgreSQL URL을 가진 환경변수 이름(값 자체는 출력하지 않음)",
    )
    return parser.parse_args(argv)


def _load_psycopg() -> _PsycopgModule:
    try:
        module = importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        raise RuntimeError("DB import에는 psycopg 3 runtime이 필요합니다.") from exc
    return cast(_PsycopgModule, module)


def _load_bundle(args: argparse.Namespace) -> ImportBundle:
    infostock = importlib.import_module("packages.infostock")
    if args.fixture is not None:
        if args.daily_backfill_dir is not None:
            raise ValueError("--daily-backfill-dir은 --collection-dir과 함께 사용하세요.")
        policy = infostock.CommittedFixturePolicy(REPOSITORY_ROOT)
        return cast("ImportBundle", infostock.load_committed_fixture(args.fixture, policy))
    return cast(
        "ImportBundle",
        infostock.load_existing_collection(
            args.collection_dir,
            infostock.ExistingCollectionPolicy(),
            daily_backfill_directory=args.daily_backfill_dir,
        ),
    )


def _write_report(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    infostock = importlib.import_module("packages.infostock")
    bundle = _load_bundle(args)
    machine = infostock.machine_quality_report(bundle)
    human = infostock.human_quality_report(bundle)
    machine_text = json.dumps(machine, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_report(args.quality_json, machine_text)
    _write_report(args.quality_markdown, human)
    if args.audit_only:
        print(json.dumps(machine, ensure_ascii=False, sort_keys=True))
        return 0
    database_url = os.environ.get(str(args.database_url_env))
    if not database_url:
        raise RuntimeError(
            f"{args.database_url_env} 환경변수에 PostgreSQL URL이 필요합니다."
        )
    connection = _load_psycopg().connect(database_url)
    try:
        store = infostock.PostgresInfostockStore(cast(Any, connection))
        result = infostock.import_bundle(bundle, store)
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "runId": result.run_id,
                "status": result.status,
                "coreStatus": result.core_status,
                "dailyStatus": result.daily_status,
                "blockers": list(result.blockers),
                "themesImported": result.themes_imported,
                "historyRowsSeen": result.history_rows_seen,
                "relatedStocksSeen": result.related_stocks_seen,
                "leadersSeen": result.leaders_seen,
                "historicalMembershipsSeen": result.historical_memberships_seen,
                "dailyListEntriesSeen": result.daily_list_entries_seen,
                "dailyPostsSeen": result.daily_posts_seen,
                "dailyBodiesSeen": result.daily_bodies_seen,
                "dailyRelationsSeen": result.daily_relations_seen,
                "snapshotsLinked": result.snapshots_linked,
                "reused": result.reused,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Infostock import 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
