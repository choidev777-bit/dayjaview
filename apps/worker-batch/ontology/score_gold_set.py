#!/usr/bin/env python3
"""기준셋으로 소재 온톨로지 분류 정확도를 잰다 (E-17 정확도 게이트).

기준셋(tests/ontology/goldset_v1.tsv)은 사람이 원문만 보고 붙인 라벨이며
원문은 담지 않는다. 원문은 label_theme_history.py가 만든 로컬
labels.jsonl에서 key로 조인하고, 분류는 현재 저장소의 transform으로
다시 계산한다 — 어휘를 고친 뒤 이 스크립트 하나로 재채점된다.

지표: primary 일치(엄격 = gold 1순위와 일치 / 허용 = gold 대안 포함,
미분류는 gold OTHER와 일치), 유형 포함(gold 1순위가 복수 라벨 안에 존재),
방향 일치, 확실성 일치. 혼동 상위 목록도 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.ontology import (
    TRANSFORM_VERSION,
    VOCABULARY_VERSION,
    classify_catalyst,
    vocabulary_content_hash,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="기준셋 대비 분류 정확도를 계산합니다."
    )
    parser.add_argument(
        "--goldset",
        type=Path,
        default=REPOSITORY_ROOT / "tests" / "ontology" / "goldset_v1.tsv",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "labels.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "goldset_score.json",
    )
    parser.add_argument(
        "--subset",
        choices=("all", "dev", "test"),
        default="all",
        help="dev=짝수 행(개선용), test=홀수 행(측정 전용). 어휘 개선은 dev만 본다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.labels.is_file():
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": "labels.jsonl이 없습니다. label_theme_history.py를 먼저 실행하세요.",
                },
                ensure_ascii=False,
            )
        )
        return 2
    text_by_key: dict[str, str] = {}
    for line in arguments.labels.open(encoding="utf-8"):
        row = json.loads(line)
        text_by_key[f"{row['themeId']}/{row['sourceHistoryKey']}"] = row["rawText"]

    gold_rows: list[tuple[str, str, str, str, str]] = []
    row_index = 0
    for line in arguments.goldset.open(encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        keep = (
            arguments.subset == "all"
            or (arguments.subset == "dev" and row_index % 2 == 0)
            or (arguments.subset == "test" and row_index % 2 == 1)
        )
        row_index += 1
        if not keep:
            continue
        key, primary, alt, direction, certainty = line.split("\t")
        gold_rows.append((key, primary, alt, direction, certainty))

    total = len(gold_rows)
    missing = 0
    primary_strict = 0
    primary_lenient = 0
    type_recall = 0
    direction_hits = 0
    certainty_hits = 0
    primary_confusions: Counter[str] = Counter()
    certainty_confusions: Counter[str] = Counter()
    mismatches: list[dict[str, str]] = []
    for key, gold_primary, gold_alt, gold_direction, gold_certainty in gold_rows:
        raw_text = text_by_key.get(key)
        if raw_text is None:
            missing += 1
            continue
        result = classify_catalyst(raw_text)
        predicted_primary = result.primary_type_id or "OTHER"
        accepted = {gold_primary} | ({gold_alt} if gold_alt else set())
        if predicted_primary == gold_primary:
            primary_strict += 1
        if predicted_primary in accepted:
            primary_lenient += 1
        else:
            primary_confusions[f"{gold_primary}→{predicted_primary}"] += 1
            if len(mismatches) < 200:
                mismatches.append(
                    {
                        "key": key,
                        "gold": gold_primary,
                        "predicted": predicted_primary,
                        "rawText": raw_text,
                    }
                )
        if gold_primary == "OTHER":
            if result.is_unclassified:
                type_recall += 1
        elif gold_primary in result.type_ids:
            type_recall += 1
        if result.direction == gold_direction:
            direction_hits += 1
        if result.certainty == gold_certainty:
            certainty_hits += 1
        else:
            certainty_confusions[f"{gold_certainty}→{result.certainty}"] += 1

    scored = total - missing
    report = {
        "schemaVersion": "1.0.0",
        "goldsetPath": str(arguments.goldset),
        "goldsetSize": total,
        "scored": scored,
        "missingKeys": missing,
        "vocabularyVersion": VOCABULARY_VERSION,
        "vocabularyContentHash": vocabulary_content_hash(),
        "transformVersion": TRANSFORM_VERSION,
        "primaryStrictAccuracy": round(primary_strict / scored, 4),
        "primaryLenientAccuracy": round(primary_lenient / scored, 4),
        "typeRecall": round(type_recall / scored, 4),
        "directionAccuracy": round(direction_hits / scored, 4),
        "certaintyAccuracy": round(certainty_hits / scored, 4),
        "topPrimaryConfusions": primary_confusions.most_common(15),
        "topCertaintyConfusions": certainty_confusions.most_common(6),
        "mismatches": mismatches,
    }
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {k: v for k, v in report.items() if k != "mismatches"}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
