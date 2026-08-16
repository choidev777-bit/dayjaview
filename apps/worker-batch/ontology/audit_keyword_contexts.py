#!/usr/bin/env python3
"""키워드 발화 문맥 감사 — 부분 문자열 충돌 사냥 (E-17).

어휘 키워드가 전수(labels.jsonl 원문)에서 어떤 앞뒤 어절과 함께 나오는지
상위 빈도를 나열한다. 사람이 훑으며 "과거의 중국산→국산" 류 오발화를
찾는 용도다. 원문은 로컬 labels.jsonl에서만 읽고 산출물도 로컬
research/ontology/ 아래에만 쓴다.

사용:
  uv run python apps/worker-batch/ontology/audit_keyword_contexts.py --keyword 동반 --keyword 봉쇄
  uv run python apps/worker-batch/ontology/audit_keyword_contexts.py   # 어휘 전체
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

from packages.ontology import VOCABULARY


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="키워드 발화 문맥 상위 빈도를 나열합니다.")
    parser.add_argument(
        "--labels",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "labels.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "keyword_context_audit.txt",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=None,
        help="감사할 키워드(복수 지정). 생략하면 어휘 전체.",
    )
    parser.add_argument("--top", type=int, default=20, help="키워드당 상위 문맥 수")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.labels.is_file():
        print("labels.jsonl이 없습니다. label_theme_history.py를 먼저 실행하세요.")
        return 2
    texts = [
        json.loads(line)["rawText"]
        for line in arguments.labels.open(encoding="utf-8")
    ]
    keyword_to_type: dict[str, str] = {
        keyword: definition.type_id
        for definition in VOCABULARY
        for keyword in definition.keywords
    }
    targets = arguments.keyword or sorted(keyword_to_type)
    lines: list[str] = []
    for keyword in targets:
        contexts: Counter[str] = Counter()
        hits = 0
        for text in texts:
            cursor = text.find(keyword)
            while cursor != -1:
                hits += 1
                before = text[max(0, cursor - 8) : cursor].split()
                after = text[cursor + len(keyword) : cursor + len(keyword) + 8].split()
                contexts[
                    f"{before[-1] if before else '^'}◇{keyword}◇{after[0] if after else '$'}"
                ] += 1
                cursor = text.find(keyword, cursor + 1)
        type_id = keyword_to_type.get(keyword, "?")
        lines.append(f"=== {keyword!r} [{type_id}] — {hits}회 ===")
        for context, count in contexts.most_common(arguments.top):
            lines.append(f"  {count:5d}x {context}")
        lines.append("")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(targets)}개 키워드 감사 완료 → {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
