#!/usr/bin/env python3
"""희소 소재 유형을 채우는 gold set 보강 표본을 뽑는다 (단계 0).

무작위 표집만으로는 희소 유형이 측정 불가로 남는다. test split 500건 기준으로
28종 중 23종이 30건에 못 미친다 — 콘텐츠 성과 2.1건, 인수합병 3.1건 수준이다.
이 job은 기존 goldset에 없는 기록에서 유형별로 부족분을 채워 뽑는다.

표집 기준은 현재 transform의 **예측 유형**이다. 참값을 모르기 때문이며, 그래서
이 표본으로 재는 것은 예측 클래스별 **precision**이지 recall이 아니다. 보고서에
그대로 표시한다.

라벨 칸은 비워 두지 않고 현재 분류를 `AI_DRAFT`로 채운다. 사람이 확인하면
`HUMAN_CONFIRMED`로 바꾼다. 승격 판정은 확인된 행만 쓴다(계획서 11.1.2).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.ontology import VOCABULARY

# dev·test 양쪽에 이만큼씩 채운다. 계획서 11.1절 "test split 최소 30건" 기준.
TARGET_PER_SPLIT = 30
SAMPLE_SEED = 20260817


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="희소 유형을 채우는 gold set 보강 표본을 뽑습니다."
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
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "tests" / "ontology" / "goldset_supplement.tsv",
    )
    parser.add_argument("--target", type=int, default=TARGET_PER_SPLIT)
    return parser


def _existing_counts(path: Path) -> tuple[set[str], Counter[str], Counter[str]]:
    """이미 쓰인 key와 split별 유형 분포. split은 주석을 뺀 행 순번의 짝·홀이다."""

    used: set[str] = set()
    dev: Counter[str] = Counter()
    test: Counter[str] = Counter()
    index = 0
    for line in path.open(encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        columns = line.split("\t")
        used.add(columns[0])
        (dev if index % 2 == 0 else test)[columns[1]] += 1
        index += 1
    return used, dev, test


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
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

    used, dev_counts, test_counts = _existing_counts(arguments.goldset)
    pool: dict[str, list[dict[str, object]]] = defaultdict(list)
    for line in arguments.labels.open(encoding="utf-8"):
        row = json.loads(line)
        key = f"{row['themeId']}/{row['sourceHistoryKey']}"
        if key in used:
            continue
        pool[str(row.get("primaryTypeId") or "OTHER")].append({**row, "key": key})

    type_ids = [definition.type_id for definition in VOCABULARY] + ["OTHER"]
    rng = random.Random(SAMPLE_SEED)
    picked: list[dict[str, object]] = []
    shortfall: dict[str, int] = {}
    for type_id in type_ids:
        need = max(0, arguments.target - dev_counts[type_id]) + max(
            0, arguments.target - test_counts[type_id]
        )
        if need == 0:
            continue
        candidates = sorted(pool.get(type_id, ()), key=lambda item: str(item["key"]))
        if len(candidates) < need:
            shortfall[type_id] = need - len(candidates)
        chosen = rng.sample(candidates, min(need, len(candidates)))
        picked.extend(sorted(chosen, key=lambda item: str(item["key"])))

    lines = [
        "# E-17 기준셋 보강 — 희소 유형을 dev·test 각 "
        f"{arguments.target}건까지 채우는 표본이다.",
        f"# seed {SAMPLE_SEED}. 기존 goldset_v1.tsv와 key가 겹치지 않는다.",
        "# 표집이 예측 유형 기준이라 이 표본으로 재는 것은 클래스별 precision이다.",
        "# 라벨은 현재 transform 결과이며 review_status가 AI_DRAFT인 동안은",
        "# 승격 판정에 쓰지 않는다(계획서 11.1.2).",
        "# 열: key, gold_primary, gold_alt, gold_direction, gold_certainty, review_status",
    ]
    for row in picked:
        lines.append(
            "\t".join(
                (
                    str(row["key"]),
                    str(row.get("primaryTypeId") or "OTHER"),
                    "",
                    str(row["direction"]),
                    str(row["certainty"]),
                    "AI_DRAFT",
                )
            )
        )
    arguments.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    distribution = Counter(str(row.get("primaryTypeId") or "OTHER") for row in picked)
    payload = {
        "status": "SUCCEEDED" if not shortfall else "PARTIAL",
        "outPath": str(arguments.out),
        "targetPerSplit": arguments.target,
        "sampled": len(picked),
        "typesTouched": len(distribution),
        "distribution": dict(sorted(distribution.items())),
        "reviewStatus": "AI_DRAFT",
        "metricMeaning": "예측 클래스별 precision (recall 아님)",
    }
    if shortfall:
        payload["shortfall"] = dict(sorted(shortfall.items()))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
