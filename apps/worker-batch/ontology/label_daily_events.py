#!/usr/bin/env python3
"""DailyFeaturedTheme relation을 source mention으로 만든다 (E-22 단계 1).

외부 API를 호출하지 않고 로컬 Daily 수집본을 다시 읽는다. 기본 실행은
gitignore된 ``research/ontology``에 mention JSONL과 coverage 보고서를 쓴다.
``--load``를 주면 현재 PostgreSQL relation과 hash를 대조한 뒤 적재한다.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.infostock.daily_api import load_daily_api_backfill
from packages.infostock.errors import FixtureValidationError
from packages.ontology import (
    DailyMentionTransformConflictError,
    PostgresDailyMentionStore,
    label_daily_mentions,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DailyFeaturedTheme 원천을 사건 mention으로 변환합니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=(
            REPOSITORY_ROOT / "data" / "infostock" / "daily-full-20260814"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology",
    )
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--database-url-env", default="INFOSTOCK_DATABASE_URL")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    database_url = os.environ.get(str(arguments.database_url_env), "").strip()
    if arguments.load and not database_url:
        _print(
            {
                "status": "FAILED",
                "messageKo": (
                    f"--load에는 {arguments.database_url_env} 환경변수의 "
                    "PostgreSQL URL이 필요합니다."
                ),
            }
        )
        return 2

    try:
        backfill, _ = load_daily_api_backfill(arguments.input_dir)
    except (OSError, ValueError, KeyError, FixtureValidationError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2

    mentions, report = label_daily_mentions(backfill)
    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    mentions_path = output_dir / "daily_mentions.jsonl"
    report_path = output_dir / "daily_coverage_report.json"
    with mentions_path.open("w", encoding="utf-8", newline="\n") as stream:
        for mention in mentions:
            stream.write(
                json.dumps(
                    mention.as_dict(include_raw_text=False),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

    load_report: dict[str, object] = {"loaded": False}
    if arguments.load:
        psycopg = importlib.import_module("psycopg")
        connection: Any = psycopg.connect(database_url)
        try:
            counts = PostgresDailyMentionStore(cast(Any, connection)).load(mentions)
        except DailyMentionTransformConflictError as exc:
            _print({"status": "FAILED", "messageKo": str(exc)})
            return 2
        finally:
            connection.close()
        load_report = {
            "loaded": True,
            "insertedMentions": counts.inserted,
            "alreadyPresent": counts.existing,
            "missingRelations": counts.missing_relations,
            "mismatchedRelations": counts.mismatched_relations,
        }

    report.update(load_report)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _print(
        {
            "status": "SUCCEEDED",
            "datasetHash": report["datasetHash"],
            "totalPosts": report["totalPosts"],
            "totalMentions": report["totalMentions"],
            "bodyStatusCounts": report["bodyStatusCounts"],
            "formatFamilyCounts": report["formatFamilyCounts"],
            "mentionsPath": str(mentions_path),
            "reportPath": str(report_path),
            **load_report,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
