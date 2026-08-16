#!/usr/bin/env python3
"""기준셋으로 소재 온톨로지 분류 정확도를 잰다 (E-17 정확도 게이트).

기준셋(tests/ontology/goldset_v1.tsv)은 사람이 원문만 보고 붙인 라벨이며
원문은 담지 않는다. 원문은 label_theme_history.py가 만든 로컬
labels.jsonl에서 key로 조인하고, 분류는 현재 저장소의 transform으로
다시 계산한다 — 어휘를 고친 뒤 이 스크립트 하나로 재채점된다.

지표: primary 일치(엄격 = gold 1순위와 일치 / 허용 = gold 대안 포함,
미분류는 gold OTHER와 일치), 유형 포함(gold 1순위가 복수 라벨 안에 존재),
방향 일치, 확실성 일치. 혼동 상위 목록도 남긴다.

**대표 수치는 `HUMAN_CONFIRMED` 행만으로 낸다.** `AI_DRAFT` 행은 라벨이
현재 transform의 자기 출력이라 채점하면 정의상 100%가 나온다. 그 수치는
`aiDraft` 블록에 따로 담고 승격 판정에 쓰지 않는다(계획서 11.1.2).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
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

# 이 미만이면 유형별 정확도를 보고하지 않는다(계획서 11.1절).
MIN_CONFIRMED_PER_TYPE = 30
CONFIRMED = "HUMAN_CONFIRMED"


@dataclass(frozen=True, slots=True)
class GoldRow:
    key: str
    primary: str
    alt: str
    direction: str
    certainty: str
    review_status: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="기준셋 대비 분류 정확도를 계산합니다."
    )
    parser.add_argument(
        "--goldset",
        type=Path,
        action="append",
        help="여러 번 줄 수 있다. 기본값은 goldset_v1.tsv와 goldset_supplement.tsv.",
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


def _read_goldset(path: Path, subset: str) -> list[GoldRow]:
    rows: list[GoldRow] = []
    index = 0
    for line in path.open(encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        keep = (
            subset == "all"
            or (subset == "dev" and index % 2 == 0)
            or (subset == "test" and index % 2 == 1)
        )
        index += 1
        if not keep:
            continue
        columns = line.split("\t")
        if len(columns) == 5:
            # 사람이 블라인드로 붙인 원본 기준셋. 출처 칸이 생기기 전 형식이다.
            columns.append(CONFIRMED)
        key, primary, alt, direction, certainty, review_status = columns
        rows.append(GoldRow(key, primary, alt, direction, certainty, review_status))
    return rows


@dataclass
class Tally:
    scored: int = 0
    primary_strict: int = 0
    primary_lenient: int = 0
    type_recall: int = 0
    direction_hits: int = 0
    certainty_hits: int = 0

    def ratios(self) -> dict[str, float | None]:
        if not self.scored:
            return {
                "primaryStrictAccuracy": None,
                "primaryLenientAccuracy": None,
                "typeRecall": None,
                "directionAccuracy": None,
                "certaintyAccuracy": None,
            }
        return {
            "primaryStrictAccuracy": round(self.primary_strict / self.scored, 4),
            "primaryLenientAccuracy": round(self.primary_lenient / self.scored, 4),
            "typeRecall": round(self.type_recall / self.scored, 4),
            "directionAccuracy": round(self.direction_hits / self.scored, 4),
            "certaintyAccuracy": round(self.certainty_hits / self.scored, 4),
        }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    goldsets = arguments.goldset or [
        REPOSITORY_ROOT / "tests" / "ontology" / "goldset_v1.tsv",
        REPOSITORY_ROOT / "tests" / "ontology" / "goldset_supplement.tsv",
    ]
    if not arguments.labels.is_file():
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": (
                        "labels.jsonl이 없습니다. label_theme_history.py를 "
                        "먼저 실행하세요."
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 2
    text_by_key: dict[str, str] = {}
    for line in arguments.labels.open(encoding="utf-8"):
        row = json.loads(line)
        text_by_key[f"{row['themeId']}/{row['sourceHistoryKey']}"] = row["rawText"]

    gold_rows: list[GoldRow] = []
    for path in goldsets:
        if path.is_file():
            gold_rows.extend(_read_goldset(path, arguments.subset))

    missing = 0
    confirmed = Tally()
    draft = Tally()
    by_type: dict[str, Tally] = defaultdict(Tally)
    confirmed_by_type: Counter[str] = Counter()
    draft_by_type: Counter[str] = Counter()
    primary_confusions: Counter[str] = Counter()
    certainty_confusions: Counter[str] = Counter()
    mismatches: list[dict[str, str]] = []

    for row in gold_rows:
        raw_text = text_by_key.get(row.key)
        if raw_text is None:
            missing += 1
            continue
        is_confirmed = row.review_status == CONFIRMED
        (confirmed_by_type if is_confirmed else draft_by_type)[row.primary] += 1
        result = classify_catalyst(raw_text)
        predicted = result.primary_type_id or "OTHER"
        accepted = {row.primary} | ({row.alt} if row.alt else set())
        targets = [confirmed if is_confirmed else draft]
        if is_confirmed:
            targets.append(by_type[row.primary])
        for tally in targets:
            tally.scored += 1
            if predicted == row.primary:
                tally.primary_strict += 1
            if predicted in accepted:
                tally.primary_lenient += 1
            if row.primary == "OTHER":
                if result.is_unclassified:
                    tally.type_recall += 1
            elif row.primary in result.type_ids:
                tally.type_recall += 1
            if result.direction == row.direction:
                tally.direction_hits += 1
            if result.certainty == row.certainty:
                tally.certainty_hits += 1
        if not is_confirmed:
            continue
        if predicted not in accepted:
            primary_confusions[f"{row.primary}→{predicted}"] += 1
            if len(mismatches) < 200:
                mismatches.append(
                    {
                        "key": row.key,
                        "gold": row.primary,
                        "predicted": predicted,
                        "rawText": raw_text,
                    }
                )
        if result.certainty != row.certainty:
            certainty_confusions[f"{row.certainty}→{result.certainty}"] += 1

    total = len(gold_rows)
    per_type = {}
    unmeasurable = []
    for type_id, tally in sorted(by_type.items()):
        if tally.scored < MIN_CONFIRMED_PER_TYPE:
            per_type[type_id] = {
                "confirmed": tally.scored,
                "status": "측정 불가",
            }
            unmeasurable.append(type_id)
        else:
            per_type[type_id] = {"confirmed": tally.scored, **tally.ratios()}
    for type_id in sorted(set(draft_by_type) - set(by_type)):
        per_type[type_id] = {"confirmed": 0, "status": "측정 불가"}
        unmeasurable.append(type_id)

    report = {
        "schemaVersion": "2.0.0",
        "goldsetPaths": [str(path) for path in goldsets],
        "goldsetSize": total,
        "missingKeys": missing,
        "vocabularyVersion": VOCABULARY_VERSION,
        "vocabularyContentHash": vocabulary_content_hash(),
        "transformVersion": TRANSFORM_VERSION,
        "humanConfirmed": confirmed.scored,
        "humanConfirmedRatio": (
            round(confirmed.scored / (confirmed.scored + draft.scored), 4)
            if confirmed.scored + draft.scored
            else 0.0
        ),
        **confirmed.ratios(),
        "aiDraft": {
            "scored": draft.scored,
            "messageKo": (
                "AI_DRAFT 라벨은 현재 transform의 자기 출력이라 채점하면 정의상 "
                "100%다. 승격 판정에 쓰지 않는다."
            ),
            **draft.ratios(),
        },
        "perTypeConfirmed": per_type,
        "unmeasurableTypes": sorted(set(unmeasurable)),
        "minConfirmedPerType": MIN_CONFIRMED_PER_TYPE,
        "topPrimaryConfusions": primary_confusions.most_common(15),
        "topCertaintyConfusions": certainty_confusions.most_common(6),
        "mismatches": mismatches,
    }
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        key: value
        for key, value in report.items()
        if key not in {"mismatches", "perTypeConfirmed"}
    }
    summary["unmeasurableTypeCount"] = len(report["unmeasurableTypes"])
    summary.pop("unmeasurableTypes", None)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
