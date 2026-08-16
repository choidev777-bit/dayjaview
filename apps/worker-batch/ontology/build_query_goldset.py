#!/usr/bin/env python3
"""질문 해석 gold set 초안을 만든다 (단계 0, 겹 A).

문장 하나가 어느 질의 유형이고 슬롯이 무엇인지를 채점하기 위한 표본이다.
표본군은 22개다 — 상승·하락 대칭이 필요한 5종은 방향별로 나누고 나머지
12종은 하나씩이다. 군마다 test 30 + dev 15를 채우고, 실패·난이도 케이스를
따로 더한다.

슬롯 값은 지어내지 않고 수집본의 실제 테마·종목·발행일에서 뽑는다.

**문장은 이 스크립트가 만든 초안이라 만든 쪽 말투에 치우친다.** 실제 사용자는
더 짧고 불완전하게 친다. 짧은 변형을 일부러 섞었지만 한계는 남는다. 운영 후
해석 실패 사유 집계로 교체·보강한다(계획서 11.1.3).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.ontology import VOCABULARY

SAMPLE_SEED = 20260817
TEST_PER_GROUP = 30
DEV_PER_GROUP = 15

# (표본군, 질의 ID, 방향, 문장 틀). 틀 안의 {이름}은 슬롯 값으로 채운다.
GROUPS: tuple[tuple[str, str, str | None, tuple[str, ...]], ...] = (
    ("DAY_MOVERS_UP", "DAY_MOVERS", "UP", (
        "{date}에 뭐가 올랐어?",
        "{date} 오른 테마 알려줘",
        "{short} 뭐 올랐어",
        "{date} 상승 테마 정리해줘",
        "{date}에 어떤 테마가 강세였나요?",
        "{short} 상승",
    )),
    ("DAY_MOVERS_DOWN", "DAY_MOVERS", "DOWN", (
        "{date}에 뭐가 빠졌어?",
        "{date} 하락한 테마 알려줘",
        "{short} 뭐 떨어졌어",
        "{date} 약세 테마 정리해줘",
        "{date}에 어떤 테마가 하락했나요?",
        "{short} 하락",
    )),
    ("PERIOD_SUMMARY_UP", "PERIOD_SUMMARY", "UP", (
        "{start}부터 {end}까지 뭐가 올랐어?",
        "{start}~{end} 상승 흐름 정리해줘",
        "{start} {end} 사이 강세 테마",
        "{start}~{end} 시장 어땠어?",
        "{start}부터 {end}까지 오른 테마 요약",
    )),
    ("PERIOD_SUMMARY_DOWN", "PERIOD_SUMMARY", "DOWN", (
        "{start}부터 {end}까지 뭐가 빠졌어?",
        "{start}~{end} 하락 흐름 정리해줘",
        "{start} {end} 사이 약세 테마",
        "{start}~{end} 하락장 정리",
        "{start}부터 {end}까지 내린 테마 요약",
    )),
    ("STOCK_DAY_REASON_UP", "STOCK_DAY_REASON", "UP", (
        "{stock} {date}에 왜 올랐어?",
        "{stock} {short} 상승 이유",
        "{stock} 왜 올랐어 {short}",
        "{date} {stock} 급등 사유 알려줘",
        "{stock}이 {date}에 오른 까닭은?",
        "{stock} {short} 왜 올라",
    )),
    ("STOCK_DAY_REASON_DOWN", "STOCK_DAY_REASON", "DOWN", (
        "{stock} {date}에 왜 빠졌어?",
        "{stock} {short} 하락 이유",
        "{stock} 왜 떨어졌어 {short}",
        "{date} {stock} 급락 사유 알려줘",
        "{stock}이 {date}에 내린 까닭은?",
        "{stock} {short} 왜 내려",
    )),
    ("STOCK_TOP_MOVES_UP", "STOCK_TOP_MOVES", "UP", (
        "{stock} 최근 많이 오른 날 언제야?",
        "{stock} {year} 크게 뛴 날 알려줘",
        "{stock} 상승 폭 컸던 날",
        "{stock} {year} 급등일 정리해줘",
        "{stock} 많이 오른 날",
    )),
    ("STOCK_TOP_MOVES_DOWN", "STOCK_TOP_MOVES", "DOWN", (
        "{stock} 최근 많이 빠진 날 언제야?",
        "{stock} {year} 크게 내린 날 알려줘",
        "{stock} 하락 폭 컸던 날",
        "{stock} {year} 급락일 정리해줘",
        "{stock} 많이 떨어진 날",
    )),
    ("THEME_COMPARISON_UP", "THEME_COMPARISON", "UP", (
        "{theme}랑 {theme2} 중에 {year} 중 어디가 더 셌어?",
        "{theme} {theme2} 상승 비교해줘",
        "{year} {theme}와 {theme2} 중 강했던 쪽",
        "{theme} vs {theme2} 어디가 더 올랐어",
    )),
    ("THEME_COMPARISON_DOWN", "THEME_COMPARISON", "DOWN", (
        "{theme}랑 {theme2} 중에 {year} 중 어디가 더 빠졌어?",
        "{theme} {theme2} 하락 비교해줘",
        "{year} {theme}와 {theme2} 중 약했던 쪽",
        "{theme} vs {theme2} 어디가 더 내렸어",
    )),
    ("STOCK_THEME_MEMBERSHIP", "STOCK_THEME_MEMBERSHIP", None, (
        "{stock} 어떤 테마에 속해?",
        "{stock}은 무슨 테마야?",
        "{stock} 테마 뭐뭐 있어?",
        "{stock}이 그 테마에 들어간 이유가 뭐야?",
        "{stock} 편입 테마와 사유 알려줘",
    )),
    ("STOCK_COOCCURRENCE", "STOCK_COOCCURRENCE", None, (
        "{stock}이랑 같이 움직이는 종목은?",
        "{stock}과 자주 같이 오른 종목 알려줘",
        "{stock} 동반 상승 종목 {year}",
        "{stock}이랑 붙어 다니는 종목",
    )),
    ("THEME_MEMBERS", "THEME_MEMBERS", None, (
        "{theme} 테마에 어떤 종목이 있어?",
        "{theme} 관련주 알려줘",
        "{theme} 구성 종목이랑 편입 이유",
        "{theme}에 뭐가 들어가 있어?",
    )),
    ("THEME_HISTORY", "THEME_HISTORY", None, (
        "{theme} 테마 과거에 뭘로 움직였어?",
        "{theme} 과거 상승 사유 정리해줘",
        "{theme}가 과거에 반응한 소재",
        "{theme} 옛날에 왜 올랐었어?",
    )),
    ("THEME_FREQUENCY", "THEME_FREQUENCY", None, (
        "{year} 중 제일 자주 나온 테마는?",
        "{year} 많이 등장한 테마 알려줘",
        "{year} 빈도 높은 테마 순위",
        "{year} 자주 부각된 테마",
    )),
    ("CATALYST_THEME_REACTION", "CATALYST_THEME_REACTION", None, (
        "{catalyst} 소재에 과거 어떤 테마가 반응했어?",
        "{catalyst} 뜨면 어떤 테마가 움직였어?",
        "{catalyst} 관련해서 반응한 테마 알려줘",
        "{catalyst}에 반응한 테마",
    )),
    ("CATALYST_FREQUENCY", "CATALYST_FREQUENCY", None, (
        "{catalyst} 소재 {year} 몇 번 나왔어?",
        "{year} {catalyst} 건수 알려줘",
        "{catalyst} 과거 몇 번이야 {year}",
        "{year} {catalyst} 몇 건",
    )),
    ("CATALYST_CERTAINTY", "CATALYST_CERTAINTY", None, (
        "{catalyst} 소재는 기대감이었어 확정이었어?",
        "{catalyst} 확정 건이랑 기대 건 나눠줘",
        "{catalyst} 기대감 비율 알려줘",
        "{catalyst} 확정이야 기대야",
    )),
    ("CATALYST_CONTINUATION", "CATALYST_CONTINUATION", None, (
        "{theme}에서 {catalyst} 처음이야 다시 나온 거야?",
        "{catalyst} 재부각인지 알려줘",
        "{theme} {catalyst} 반복된 소재야?",
        "{catalyst} 처음 나온 소재야?",
    )),
    ("COMPANY_DIRECT_EVENT", "COMPANY_DIRECT_EVENT", None, (
        "{stock}이 직접 한 일만 알려줘",
        "{stock} 본인 사건만 보여줘",
        "{stock}이 주체인 사건",
        "{stock} 직접 발표한 것만",
    )),
    ("COMPANY_VALUE_SUMMARY", "COMPANY_VALUE_SUMMARY", None, (
        "{stock} 1조 넘는 수주 몇 건이야?",
        "{stock} 확정 수주액 합계 알려줘",
        "{stock} {year} 계약 금액 합계",
        "{stock} 1000억 이상 계약",
    )),
    ("COMPANY_HISTORICAL_OUTCOME", "COMPANY_HISTORICAL_OUTCOME", None, (
        "{stock} 수주 발표 뒤에 주가 어떻게 됐어?",
        "{stock} 그 사건 이후 흐름 알려줘",
        "{stock} 발표 후 5일 수익률",
        "{stock} 이후 반응 어땠어",
    )),
)

# 17종 어디에도 걸리지 않아야 하는 문장. 사유를 함께 고정한다.
OUT_OF_SCOPE: tuple[tuple[str, str], ...] = (
    ("{stock} 지금 사야 돼?", "OUT_OF_SCOPE"),
    ("{stock} 내일 오를까?", "OUT_OF_SCOPE"),
    ("{theme} 목표가 얼마야?", "OUT_OF_SCOPE"),
    ("{stock} 매수 타이밍 알려줘", "OUT_OF_SCOPE"),
    ("{stock} 손절해야 할까?", "OUT_OF_SCOPE"),
    ("{theme} 앞으로 전망 어때?", "OUT_OF_SCOPE"),
    ("{stock} 배당 얼마 줘?", "NOT_INTERPRETABLE"),
    ("{stock} 부채비율 알려줘", "NOT_INTERPRETABLE"),
    ("오늘 날씨 어때?", "NOT_INTERPRETABLE"),
    ("{stock} 대표이사 누구야?", "NOT_INTERPRETABLE"),
    ("코스피 지수 알려줘", "NOT_INTERPRETABLE"),
    ("{stock} 공매도 잔고", "NOT_INTERPRETABLE"),
)

# 슬롯 해석이 어려운 문장. 기대 슬롯을 명시해 회귀로 고정한다.
HARD_SLOTS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("어제 뭐가 올랐어?", "DAY_MOVERS", {"date": "RELATIVE:YESTERDAY"}),
    ("오늘 뭐 올랐어", "DAY_MOVERS", {"date": "RELATIVE:TODAY"}),
    ("지난주 시장 어땠어?", "PERIOD_SUMMARY", {"dateRange": "RELATIVE:LAST_WEEK"}),
    ("이번 달 상승 테마", "PERIOD_SUMMARY", {"dateRange": "RELATIVE:THIS_MONTH"}),
    ("포스코 ICT 어떤 테마야?", "STOCK_THEME_MEMBERSHIP", {"stock": "ALIAS:포스코 ICT"}),
    ("포스코DX 어떤 테마야?", "STOCK_THEME_MEMBERSHIP", {"stock": "포스코DX"}),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="질문 해석 gold set 초안을 만듭니다."
    )
    parser.add_argument(
        "--collection",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "infostock" / "import",
    )
    parser.add_argument(
        "--daily-lists",
        type=Path,
        default=REPOSITORY_ROOT
        / "data"
        / "infostock"
        / "daily-full-20260814"
        / "lists",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "tests" / "ontology" / "query_goldset.tsv",
    )
    return parser


def _slot_values(
    collection: Path, daily_lists: Path
) -> tuple[list[str], list[str], list[str]]:
    themes: list[str] = []
    stocks: list[str] = []
    for path in sorted(collection.glob("theme-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = payload.get("themeName")
        if not name:
            continue
        themes.append(str(name))
        for stock in payload.get("relatedStocks") or []:
            if stock.get("stockCode") and stock.get("name"):
                stocks.append(str(stock["name"]))
    dates: set[str] = set()
    for path in sorted(daily_lists.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in (payload.get("data") or {}).get("items") or []:
            value = str(item.get("sendDate") or "")
            if re.fullmatch(r"\d{8}", value):
                dates.add(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    return (
        sorted(set(themes)),
        sorted(set(stocks)),
        sorted(dates),
    )


def _periods(dates: Sequence[str]) -> list[str]:
    """기간 슬롯 후보. 연도만 쓰면 조합이 모자라 표본군을 못 채운다."""

    years = sorted({date[:4] for date in dates})
    months = sorted({f"{date[:4]}년 {int(date[5:7])}월" for date in dates})
    return [*years, *months]


def _fill(
    template: str,
    rng: random.Random,
    themes: Sequence[str],
    stocks: Sequence[str],
    dates: Sequence[str],
    catalysts: Sequence[tuple[str, str]],
    periods: Sequence[str],
) -> tuple[str, dict[str, str]]:
    slots: dict[str, str] = {}
    text = template
    if "{date}" in text or "{short}" in text:
        date = rng.choice(dates)
        slots["date"] = date
        text = text.replace("{date}", date)
        text = text.replace("{short}", f"{int(date[5:7])}/{int(date[8:])}")
    if "{start}" in text:
        index = rng.randrange(0, max(1, len(dates) - 5))
        start, end = dates[index], dates[min(index + 4, len(dates) - 1)]
        slots["dateRange"] = f"{start}~{end}"
        text = text.replace("{start}", start).replace("{end}", end)
    if "{stock}" in text:
        stock = rng.choice(stocks)
        slots["stock"] = stock
        text = text.replace("{stock}", stock)
    if "{theme2}" in text:
        theme2 = rng.choice(themes)
        slots["theme2"] = theme2
        text = text.replace("{theme2}", theme2)
    if "{theme}" in text:
        theme = rng.choice(themes)
        slots["theme"] = theme
        text = text.replace("{theme}", theme)
    if "{catalyst}" in text:
        type_id, name_ko = rng.choice(catalysts)
        slots["catalystType"] = type_id
        text = text.replace("{catalyst}", name_ko)
    if "{year}" in text:
        period = rng.choice(periods)
        slots["period"] = period
        text = text.replace("{year}", period)
    return text, slots


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    themes, stocks, dates = _slot_values(arguments.collection, arguments.daily_lists)
    if not (themes and stocks and dates):
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": "테마·종목·발행일 중 하나를 수집본에서 읽지 못했습니다.",
                },
                ensure_ascii=False,
            )
        )
        return 2
    catalysts = [(item.type_id, item.name_ko) for item in VOCABULARY]
    periods = _periods(dates)
    rng = random.Random(SAMPLE_SEED)

    rows: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for group, query_id, direction, templates in GROUPS:
        wanted = TEST_PER_GROUP + DEV_PER_GROUP
        made = 0
        guard = 0
        while made < wanted and guard < wanted * 60:
            guard += 1
            template = templates[made % len(templates)]
            question, slots = _fill(template, rng, themes, stocks, dates, catalysts, periods)
            if question in seen:
                continue
            seen.add(question)
            if direction is not None:
                slots["direction"] = direction
            rows.append(
                (
                    f"{group}-{made:03d}",
                    group,
                    question,
                    query_id,
                    json.dumps(slots, ensure_ascii=False, sort_keys=True),
                    "AI_DRAFT",
                )
            )
            made += 1
        if made < wanted:
            print(
                json.dumps(
                    {
                        "status": "FAILED",
                        "messageKo": f"{group} 표본을 {wanted}건 채우지 못했습니다.",
                    },
                    ensure_ascii=False,
                )
            )
            return 2

    for index, (template, reason) in enumerate(OUT_OF_SCOPE * 5):
        question, _ = _fill(template, rng, themes, stocks, dates, catalysts, periods)
        if question in seen:
            continue
        seen.add(question)
        rows.append(
            (
                f"REJECT-{index:03d}",
                "REJECT",
                question,
                reason,
                "{}",
                "AI_DRAFT",
            )
        )
    for index, (question, query_id, slots) in enumerate(HARD_SLOTS):
        rows.append(
            (
                f"HARD-{index:03d}",
                "HARD_SLOT",
                question,
                query_id,
                json.dumps(slots, ensure_ascii=False, sort_keys=True),
                "AI_DRAFT",
            )
        )

    lines = [
        "# 질문 해석 gold set 초안 — 문장이 어느 질의 유형이고 슬롯이 무엇인지.",
        f"# seed {SAMPLE_SEED}. 표본군 {len(GROUPS)}개 × (test {TEST_PER_GROUP} + dev {DEV_PER_GROUP}).",
        "# REJECT군은 17종 밖 문장이며 gold_query_id가 거절 사유다.",
        "# HARD_SLOT군은 상대 날짜·과거 사명처럼 슬롯 해석이 어려운 회귀 케이스다.",
        "# 문장은 스크립트가 만든 초안이라 실제 사용자 말투와 다르다(계획서 11.1.3).",
        "# review_status가 AI_DRAFT인 동안은 승격 판정에 쓰지 않는다(계획서 11.1.2).",
        "# 열: question_id, sample_group, question, gold_query_id, gold_slots, review_status",
    ]
    lines.extend("\t".join(row) for row in rows)
    arguments.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "outPath": str(arguments.out),
                "rows": len(rows),
                "sampleGroups": len(GROUPS),
                "perGroup": TEST_PER_GROUP + DEV_PER_GROUP,
                "rejectRows": sum(1 for row in rows if row[1] == "REJECT"),
                "hardSlotRows": sum(1 for row in rows if row[1] == "HARD_SLOT"),
                "reviewStatus": "AI_DRAFT",
                "slotPool": {
                    "themes": len(themes),
                    "stocks": len(stocks),
                    "dates": len(dates),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
