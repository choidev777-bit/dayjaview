#!/usr/bin/env python3
"""리서치 답변이 읽는 artifact를 발행한다 (단계 5, 계획서 7.6절).

여기서 고정하는 것은 데이터가 아니라 **버전 묶음**이다. dataset hash, 회사
master, 어휘, 변환, 중복 정책, 질의 계약이 한 묶음으로 hash된다. 같은 묶음이면
같은 답이 나와야 하고, 묶음이 달라지면 answer 블록의 versions가 달라진다.

`latest` 같은 바뀌는 경로를 만들지 않는다. 파일명에 artifact hash가 들어간다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.infostock.hashing import sha256_json  # noqa: E402
from packages.ontology import (  # noqa: E402
    ANSWER_CONTRACT_VERSION,
    COMPANY_MASTER_VERSION,
    COMPANY_ROLE_TRANSFORM_VERSION,
    DAILY_MENTION_TRANSFORM_VERSION,
    DEDUP_POLICY_VERSION,
    EVENT_STRUCTURE_TRANSFORM_VERSION,
    QUERY_CONTRACT_VERSION,
    QUERY_PLANNER_VERSION,
    TRANSFORM_VERSION,
    VOCABULARY_VERSION,
    PostgresResearchRepository,
    QueryType,
    query_contract_content_hash,
    query_contract_document,
    vocabulary_content_hash,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="자연어 리서치 artifact를 발행합니다."
    )
    parser.add_argument("--dsn", default="", help="비우면 DB를 읽지 않고 코드 버전만 고정합니다.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology",
    )
    parser.add_argument(
        "--verified",
        default="",
        help="사람 검수를 통과해 공개할 질의 유형(쉼표 구분). 비면 전부 잠긴다.",
    )
    return parser


def _verified(raw: str) -> list[str]:
    values = sorted(
        {QueryType(item.strip()).value for item in raw.split(",") if item.strip()}
    )
    return values


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        verified = _verified(arguments.verified)
    except ValueError as error:
        print(
            json.dumps(
                {"status": "FAILED", "messageKo": f"알 수 없는 질의 유형입니다: {error}"},
                ensure_ascii=False,
            )
        )
        return 2

    versions: dict[str, Any] = {
        "answerContract": ANSWER_CONTRACT_VERSION,
        "classificationTransform": TRANSFORM_VERSION,
        "companyMaster": COMPANY_MASTER_VERSION,
        "companyRoleTransform": COMPANY_ROLE_TRANSFORM_VERSION,
        "dailyMentionTransform": DAILY_MENTION_TRANSFORM_VERSION,
        "dedupPolicy": DEDUP_POLICY_VERSION,
        "eventStructureTransform": EVENT_STRUCTURE_TRANSFORM_VERSION,
        "ontologyVocabulary": VOCABULARY_VERSION,
        "queryContract": QUERY_CONTRACT_VERSION,
        "queryPlanner": QUERY_PLANNER_VERSION,
    }
    content_hashes = {
        "queryContract": query_contract_content_hash(),
        "vocabulary": vocabulary_content_hash(),
    }
    stored: dict[str, Any] = {}
    prerequisites: list[str] = []
    if arguments.dsn:
        import psycopg

        with psycopg.connect(arguments.dsn) as connection:
            repository = PostgresResearchRepository(connection)
            stored = dict(repository.versions())
            prerequisites = sorted(
                item.value for item in repository.ready_prerequisites()
            )

    payload = {
        "schemaVersion": "1.0.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "codeVersions": versions,
        "contentHashes": content_hashes,
        "storedVersions": dict(sorted(stored.items())),
        "readyPrerequisites": prerequisites,
        "queryContract": query_contract_document(),
        # 검수된 행이 없으면 어떤 유형도 열리지 않는다(계획서 11.1.2).
        "humanVerifiedQueryTypes": verified,
        "promotionEligible": bool(verified),
    }
    artifact_hash = sha256_json(payload)
    payload["artifactHash"] = artifact_hash

    arguments.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = arguments.out_dir / f"company_ontology_artifact.{artifact_hash[:16]}.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "outPath": str(out_path),
                "artifactHash": artifact_hash,
                "humanVerifiedQueryTypes": verified,
                "promotionEligible": bool(verified),
                "readyPrerequisites": prerequisites,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
