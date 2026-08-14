"""Build the deterministic committed 280-theme synthetic source fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.infostock.hashing import fixture_bundle_hash, sha256_json

THEME_COUNT = 280
INDEX_COLLECTED_AT = "2026-08-14T00:00:00+00:00"
DETAIL_COLLECTED_AT = "2026-08-14T00:01:00+00:00"


def _snapshot(
    *,
    source_url: str,
    collected_at: str,
    raw_payload: dict[str, object],
    source_theme_id: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "sourceUrl": source_url,
        "collectedAt": collected_at,
        "isComplete": True,
        "rawHash": sha256_json(raw_payload),
        "rawPayload": raw_payload,
    }
    if source_theme_id is not None:
        value["sourceThemeId"] = source_theme_id
    return value


def build_fixture_payload() -> dict[str, Any]:
    index_items: list[dict[str, str]] = []
    detail_snapshots: list[dict[str, object]] = []
    for number in range(1, THEME_COUNT + 1):
        theme_id = str(1000 + number)
        theme_name = f"합성 테마 {number:03d}"
        index_items.append({"code": theme_id, "name": theme_name})
        current_code_a = f"{100000 + number:06d}"
        current_code_b = f"{200000 + number:06d}"
        historical_leader_code = f"{300000 + number:06d}"
        raw_payload: dict[str, object] = {
            "success": True,
            "data": {
                "theme": {
                    "code": theme_id,
                    "name": theme_name,
                    "outline": f"{theme_name}의 synthetic 설명입니다.",
                },
                "items": [
                    {
                        "B2Bseq": f"SYN-HISTORY-{theme_id}",
                        "showDate": "20260813",
                        "createTime": "20260813100000",
                        "lastUpdateTime": "20260813183000",
                        "content": f"{theme_name} 관련 종목이 synthetic 근거로 상승",
                        "LEAD_STOCK": (
                            f"{historical_leader_code}-과거 주도주 {number:03d}"
                        ),
                        "STOCKS": (
                            f"{historical_leader_code}-과거 주도주 {number:03d}"
                        ),
                        "CREATE_WRITER": "synthetic-fixture",
                        "CHART": "0",
                    }
                ],
                "stockItems": [
                    {
                        "code": current_code_a,
                        "name": f"현재 관련주 A-{number:03d}",
                        "outline": f"{theme_name} 현재 편입 이유 A",
                        "index": "0",
                    },
                    {
                        "code": current_code_b,
                        "name": f"현재 관련주 B-{number:03d}",
                        "outline": f"{theme_name} 현재 편입 이유 B",
                        "index": "1",
                    },
                ],
            },
        }
        detail_snapshots.append(
            _snapshot(
                source_url=f"https://infostock.co.kr/Theme/ThemeDB/{theme_id}",
                collected_at=DETAIL_COLLECTED_AT,
                source_theme_id=theme_id,
                raw_payload=raw_payload,
            )
        )

    index_raw: dict[str, object] = {
        "success": True,
        "data": {"items": index_items},
    }
    payload: dict[str, Any] = {
        "fixtureVersion": "1.0.0",
        "dataset": "synthetic-infostock-theme-full-sync-280",
        "source": "INFOSTOCK",
        "rightsScope": "FIXTURE_ONLY",
        "parserVersion": "collect-infostock-fixture/1.0.0",
        "expectedThemeCount": THEME_COUNT,
        "indexSnapshot": _snapshot(
            source_url="https://infostock.co.kr/Theme/ThemeDB/ThemeAll",
            collected_at=INDEX_COLLECTED_AT,
            raw_payload=index_raw,
        ),
        "detailSnapshots": detail_snapshots,
    }
    payload["bundleHash"] = fixture_bundle_hash(payload)
    return payload


def main() -> None:
    target = Path(__file__).parent / "fixtures" / "infostock-280.synthetic.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_fixture_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
