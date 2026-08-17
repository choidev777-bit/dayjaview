#!/usr/bin/env python3
"""테마 history의 회사 mention·역할을 라벨링한다 (회사 온톨로지 단계 3).

외부 호출 없이 로컬 인포스탁 수집본을 읽는다. 기본 동작은 gitignore된
``research/ontology``에 전수 JSONL과 coverage 보고서를 쓰는 것이다. ``--load``를
주면 같은 revision을 PostgreSQL에 append하며, 재실행해도 행이 늘지 않는다.
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
    CompanyRoleCompanyMissingError,
    CompanyRoleTransformConflictError,
    PostgresCompanyRoleStore,
    build_company_master,
    label_company_history,
    summarize_company_role_labels,
)
from packages.ontology.krx_names import KrxNameIndex, load_name_index


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="테마 history에서 회사 mention과 역할을 추출합니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "infostock" / "import",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology",
    )
    parser.add_argument(
        "--krx-names",
        type=Path,
        default=None,
        help="build_krx_name_windows.py가 만든 종목명 이력 색인 경로.",
    )
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--database-url-env", default="DATABASE_URL")
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
                    f"--load에는 {arguments.database_url_env} 환경변수의 PostgreSQL "
                    "URL이 필요합니다."
                ),
            }
        )
        return 2
    try:
        bundle = load_existing_collection(arguments.input_dir)
        krx_names: KrxNameIndex | None = (
            None
            if arguments.krx_names is None
            else load_name_index(
                json.loads(arguments.krx_names.read_text(encoding="utf-8"))
            )
        )
    except (OSError, ValueError, KeyError, FixtureValidationError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2

    master = build_company_master(bundle, krx_names=krx_names)
    resolvable_seed_codes: frozenset[str] | None = None
    if arguments.load:
        psycopg = importlib.import_module("psycopg")
        company_connection: Any = psycopg.connect(database_url)
        try:
            resolvable_seed_codes = frozenset(
                PostgresCompanyRoleStore(cast(Any, company_connection)).company_ids()
            )
        finally:
            company_connection.close()
    labels, report = label_company_history(
        bundle,
        master,
        resolvable_seed_codes=resolvable_seed_codes,
    )
    if arguments.load:
        psycopg = importlib.import_module("psycopg")
        alignment_connection: Any = psycopg.connect(database_url)
        try:
            alignment = PostgresCompanyRoleStore(
                cast(Any, alignment_connection)
            ).align_labels_to_current_sources(labels)
        finally:
            alignment_connection.close()
        labels = alignment.labels
        report["labelInputDatasetHash"] = report["datasetHash"]
        if alignment.database_dataset_hash is not None:
            report["datasetHash"] = alignment.database_dataset_hash
        report["databaseAlignedHistories"] = alignment.histories_aligned
        report["databaseAlignedMentions"] = alignment.mentions_aligned
        report.update(summarize_company_role_labels(labels))
    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "company_labels.jsonl"
    report_path = output_dir / "company_role_report.json"
    with labels_path.open("w", encoding="utf-8", newline="\n") as stream:
        for label in labels:
            stream.write(
                json.dumps(label.as_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            )

    load_report: dict[str, object] = {"loaded": False}
    if arguments.load:
        psycopg = importlib.import_module("psycopg")
        load_connection: Any = psycopg.connect(database_url)
        try:
            counts = PostgresCompanyRoleStore(cast(Any, load_connection)).load_bulk(
                labels, labeled_at=datetime.now(UTC)
            )
        except (
            CompanyRoleCompanyMissingError,
            CompanyRoleTransformConflictError,
        ) as exc:
            _print({"status": "FAILED", "messageKo": str(exc)})
            return 2
        finally:
            load_connection.close()
        load_report = {
            "loaded": True,
            "insertedRevisions": counts.inserted,
            "alreadyPresent": counts.existing,
            "unresolvedHistory": counts.unresolved_history,
            "mismatchedHistory": counts.mismatched_history,
            "missingReferences": counts.missing_references,
            "insertedMentions": counts.mentions_inserted,
            "insertedRoles": counts.roles_inserted,
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
            "companyMasterVersion": report["companyMasterVersion"],
            "roleTransformVersion": report["roleTransformVersion"],
            "totalHistories": report["totalHistories"],
            "historiesWithCompanyMention": report["historiesWithCompanyMention"],
            "directEventHistories": report["directEventHistories"],
            "labelsPath": str(labels_path),
            "reportPath": str(report_path),
            **load_report,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
