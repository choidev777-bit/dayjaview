#!/usr/bin/env python3
"""history 회사 역할을 고유 현실 사건·프로젝트로 구조화한다 (E-22 단계 4).

외부 API를 호출하지 않는다. 로컬 인포스탁 수집본을 다시 읽어 gitignore된
``research/ontology``에 사건·프로젝트·관계와 coverage 보고서를 쓴다. ``--load``를
주면 현재 DB 원천과 hash를 대조한 뒤 revision append 방식으로 적재한다.
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
    CatalystCompanyMissingError,
    CatalystIdentityConflictError,
    CatalystSourceConflictError,
    CatalystTransformConflictError,
    PostgresCatalystEventStore,
    PostgresCompanyRoleStore,
    build_company_master,
    build_history_catalyst_drafts,
    deduplicate_catalysts,
    label_company_history,
)
from packages.ontology.krx_names import KrxNameIndex, load_name_index


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="테마 history를 고유 현실 사건과 프로젝트로 구조화합니다."
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
    parser.add_argument("--krx-names", type=Path, default=None)
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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
    resolvable_codes: frozenset[str] | None = None
    if arguments.load:
        psycopg = importlib.import_module("psycopg")
        connection: Any = psycopg.connect(database_url)
        try:
            resolvable_codes = frozenset(
                PostgresCompanyRoleStore(cast(Any, connection)).company_ids()
            )
        finally:
            connection.close()
    labels, company_report = label_company_history(
        bundle,
        master,
        resolvable_seed_codes=resolvable_codes,
    )
    if arguments.load:
        psycopg = importlib.import_module("psycopg")
        connection = psycopg.connect(database_url)
        try:
            labels = PostgresCompanyRoleStore(
                cast(Any, connection)
            ).align_labels_to_current_sources(labels).labels
        finally:
            connection.close()

    drafts, structure_report = build_history_catalyst_drafts(
        labels,
        dataset_hash=bundle.dataset_hash,
    )
    result = deduplicate_catalysts(drafts)
    report = {
        **result.report(),
        "sourceHistoryCount": structure_report["sourceHistoryCount"],
        "catalystDraftCount": structure_report["catalystDraftCount"],
        "directCompanyRoleCount": structure_report["directCompanyRoleCount"],
        "projectLinkedDraftCount": structure_report["projectLinkedDraftCount"],
        "valueFactCount": structure_report["valueFactCount"],
        "companyMasterVersion": company_report["companyMasterVersion"],
        "companyRoleTransformVersion": company_report["roleTransformVersion"],
        "eventStructureTransformVersion": structure_report["transformVersion"],
        "loaded": False,
    }

    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    catalysts_path = output_dir / "company_catalysts.jsonl"
    projects_path = output_dir / "company_projects.jsonl"
    relations_path = output_dir / "company_catalyst_relations.jsonl"
    report_path = output_dir / "company_event_report.json"
    _write_jsonl(
        catalysts_path,
        [item.as_dict(include_raw_text=False) for item in result.catalysts],
    )
    _write_jsonl(projects_path, [item.as_dict() for item in result.projects])
    _write_jsonl(relations_path, [item.as_dict() for item in result.relations])

    if arguments.load:
        psycopg = importlib.import_module("psycopg")
        connection = psycopg.connect(database_url)
        try:
            _print(
                {
                    "status": "LOADING_BULK",
                    "uniqueCatalystCount": len(result.catalysts),
                    "messageKo": "COPY staging과 집합 INSERT로 원자 적재합니다.",
                }
            )
            counts = PostgresCatalystEventStore(cast(Any, connection)).load_bulk(
                result,
                generated_at=datetime.now(UTC),
            )
        except (
            CatalystCompanyMissingError,
            CatalystIdentityConflictError,
            CatalystSourceConflictError,
            CatalystTransformConflictError,
        ) as exc:
            _print({"status": "FAILED", "messageKo": str(exc)})
            return 2
        finally:
            connection.close()
        report.update(
            {
                "loaded": True,
                "insertedRevisions": counts.inserted_revisions,
                "alreadyPresent": counts.existing_revisions,
                "skippedCatalysts": counts.skipped_catalysts,
                "missingHistories": counts.missing_histories,
                "mismatchedHistories": counts.mismatched_histories,
                "insertedSourceMentions": counts.source_mentions_inserted,
                "insertedProjects": counts.projects_inserted,
                "insertedActors": counts.actors_inserted,
                "insertedCompanyRoles": counts.company_roles_inserted,
                "insertedParticipants": counts.participants_inserted,
                "insertedValues": counts.values_inserted,
                "insertedReactions": counts.reactions_inserted,
                "insertedRelations": counts.relations_inserted,
            }
        )

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _print(
        {
            "status": "SUCCEEDED",
            "datasetHash": bundle.dataset_hash,
            "catalystDraftCount": report["catalystDraftCount"],
            "uniqueCatalystCount": report["uniqueCatalystCount"],
            "projectCount": report["projectCount"],
            "possibleDuplicateCount": report["possibleDuplicateCount"],
            "artifactHash": report["artifactHash"],
            "catalystsPath": str(catalysts_path),
            "projectsPath": str(projects_path),
            "relationsPath": str(relations_path),
            "reportPath": str(report_path),
            "loaded": report["loaded"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
