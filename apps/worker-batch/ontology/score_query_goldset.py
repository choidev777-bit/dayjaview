#!/usr/bin/env python3
"""겹 A 질문 해석 gold set 채점 (단계 5).

`tests/ontology/query_goldset.tsv`의 문장을 실제 해석기(`plan_question`)에
넣고 질의 유형·슬롯이 정답과 맞는지 센다. dev/test는 파일 안 `split` 열을
그대로 따른다 — test split을 보며 규칙을 고치면 측정이 무의미해진다.

**승격 판정에는 `HUMAN_CONFIRMED` 행만 쓴다(계획서 11.1.2).** 지금 gold set은
전부 `AI_DRAFT`이므로 이 스크립트의 수치는 개발용 관측치이며 게이트를 열지
않는다. 그 사실을 산출 JSON의 `promotionEligible`이 그대로 말한다.

catalog는 수집본의 실제 테마·종목 이름으로 만든다. gold set이 그 이름에서
슬롯 값을 뽑았으므로 같은 원천을 써야 이름 해석이 재현된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.ontology import (  # noqa: E402
    CompanyAliasDraft,
    CompanyDraft,
    CompanyInstrumentDraft,
    CompanyMaster,
    QuestionCatalog,
    ThemeEntry,
    normalize_company_name,
    plan_question,
)

DEFAULT_TODAY = date(2026, 8, 17)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="질문 해석 gold set을 채점합니다.")
    parser.add_argument(
        "--goldset",
        type=Path,
        default=REPOSITORY_ROOT / "tests" / "ontology" / "query_goldset.tsv",
    )
    parser.add_argument(
        "--collection",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "infostock" / "import",
    )
    parser.add_argument(
        "--krx-names",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "krx_name_windows.json",
    )
    parser.add_argument("--today", type=date.fromisoformat, default=DEFAULT_TODAY)
    parser.add_argument("--subset", choices=("dev", "test", "all"), default="all")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "query_goldset_score.json",
    )
    return parser


def _alias(
    name: str,
    *,
    alias_type: str,
    valid_from: date | None,
    valid_to: date | None,
) -> CompanyAliasDraft:
    return CompanyAliasDraft(
        alias=name,
        normalized_alias=normalize_company_name(name),
        alias_type=alias_type,  # type: ignore[arg-type]
        validity_basis="KRX_LISTING",
        source_authority="KRX_LISTING",
        valid_from=valid_from,
        valid_to=valid_to,
        mention_count=1,
    )


def _catalog(collection: Path, krx_names: Path) -> QuestionCatalog:
    themes: list[ThemeEntry] = []
    by_code: dict[str, str] = {}
    for path in sorted(collection.glob("theme-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = payload.get("themeName")
        theme_id = str(payload.get("sourceThemeId") or path.stem.split("-", 1)[-1])
        if name:
            themes.append(ThemeEntry(theme_id, str(name)))
        for stock in payload.get("relatedStocks") or []:
            code, stock_name = stock.get("stockCode"), stock.get("name")
            if code and stock_name:
                by_code.setdefault(str(code), str(stock_name))

    past: dict[str, list[tuple[str, date | None, date | None]]] = defaultdict(list)
    if krx_names.is_file():
        payload = json.loads(krx_names.read_text(encoding="utf-8"))
        windows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for window in payload.get("windows") or []:
            code = str(window.get("stockCode") or "")
            if code and window.get("name"):
                windows[code].append(window)
        for code, entries in windows.items():
            entries.sort(key=lambda item: str(item.get("firstDate") or ""))
            current = str(entries[-1]["name"])
            by_code.setdefault(code, current)
            for entry in entries[:-1]:
                name = str(entry["name"])
                if name == current:
                    continue
                past[code].append(
                    (
                        name,
                        _optional_date(entry.get("firstDate")),
                        _optional_date(entry.get("lastDate")),
                    )
                )

    companies = tuple(
        CompanyDraft(
            seed_stock_code=code,
            canonical_name=name,
            name_basis="KRX_LISTING",
            dart_corp_code=None,
            aliases=(
                _alias(name, alias_type="CURRENT_NAME", valid_from=None, valid_to=None),
                *(
                    _alias(
                        old,
                        alias_type="PAST_NAME",
                        valid_from=valid_from,
                        valid_to=valid_to,
                    )
                    for old, valid_from, valid_to in past.get(code, ())
                ),
            ),
            instruments=(
                CompanyInstrumentDraft(
                    stock_code=code,
                    share_class="COMMON",
                    link_basis="STOCK_CODE",
                    valid_from=None,
                    valid_to=None,
                ),
            ),
            revisions=(),
        )
        for code, name in sorted(by_code.items())
    )
    return QuestionCatalog(
        company_master=CompanyMaster(
            master_version="company-master/goldset",
            companies=companies,
            unresolved=(),
        ),
        themes=tuple(themes),
    )


def _optional_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "questionId": parts[0],
                "sampleGroup": parts[1],
                "question": parts[2],
                "goldQueryId": parts[3],
                "goldSlots": json.loads(parts[4] or "{}"),
                "split": parts[5],
                "reviewStatus": parts[6] if len(parts) > 6 else "AI_DRAFT",
            }
        )
    return rows


def _predicted(question: str, catalog: QuestionCatalog, today: date) -> dict[str, Any]:
    result = plan_question(question, catalog=catalog, today=today)
    if result.plan is not None:
        plan = result.plan
        return {
            "queryId": plan.query_type.value,
            "direction": plan.direction,
            "date": None if plan.date is None else plan.date.value.isoformat(),
            "catalystType": (
                None if plan.catalyst_type is None else plan.catalyst_type.type_id
            ),
            "stockCode": None if plan.company is None else plan.company.seed_stock_code,
            "themeCount": len(plan.themes),
            "hasPeriod": plan.period is not None,
        }
    assert result.failure is not None
    return {
        "queryId": result.failure.reason.value,
        "direction": None,
        "date": None,
        "catalystType": None,
        "stockCode": None,
        "themeCount": 0,
        "hasPeriod": False,
        "candidateCount": len(result.failure.candidates),
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.goldset.is_file():
        print(json.dumps({"status": "FAILED", "messageKo": "gold set이 없습니다."}))
        return 2
    if not any(arguments.collection.glob("theme-*.json")):
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": f"수집본을 찾지 못했습니다: {arguments.collection}",
                },
                ensure_ascii=False,
            )
        )
        return 2

    catalog = _catalog(arguments.collection, arguments.krx_names)
    rows = [
        row
        for row in _rows(arguments.goldset)
        if arguments.subset == "all" or row["split"] == arguments.subset
    ]

    totals: Counter[str] = Counter()
    by_group: dict[str, Counter[str]] = defaultdict(Counter)
    confusions: Counter[str] = Counter()
    review_counts: Counter[str] = Counter()
    for row in rows:
        gold, slots = row["goldQueryId"], row["goldSlots"]
        predicted = _predicted(row["question"], catalog, arguments.today)
        group = row["sampleGroup"]
        review_counts[row["reviewStatus"]] += 1
        totals["rows"] += 1
        by_group[group]["rows"] += 1

        type_ok = predicted["queryId"] == gold
        totals["typeHit"] += int(type_ok)
        by_group[group]["typeHit"] += int(type_ok)
        if not type_ok:
            confusions[f"{gold}->{predicted['queryId']}"] += 1

        if "direction" in slots:
            totals["directionRows"] += 1
            hit = predicted["direction"] == slots["direction"]
            totals["directionHit"] += int(hit)
            by_group[group]["directionHit"] += int(hit)
            by_group[group]["directionRows"] += 1
        if isinstance(slots.get("date"), str) and not slots["date"].startswith(
            "RELATIVE:"
        ):
            totals["dateRows"] += 1
            hit = int(predicted["date"] == slots["date"])
            totals["dateHit"] += hit
            # `6/29`만 적힌 문장은 연도를 원문에서 되찾을 수 없다. 해석기는
            # 가장 가까운 과거로 읽으므로 gold의 연도와 다를 수밖에 없다.
            if slots["date"] in row["question"]:
                totals["dateRecoverableRows"] += 1
                totals["dateRecoverableHit"] += hit
        if "catalystType" in slots:
            totals["catalystRows"] += 1
            totals["catalystHit"] += int(
                predicted["catalystType"] == slots["catalystType"]
            )
        if "stockCode" in slots:
            totals["companyRows"] += 1
            totals["companyHit"] += int(predicted["stockCode"] == slots["stockCode"])
        if str(slots.get("stock", "")).startswith("AMBIGUOUS:"):
            totals["ambiguousRows"] += 1
            # 애매한 이름은 임의로 고르지 않고 후보를 돌려줘야 한다(계획서 8.1절).
            totals["ambiguousHeld"] += int(
                predicted["queryId"] == "AMBIGUOUS_ALIAS"
                or predicted["stockCode"] is None
            )

    def _ratio(hit: str, total: str) -> float | None:
        denominator = totals[total]
        return None if not denominator else round(totals[hit] / denominator, 4)

    report = {
        "status": "SUCCEEDED",
        "goldset": str(arguments.goldset),
        "subset": arguments.subset,
        "today": arguments.today.isoformat(),
        "rows": totals["rows"],
        "reviewStatusCounts": dict(sorted(review_counts.items())),
        "humanConfirmedRatio": round(
            review_counts.get("HUMAN_CONFIRMED", 0) / max(totals["rows"], 1), 4
        ),
        "promotionEligible": review_counts.get("HUMAN_CONFIRMED", 0) == totals["rows"],
        "queryTypeAccuracy": _ratio("typeHit", "rows"),
        "directionAccuracy": _ratio("directionHit", "directionRows"),
        "dateAccuracy": _ratio("dateHit", "dateRows"),
        "dateRecoverableAccuracy": _ratio("dateRecoverableHit", "dateRecoverableRows"),
        "dateAccuracyCeiling": _ratio("dateRecoverableRows", "dateRows"),
        "catalystTypeAccuracy": _ratio("catalystHit", "catalystRows"),
        "companyResolutionAccuracy": _ratio("companyHit", "companyRows"),
        "ambiguousHeldRatio": _ratio("ambiguousHeld", "ambiguousRows"),
        "byGroup": {
            group: {
                "rows": counter["rows"],
                "queryTypeAccuracy": round(counter["typeHit"] / counter["rows"], 4),
                "directionAccuracy": (
                    None
                    if not counter["directionRows"]
                    else round(counter["directionHit"] / counter["directionRows"], 4)
                ),
            }
            for group, counter in sorted(by_group.items())
        },
        "topConfusions": dict(confusions.most_common(15)),
        "notesKo": [
            "gold set이 전부 AI_DRAFT이므로 이 수치는 개발 관측치이고 11.2절 승격 "
            "판정에 쓰지 않는다.",
            "`6/29`처럼 연도가 없는 문장은 gold의 연도를 원문에서 되찾을 수 없다. "
            "dateAccuracyCeiling이 그 상한이고 dateRecoverableAccuracy가 실제 성능이다.",
            "gold의 AMBIGUOUS 표본은 이름 접두사 충돌이지만, 해석기는 정확히 일치하는 "
            "이름을 애매하다고 보지 않는다. 같은 alias를 두 회사가 쓸 때만 후보를 "
            "돌려준다(계획서 8.1절). ambiguousHeldRatio는 그 차이를 잰 값이다.",
        ],
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
