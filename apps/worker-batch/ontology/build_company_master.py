#!/usr/bin/env python3
"""회사 master를 만들어 `core` 회사 테이블에 적재한다 (회사 온톨로지 단계 2).

로컬 수집본(data/infostock/import)의 종목 참조에서 회사·alias·회사-종목 관계를
만든다. 외부 API를 호출하지 않는다. DART 고유번호는 이미 수집해 둔 OpenDART
대조표 파일을 줄 때만 붙이고, 없으면 비워 둔다.

같은 수집본으로 다시 실행하면 아무 행도 늘지 않는다. `--report-only`는 DB를
건드리지 않고 집계만 낸다.
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
    COMPANY_MASTER_VERSION,
    PostgresCompanyMasterStore,
    build_company_master,
)
from packages.ontology.krx_names import KrxNameIndex, load_name_index


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="회사 master를 만들어 core 회사 테이블에 적재합니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "infostock" / "import",
    )
    parser.add_argument(
        "--corp-code-snapshot",
        type=Path,
        default=None,
        help="수집해 둔 OpenDART 고유번호 대조표 JSON 봉투 경로.",
    )
    parser.add_argument(
        "--krx-names",
        type=Path,
        default=None,
        help="build_krx_name_windows.py가 만든 종목명 이력 색인 경로.",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _corp_codes(path: Path) -> dict[str, str]:
    """`packages/reference-data`는 하이픈 디렉터리라 importlib로 부른다."""

    parsers = importlib.import_module("packages.reference-data.reference_data.parsers")
    snapshot = parsers.load_collected_snapshot(
        json.loads(path.read_text(encoding="utf-8"))
    )
    return cast(dict[str, str], parsers.parse_corp_code_index(snapshot))


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    database_url = os.environ.get(str(arguments.database_url_env), "").strip()
    if not database_url and not arguments.report_only:
        _print(
            {
                "status": "FAILED",
                "messageKo": (
                    f"{arguments.database_url_env} 환경변수에 PostgreSQL URL이 "
                    "필요합니다. DB 없이 집계만 보려면 --report-only를 쓰십시오."
                ),
            }
        )
        return 2
    try:
        bundle = load_existing_collection(arguments.input_dir)
    except (OSError, FixtureValidationError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2

    corp_codes: dict[str, str] = {}
    krx_names: KrxNameIndex | None = None
    try:
        if arguments.corp_code_snapshot is not None:
            corp_codes = _corp_codes(arguments.corp_code_snapshot)
        if arguments.krx_names is not None:
            krx_names = load_name_index(
                json.loads(arguments.krx_names.read_text(encoding="utf-8"))
            )
    except (OSError, ValueError, KeyError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2

    master = build_company_master(bundle, corp_codes=corp_codes, krx_names=krx_names)
    report: dict[str, Any] = {
        "status": "SUCCEEDED",
        "datasetHash": bundle.dataset_hash,
        "masterVersion": COMPANY_MASTER_VERSION,
        "companies": len(master.companies),
        "aliases": master.alias_count,
        "instruments": master.instrument_count,
        "revisions": master.revision_count,
        "multiInstrumentCompanies": sum(
            len(company.instruments) > 1 for company in master.companies
        ),
        "companiesWithDartCorpCode": sum(
            company.dart_corp_code is not None for company in master.companies
        ),
        "companiesWithKrxName": sum(
            company.name_basis == "KRX_LISTING" for company in master.companies
        ),
        "krxAliases": sum(
            alias.validity_basis == "KRX_LISTING"
            for company in master.companies
            for alias in company.aliases
        ),
        "nameChangeRevisions": sum(
            revision.change_type == "NAME_CHANGED"
            for company in master.companies
            for revision in company.revisions
        ),
        "unresolvedNames": len(master.unresolved),
        "unresolvedMentions": sum(
            review.mention_count for review in master.unresolved
        ),
        "unresolvedLeaderMentions": sum(
            review.mention_count
            for review in master.unresolved
            if review.source_kind == "HISTORY_LEADER"
        ),
        "loaded": False,
    }

    if not arguments.report_only:
        psycopg = importlib.import_module("psycopg")
        connection: Any = psycopg.connect(database_url)
        try:
            counts = PostgresCompanyMasterStore(cast(Any, connection)).load(
                master, recorded_at=datetime.now(UTC)
            )
        finally:
            connection.close()
        report.update(
            {
                "loaded": True,
                "loadedCompanies": counts.companies,
                "insertedCompanies": counts.companies_inserted,
                "insertedAliases": counts.aliases_inserted,
                "insertedInstruments": counts.instruments_inserted,
                "insertedRevisions": counts.revisions_inserted,
                "insertedReviews": counts.reviews_inserted,
                "unknownStockCodes": counts.unknown_stock_codes,
                "skippedCompanies": counts.skipped_companies,
            }
        )

    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
