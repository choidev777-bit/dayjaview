"""검수팩 역할 표본이 구조 참조의 출처 명단을 같이 싣는지 본다.

RELATED는 테마 구성종목 명단에서만 나오는데 그 명단은 rawText에 없다. 명단을
싣지 않으면 검수자는 이름이 어디서 왔는지 확인할 수 없고, 실제로 사람 검수에서
`관련주 목록에만 등장` 30건이 전부 "제시 원문에 해당 종목명이 없음"으로
떨어졌다(2026-08-18 검수 결과).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    REPOSITORY_ROOT / "apps" / "worker-batch" / "ontology"
    / "build_human_quality_review.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_human_quality_review", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _history() -> dict[str, object]:
    """상세 문단 1건 + 주도주 2 + 구성종목 3. 구성종목은 rawText에 없다."""

    raw_text = "삼성전자, HBM4 개발 순항 기대감 등에 상승(주도주 : 케이씨에스, 누리플렉스)"
    mentions: list[dict[str, object]] = [
        {
            "mentionKind": "BODY",
            "mentionText": "삼성전자",
            "seedStockCode": "005930",
            "roles": [
                {
                    "role": "ACTOR",
                    "extractionBasis": "BODY_RULE",
                    "start": 0,
                    "end": 4,
                    "evidenceText": "삼성전자",
                }
            ],
        }
    ]
    for name in ("케이씨에스", "누리플렉스"):
        mentions.append(
            {
                "mentionKind": "LEADER_LIST",
                "mentionText": name,
                "seedStockCode": "",
                "roles": [
                    {
                        "role": "LEADER",
                        "extractionBasis": "STRUCTURED_LEADER",
                        "start": 0,
                        "end": len(name),
                        "evidenceText": name,
                    }
                ],
            }
        )
    for name in ("공구우먼", "파워로직스", "인벤티지랩"):
        mentions.append(
            {
                "mentionKind": "MEMBERSHIP",
                "mentionText": name,
                "seedStockCode": "",
                "roles": [
                    {
                        "role": "RELATED",
                        "extractionBasis": "STRUCTURED_MEMBERSHIP",
                        "start": 0,
                        "end": len(name),
                        "evidenceText": name,
                    }
                ],
            }
        )
    return {
        "sourceThemeId": "thm_1",
        "sourceHistoryKey": "hist_1",
        "themeName": "반도체",
        "eventDate": "2026-08-18",
        "rawText": raw_text,
        "mentions": mentions,
    }


def test_structured_reference_rows_carry_their_source_list(tmp_path: Path) -> None:
    module = _module()
    history = _history()
    path = tmp_path / "company_labels.jsonl"
    path.write_text(
        json.dumps(history, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    _raw_by_key, rows, _population = module._load_histories_and_role_sample(path)
    by_role = {str(row["candidate_role"]): row for row in rows}

    related = by_role["RELATED"]
    # 구성종목 이름은 rawText에 없다. 명단이 없으면 검수 자체가 불가능하다.
    assert related["mention_text"] not in str(related["raw_text"])
    assert (
        related["candidate_reference_list"] == "공구우먼, 파워로직스, 인벤티지랩"
    )
    assert str(related["candidate_reference_position"]).endswith("/3")

    # 주도주 명단은 구성종목 명단과 섞이지 않는다.
    leader = by_role["LEADER"]
    assert leader["candidate_reference_list"] == "케이씨에스, 누리플렉스"
    assert str(leader["candidate_reference_position"]).endswith("/2")

    # 본문 역할은 rawText로 검증되므로 명단 자리를 비워 둔다.
    actor = by_role["ACTOR"]
    assert actor["candidate_reference_list"] == ""
    assert actor["candidate_reference_position"] == ""
