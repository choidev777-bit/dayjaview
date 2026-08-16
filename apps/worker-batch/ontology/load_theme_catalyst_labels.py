#!/usr/bin/env python3
"""테마 history 라벨을 PostgreSQL `ontology` 스키마에 적재한다 (E-17).

로컬 수집본(data/infostock/import)을 읽어 같은 통제어휘로 분류한 뒤 DB에
넣는다. 외부 API를 호출하지 않는다. 같은 수집본으로 다시 실행하면 이미
적재된 (history_id, 어휘 버전, 변환 버전) 조합은 건너뛴다.

DB에 현재 revision이 없거나 원문이 다른 기록은 넣지 않고 건수만 보고한다.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.infostock.errors import FixtureValidationError
from packages.infostock.existing_collection import load_existing_collection
from packages.ontology import (
    TRANSFORM_VERSION,
    VOCABULARY_VERSION,
    PostgresCatalystLabelStore,
    VocabularyConflictError,
    records_from_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="테마 history 라벨을 ontology 스키마에 적재합니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "infostock" / "import",
    )
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    database_url = os.environ.get(str(arguments.database_url_env), "").strip()
    if not database_url:
        _print(
            {
                "status": "FAILED",
                "messageKo": (
                    f"{arguments.database_url_env} 환경변수에 PostgreSQL URL이 "
                    "필요합니다."
                ),
            }
        )
        return 2
    try:
        bundle = load_existing_collection(arguments.input_dir)
    except (OSError, FixtureValidationError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2

    psycopg = importlib.import_module("psycopg")
    connection: Any = psycopg.connect(database_url)
    now = datetime.now(UTC)
    try:
        store = PostgresCatalystLabelStore(cast(Any, connection))
        try:
            vocabulary_registered = store.sync_vocabulary(registered_at=now)
        except VocabularyConflictError as exc:
            _print({"status": "FAILED", "messageKo": str(exc)})
            return 2
        counts = store.load(records_from_bundle(bundle), labeled_at=now)
    finally:
        connection.close()

    _print(
        {
            "status": "SUCCEEDED",
            "datasetHash": bundle.dataset_hash,
            "vocabularyVersion": VOCABULARY_VERSION,
            "vocabularyRegistered": vocabulary_registered,
            "transformVersion": TRANSFORM_VERSION,
            "totalRecords": counts.total,
            "inserted": counts.inserted,
            "alreadyPresent": counts.existing,
            "unresolvedHistory": counts.unresolved,
            "rawTextMismatched": counts.mismatched,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
