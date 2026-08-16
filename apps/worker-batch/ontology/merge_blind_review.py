#!/usr/bin/env python3
"""독립 판정을 기존 라벨과 대조한다 (단계 0, 겹 C).

두 방법이 일치한 행은 `AI_CROSS_CHECKED`로 올리고 어긋난 행만 사람 검수 큐로
보낸다. 사람이 볼 양을 줄이는 것이 목적이며, 승격 게이트는 여전히
`HUMAN_CONFIRMED`만 센다(계획서 11.1.2).

두 방법이 독립이라는 전제가 이 대조의 값어치다 — 기존 라벨은 키워드 매칭이고
판정자는 원문만 봤다. 그래도 오차가 상관될 수 있어 일치가 정답을 뜻하지는
않는다. 일치는 "사람이 나중에 봐도 된다"는 뜻이지 "맞다"는 뜻이 아니다.
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

CROSS_CHECKED = "AI_CROSS_CHECKED"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="독립 판정과 기존 라벨을 대조해 검수 큐를 만듭니다."
    )
    parser.add_argument(
        "--supplement",
        type=Path,
        default=REPOSITORY_ROOT / "tests" / "ontology" / "goldset_supplement.tsv",
    )
    parser.add_argument(
        "--review",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "codex_review_output.tsv",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "labels.jsonl",
    )
    parser.add_argument(
        "--queue",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "label_review_queue.tsv",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "blind_review_score.json",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="일치한 행의 review_status를 AI_CROSS_CHECKED로 올려 저장한다.",
    )
    return parser


def _read_tsv(path: Path) -> list[list[str]]:
    return [
        line.rstrip("\n").split("\t")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    for path in (arguments.supplement, arguments.review):
        if not path.is_file():
            print(
                json.dumps(
                    {"status": "FAILED", "messageKo": f"{path} 파일이 없습니다."},
                    ensure_ascii=False,
                )
            )
            return 2

    text_by_key: dict[str, str] = {}
    if arguments.labels.is_file():
        for line in arguments.labels.open(encoding="utf-8"):
            row = json.loads(line)
            text_by_key[f"{row['themeId']}/{row['sourceHistoryKey']}"] = row["rawText"]

    review = {row[0]: row for row in _read_tsv(arguments.review)}
    base = _read_tsv(arguments.supplement)

    agree_primary = agree_lenient = agree_direction = agree_certainty = 0
    agree_all = 0
    scored = 0
    missing: list[str] = []
    confusions: Counter[str] = Counter()
    by_confidence: Counter[str] = Counter()
    agree_by_confidence: Counter[str] = Counter()
    queue: list[list[str]] = []
    statuses: dict[str, str] = {}

    for row in base:
        key, primary, alt, direction, certainty = row[0], row[1], row[2], row[3], row[4]
        judged = review.get(key)
        if judged is None:
            missing.append(key)
            continue
        scored += 1
        _, j_primary, j_alt, j_direction, j_certainty, confidence, note = (
            judged + [""] * (7 - len(judged))
        )[:7]
        by_confidence[confidence] += 1
        primary_same = j_primary == primary
        # 한쪽의 대안이 다른 쪽 1순위와 같으면 허용 일치로 본다.
        lenient_same = bool(
            primary_same or j_alt == primary or (alt and alt == j_primary)
        )
        direction_same = j_direction == direction
        certainty_same = j_certainty == certainty
        agree_primary += primary_same
        agree_lenient += lenient_same
        agree_direction += direction_same
        agree_certainty += certainty_same
        everything = primary_same and direction_same and certainty_same
        agree_all += everything
        if everything:
            agree_by_confidence[confidence] += 1
            statuses[key] = CROSS_CHECKED
            continue
        reasons = []
        if not primary_same:
            reasons.append(f"유형 {primary}↔{j_primary}")
            confusions[f"{primary}↔{j_primary}"] += 1
        if not direction_same:
            reasons.append(f"방향 {direction}↔{j_direction}")
        if not certainty_same:
            reasons.append(f"확실성 {certainty}↔{j_certainty}")
        queue.append(
            [
                key,
                " · ".join(reasons),
                primary,
                j_primary,
                j_alt,
                direction,
                j_direction,
                certainty,
                j_certainty,
                confidence,
                note.replace("\t", " "),
                text_by_key.get(key, "").replace("\t", " "),
            ]
        )

    arguments.queue.parent.mkdir(parents=True, exist_ok=True)
    arguments.queue.write_text(
        "\n".join(
            [
                "# 검수 큐 — 두 독립 판정이 어긋난 행만이다. 사람이 원문을 보고 하나를 고른다.",
                "# 열: key, 어긋난 축, rule_primary, judged_primary, judged_alt,"
                " rule_direction, judged_direction, rule_certainty, judged_certainty,"
                " confidence, note, raw_text",
                *("\t".join(row) for row in queue),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if arguments.apply and statuses:
        lines: list[str] = []
        for line in arguments.supplement.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                lines.append(line)
                continue
            columns = line.split("\t")
            if columns[0] in statuses and columns[5] == "AI_DRAFT":
                columns[5] = statuses[columns[0]]
            lines.append("\t".join(columns))
        arguments.supplement.write_text(
            "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
        )

    report = {
        "schemaVersion": "1.0.0",
        "scored": scored,
        "missingKeys": len(missing),
        "agreement": {
            "primaryStrict": round(agree_primary / scored, 4) if scored else None,
            "primaryLenient": round(agree_lenient / scored, 4) if scored else None,
            "direction": round(agree_direction / scored, 4) if scored else None,
            "certainty": round(agree_certainty / scored, 4) if scored else None,
            "allThreeAxes": round(agree_all / scored, 4) if scored else None,
        },
        "crossCheckedRows": agree_all,
        "reviewQueueRows": len(queue),
        "byConfidence": {
            level: {
                "rows": count,
                "agreedAllAxes": agree_by_confidence[level],
                "agreementRatio": round(agree_by_confidence[level] / count, 4),
            }
            for level, count in sorted(by_confidence.items())
        },
        "topPrimaryDisagreements": confusions.most_common(15),
        "queuePath": str(arguments.queue),
        "applied": bool(arguments.apply),
        "messageKo": (
            "일치는 '사람이 나중에 봐도 된다'는 뜻이지 '맞다'는 뜻이 아니다. "
            "승격 게이트는 계속 HUMAN_CONFIRMED만 센다."
        ),
    }
    arguments.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
