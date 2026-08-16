#!/usr/bin/env python3
"""외부 검수자용 눈가림 입력을 만든다 (단계 0, 겹 C).

현재 라벨을 보여주면 검수자가 그대로 동의해 대조가 무의미해진다. 그래서 원문과
유형 정의만 주고 판정을 따로 받는다.

유형 정의에는 **키워드를 넣지 않는다.** 지금 라벨은 키워드 매칭이 붙인 것이라,
같은 키워드를 보여주면 검수자가 같은 규칙을 흉내내 같은 오차를 낸다. 판정이
독립적이어야 대조에 값어치가 있다.

산출물은 저작권 원문을 담으므로 gitignore된 research/ontology/에만 둔다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.ontology import VOCABULARY


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="눈가림 검수 입력과 유형 정의를 만듭니다."
    )
    parser.add_argument(
        "--supplement",
        type=Path,
        default=REPOSITORY_ROOT / "tests" / "ontology" / "goldset_supplement.tsv",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "labels.jsonl",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology",
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

    keys: list[str] = []
    for line in arguments.supplement.open(encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        keys.append(line.split("\t")[0])

    missing = [key for key in keys if key not in text_by_key]
    rows = [(key, text_by_key[key]) for key in keys if key in text_by_key]

    arguments.out_dir.mkdir(parents=True, exist_ok=True)
    input_path = arguments.out_dir / "blind_review_input.tsv"
    input_path.write_text(
        "\n".join(
            [
                "# 검수 입력 — key와 원문만 있다. 현재 라벨은 일부러 담지 않는다.",
                "# 열: key, raw_text",
                *(f"{key}\t{text}" for key, text in rows),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    definitions_path = arguments.out_dir / "catalyst_type_definitions.tsv"
    definitions_path.write_text(
        "\n".join(
            [
                "# 소재 유형 정의 — 판정에 쓸 목록이다. 키워드는 일부러 담지 않는다.",
                "# 해당 없으면 OTHER를 쓴다.",
                "# 열: type_id, name_ko, description_ko",
                *(
                    f"{item.type_id}\t{item.name_ko}\t{item.description_ko}"
                    for item in VOCABULARY
                ),
                "OTHER\t기타\t위 유형 어디에도 맞지 않는 사유.",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            {
                "status": "SUCCEEDED" if not missing else "PARTIAL",
                "inputPath": str(input_path),
                "definitionsPath": str(definitions_path),
                "rows": len(rows),
                "types": len(VOCABULARY) + 1,
                "missingKeys": len(missing),
                "messageKo": "원문을 담으므로 두 파일 모두 gitignore된 위치에 둔다.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
