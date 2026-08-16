#!/usr/bin/env python3
"""구 DAY-JA-VIEW DB를 인포스탁 collector 산출물 형식으로 내보낸다.

파이프라인은 테마 명단을 DB가 아니라 `INFOSTOCK_IMPORT_DIR`의 수집본 디렉터리에서 읽는다
(`apps/api/serve.py` → `load_theme_universe` → `load_existing_collection`). 그래서 구 DB를
현재 스키마로 옮기는 것만으로는 화면에 뜨지 않는다. 이 스크립트는 같은 구 DB에서
collector가 만드는 것과 같은 모양의 파일을 만든다.

실제 수집본이 준비되면 이 디렉터리를 그것으로 바꿔 끼우면 된다. 제품 코드는 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from packages.infostock.hashing import sha256_json

SOURCE_URL = "https://infostock.co.kr/Theme/ThemeDB/{theme_id}"
REQUIRED_THEME_COUNT = 280


def _envelope(source_type: str) -> dict[str, object]:
    return {"schemaVersion": "1.0.0", "source": "infostock", "sourceType": source_type}


def _split_stocks(raw: str | None) -> list[tuple[str | None, str]]:
    """`079650-서산|198440-강동씨앤엘` 형태를 (코드, 이름)으로 나눈다."""
    if not raw:
        return []
    out: list[tuple[str | None, str]] = []
    for chunk in raw.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        code, _, name = chunk.partition("-")
        code = code.strip()
        name = name.strip()
        if name and len(code) == 6 and code.isalnum():
            out.append((code, name))
        elif chunk:
            out.append((None, chunk))
    return out


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="구 DB를 collector 수집본 형식으로 내보냅니다.")
    parser.add_argument("--source", required=True, help="구 DB DSN")
    parser.add_argument("--out", required=True, type=Path, help="내보낼 디렉터리")
    parser.add_argument("--since", help="이 날짜 이후 사건만 담는다 (YYYY-MM-DD)")
    args = parser.parse_args()

    captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(args.source) as source:
        cur = source.cursor()
        cur.execute(
            "select theme_code, name, coalesce(outline,'') from theme.themes order by theme_code"
        )
        themes = cur.fetchall()
        if len(themes) != REQUIRED_THEME_COUNT:
            raise SystemExit(f"테마가 {REQUIRED_THEME_COUNT}개여야 합니다: {len(themes)}개")

        where = "where o.session_date >= %s" if args.since else ""
        cur.execute(
            f"""
            select o.theme_code, o.session_date, o.content, o.lead_stock_raw, o.occurrence_id
            from theme.occurrences o {where}
            order by o.theme_code, o.session_date desc, o.occurrence_id
            """,
            (args.since,) if args.since else (),
        )
        history_by_theme: dict[int, list[tuple]] = defaultdict(list)
        for row in cur.fetchall():
            history_by_theme[row[0]].append(row)

        # 테마의 현재 구성종목. 가장 최근 사건의 명단을 그 테마의 관련주로 쓴다.
        cur.execute(
            """
            select o.theme_code, m.source_security_code, s.display_name, m.source_order
            from theme.occurrence_members m
            join theme.occurrences o on o.occurrence_id = m.occurrence_id
            join market.securities s on s.security_id = m.security_id
            where o.session_date = (
                select max(o2.session_date) from theme.occurrences o2 where o2.theme_code = o.theme_code
            )
            order by o.theme_code, m.source_order
            """
        )
        # 같은 날 여러 사건이 있으면 같은 종목이 여러 번 나온다. 코드 기준으로 한 번만 담는다.
        related_by_theme: dict[int, list[tuple]] = defaultdict(list)
        seen_codes: dict[int, set[str]] = defaultdict(set)
        for theme_code, code, name, order in cur.fetchall():
            name = (name or "").strip()
            if not name:
                continue  # 이름 없는 행은 담지 않는다. 계약이 빈 이름을 거부한다.
            key = (code or "").strip()
            if key and key in seen_codes[theme_code]:
                continue
            if key:
                seen_codes[theme_code].add(key)
            related_by_theme[theme_code].append((key or None, name, order))

    index_items: list[dict[str, object]] = []
    manifest_themes: list[dict[str, object]] = []
    history_total = 0
    related_total = 0
    duplicate_total = 0
    missing_date_total = 0
    missing_content_total = 0
    missing_leader_code_total = 0
    missing_related_code_total = 0

    for order, (theme_code, name, outline) in enumerate(themes):
        theme_id = str(theme_code)
        url = SOURCE_URL.format(theme_id=theme_id)
        index_items.append(
            {"sourceOrder": order, "themeId": theme_id, "themeName": name, "sourceUrl": url}
        )

        history: list[dict[str, object]] = []
        for position, (_, session_date, content, lead_raw, occurrence_id) in enumerate(
            history_by_theme.get(theme_code, [])
        ):
            history.append(
                {
                    "sourceOrder": position,
                    "sourceId": str(occurrence_id),
                    "date": session_date.isoformat() if session_date else None,
                    "content": (content or "").strip(),
                    "leaders": [
                        {"sourceOrder": i, "stockCode": code, "name": stock_name}
                        for i, (code, stock_name) in enumerate(
                            [x for x in _split_stocks(lead_raw) if x[1].strip()]
                        )
                    ],
                }
            )
        history_total += len(history)
        # 계약은 (content, date) 지문이 겹치면 두 번째부터 SOURCE_DUPLICATE로 센다.
        fingerprints = Counter(
            sha256_json({"content": h["content"], "date": h["date"]}) for h in history
        )
        duplicate_total += sum(count - 1 for count in fingerprints.values() if count > 1)
        for h in history:
            if h["date"] is None:
                missing_date_total += 1
            elif not h["content"]:
                missing_content_total += 1
            missing_leader_code_total += sum(1 for r in h["leaders"] if not r["stockCode"])

        # 구 DB에는 편입 사유가 없다. 빈 문자열은 허용되므로 지어내지 않고 비워 둔다.
        related_source = related_by_theme.get(theme_code, [])
        missing_related_code_total += sum(1 for code, _, _ in related_source if not code)
        related = [
            {
                "sourceOrder": i,
                "stockCode": code,
                "name": stock_name,
                "rationale": "",
            }
            for i, (code, stock_name, _) in enumerate(related_source)
        ]

        detail = {
            **_envelope("theme_detail"),
            "themeId": theme_id,
            "themeName": name,
            "description": outline,
            "sourceUrl": url,
            "capturedAt": captured_at,
            "historyComplete": True,
            "history": history,
            "relatedStocks": related,
        }
        detail["contentHash"] = sha256_json(
            {
                "themeId": theme_id,
                "themeName": name,
                "description": outline,
                "history": history,
                "relatedStocks": related,
            }
        )
        _write(out / f"theme-{theme_id}.json", detail)
        manifest_themes.append(
            {
                "themeId": theme_id,
                "themeName": name,
                "historyCount": len(history),
                "relatedStockCount": len(related),
                "contentHash": detail["contentHash"],
            }
        )
        related_total += len(related)

    index = {
        **_envelope("theme_index"),
        "capturedAt": captured_at,
        "items": index_items,
        "contentHash": sha256_json(index_items),
    }
    _write(out / "theme-index.json", index)

    manifest = {
        "schemaVersion": "1.0.0",
        "dataset": "infostock-theme-full-sync",
        "capturedAt": captured_at,
        "requestedThemeCount": REQUIRED_THEME_COUNT,
        "completedThemeCount": REQUIRED_THEME_COUNT,
        "failedThemeCount": 0,
        "failures": [],
        "finishedAt": captured_at,
        "apiBaseUrl": "https://infostock.co.kr",
        "historyCount": history_total,
        "relatedStockCount": related_total,
        "quality": {
            "duplicateHistoryCount": duplicate_total,
            "missingHistoryDateCount": missing_date_total,
            "missingHistoryContentCount": missing_content_total,
            "missingLeaderCodeCount": missing_leader_code_total,
            "missingRelatedStockCodeCount": missing_related_code_total,
        },
        "themes": manifest_themes,
    }
    _write(out / "manifest.json", manifest)

    print(f"테마 {len(themes):,} · 사건 {history_total:,} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
