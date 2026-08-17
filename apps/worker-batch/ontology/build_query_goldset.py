#!/usr/bin/env python3
"""질문 해석 gold set 초안을 만든다 (단계 0, 겹 A).

문장 하나가 어느 질의 유형이고 슬롯이 무엇인지를 채점하기 위한 표본이다.
표본군은 22개다 — 상승·하락 대칭이 필요한 5종은 방향별로 나누고 나머지
12종은 하나씩이다. 군마다 test 30 + dev 15를 채우고, 실패·난이도 케이스
160문장을 따로 더한다(계획서 11.1.1).

**dev/test는 `split` 열로 표시한다.** 겹 C가 쓰는 짝/홀 행 규칙은 1:1만
표현할 수 있어 30:15에 쓸 수 없다. 실패·난이도 160문장은 회귀 고정용이라
전부 test다 — 보면서 규칙을 고치면 회귀로서 값어치가 없다.

슬롯 값은 지어내지 않고 수집본의 실제 테마·종목·발행일에서 뽑는다. 과거
사명은 KRX 종목명 이력 색인(`build_krx_name_windows.py` 산출)에서 실제로
바뀐 이름만 쓴다. 유사명 충돌도 실제 종목 목록에서 한 이름이 다른 이름의
접두사인 쌍을 찾아 만든다.

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
# 실패·난이도 케이스 160문장의 내역 (계획서 11.1.1). 전부 test split이다.
REJECT_ROWS = 60
HARD_SLOT_ROWS = 80
TODAY_ROWS = 20
TEST = "test"
DEV = "dev"

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

# 난이도 ①: 지금 시각을 알아야 풀리는 상대 날짜.
# (문장 틀, 질의 ID, 상대 표현이 들어갈 슬롯 키, 상대 표현 값, 방향)
RELATIVE_TEMPLATES: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("어제 뭐가 올랐어?", "DAY_MOVERS", "date", "RELATIVE:YESTERDAY", "UP"),
    ("어제 하락한 테마 알려줘", "DAY_MOVERS", "date", "RELATIVE:YESTERDAY", "DOWN"),
    ("그저께 뭐 올랐어", "DAY_MOVERS", "date", "RELATIVE:DAY_BEFORE_YESTERDAY", "UP"),
    ("지난 금요일 상승 테마", "DAY_MOVERS", "date", "RELATIVE:LAST_FRIDAY", "UP"),
    ("전 거래일 뭐가 셌어?", "DAY_MOVERS", "date", "RELATIVE:PREVIOUS_TRADING_DAY", "UP"),
    ("지난주 시장 어땠어?", "PERIOD_SUMMARY", "dateRange", "RELATIVE:LAST_WEEK", None),
    ("이번 주 상승 흐름 정리해줘", "PERIOD_SUMMARY", "dateRange", "RELATIVE:THIS_WEEK", "UP"),
    ("이번 달 상승 테마", "PERIOD_SUMMARY", "dateRange", "RELATIVE:THIS_MONTH", "UP"),
    ("지난달 약세 테마 알려줘", "PERIOD_SUMMARY", "dateRange", "RELATIVE:LAST_MONTH", "DOWN"),
    ("최근 일주일 하락장 정리", "PERIOD_SUMMARY", "dateRange", "RELATIVE:LAST_7_DAYS", "DOWN"),
    ("최근 3개월 자주 나온 테마", "THEME_FREQUENCY", "period", "RELATIVE:LAST_3_MONTHS", None),
    ("올해 많이 등장한 테마", "THEME_FREQUENCY", "period", "RELATIVE:THIS_YEAR", None),
    ("작년에 자주 부각된 테마", "THEME_FREQUENCY", "period", "RELATIVE:LAST_YEAR", None),
    ("{stock} 어제 왜 올랐어?", "STOCK_DAY_REASON", "date", "RELATIVE:YESTERDAY", "UP"),
    ("{stock} 어제 왜 빠졌어", "STOCK_DAY_REASON", "date", "RELATIVE:YESTERDAY", "DOWN"),
    ("{stock} 최근 한 달 크게 오른 날", "STOCK_TOP_MOVES", "period", "RELATIVE:LAST_MONTH", "UP"),
    ("{stock} 올해 급락일 정리해줘", "STOCK_TOP_MOVES", "period", "RELATIVE:THIS_YEAR", "DOWN"),
    ("최근 1년 {catalyst} 몇 번 나왔어?", "CATALYST_FREQUENCY", "period", "RELATIVE:LAST_12_MONTHS", None),
    ("올해 {catalyst} 건수 알려줘", "CATALYST_FREQUENCY", "period", "RELATIVE:THIS_YEAR", None),
    ("{stock} 최근 같이 움직인 종목", "STOCK_COOCCURRENCE", "period", "RELATIVE:RECENT", None),
)

# 난이도 ②: 한 문장에 슬롯이 셋 이상이라 하나만 잡으면 틀리는 문장.
MULTI_SLOT_TEMPLATES: tuple[tuple[str, str, str | None], ...] = (
    ("{stock} {year} {catalyst} 관련해서 오른 날 있어?", "STOCK_TOP_MOVES", "UP"),
    ("{theme}랑 {theme2} {year} 중 어디가 더 셌어?", "THEME_COMPARISON", "UP"),
    ("{year} {theme}에서 {catalyst} 몇 번 나왔어?", "CATALYST_FREQUENCY", None),
    ("{stock} {date} {catalyst} 때문에 올랐어?", "STOCK_DAY_REASON", "UP"),
    ("{theme} {catalyst} {year} 처음이야 다시 나온 거야?", "CATALYST_CONTINUATION", None),
    ("{stock}이랑 {year}에 같이 오른 종목 알려줘", "STOCK_COOCCURRENCE", "UP"),
    ("{stock} {year} 1조 넘는 수주 몇 건이야?", "COMPANY_VALUE_SUMMARY", None),
    ("{stock} {date} 발표 뒤 흐름 {year} 기준으로", "COMPANY_HISTORICAL_OUTCOME", None),
    ("{year} {catalyst}에 반응한 테마 중 {theme} 있었어?", "CATALYST_THEME_REACTION", None),
    ("{theme} {year} 하락 사유 정리해줘", "THEME_HISTORY", "DOWN"),
)

# 발행 전 시각에 오늘을 묻는 문장(계획서 4.0.1). 실시간 값으로 대체하지 않고
# 직전 거래일로 답하는지 보는 회귀 fixture다.
TODAY_TEMPLATES: tuple[tuple[str, str, str | None], ...] = (
    ("오늘 뭐가 올랐어?", "DAY_MOVERS", "UP"),
    ("오늘 뭐 빠졌어", "DAY_MOVERS", "DOWN"),
    ("오늘 상승 테마 알려줘", "DAY_MOVERS", "UP"),
    ("오늘 약세 테마 정리해줘", "DAY_MOVERS", "DOWN"),
    ("금일 강세 테마", "DAY_MOVERS", "UP"),
    ("오늘 특징테마 알려줘", "DAY_MOVERS", None),
    ("오늘장 어땠어?", "DAY_MOVERS", None),
    ("지금 뭐가 오르고 있어?", "DAY_MOVERS", "UP"),
    ("오늘 시장 요약해줘", "DAY_MOVERS", None),
    ("오늘 올라간 테마 순위", "DAY_MOVERS", "UP"),
    ("오늘 상한가 간 테마", "DAY_MOVERS", "UP"),
    ("{stock} 오늘 왜 올랐어?", "STOCK_DAY_REASON", "UP"),
    ("{stock} 오늘 왜 빠져", "STOCK_DAY_REASON", "DOWN"),
    ("{stock} 금일 상승 이유", "STOCK_DAY_REASON", "UP"),
    ("{stock} 오늘 하락 사유 알려줘", "STOCK_DAY_REASON", "DOWN"),
    ("{stock} 지금 왜 오르는 거야", "STOCK_DAY_REASON", "UP"),
    ("{stock} 오늘 상승 이유가 뭐야", "STOCK_DAY_REASON", "UP"),
    ("오늘까지 이번 주 시장 어땠어?", "PERIOD_SUMMARY", None),
    ("이번 주 오늘까지 상승 흐름", "PERIOD_SUMMARY", "UP"),
    ("오늘 포함해서 최근 하락 테마", "PERIOD_SUMMARY", "DOWN"),
)

# 난이도 ③·④의 문장 틀. 값은 실제 데이터에서 뽑는다.
# (문장 틀, 질의 ID, 방향)
PAST_NAME_TEMPLATES: tuple[tuple[str, str, str | None], ...] = (
    ("{old} 어떤 테마야?", "STOCK_THEME_MEMBERSHIP", None),
    ("{old} {date}에 왜 올랐어?", "STOCK_DAY_REASON", "UP"),
    ("{old} 크게 오른 날 언제야?", "STOCK_TOP_MOVES", "UP"),
    ("{old}이랑 같이 움직인 종목", "STOCK_COOCCURRENCE", None),
)
AMBIGUOUS_NAME_TEMPLATES: tuple[tuple[str, str, str | None], ...] = (
    ("{name} 어떤 테마에 속해?", "STOCK_THEME_MEMBERSHIP", None),
    ("{name} 크게 오른 날 알려줘", "STOCK_TOP_MOVES", "UP"),
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
        "--krx-names",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "krx_name_windows.json",
        help="과거 사명 표본의 원천. build_krx_name_windows.py 산출물이다.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "tests" / "ontology" / "query_goldset.tsv",
    )
    return parser


def _past_names(path: Path) -> list[tuple[str, str, str, str]]:
    """이름이 실제로 바뀐 종목만 (과거 이름, 현재 이름, 종목코드, 과거 창 끝).

    **다른 회사가 지금 쓰고 있는 이름은 뺀다.** 예를 들어 000070은 `삼양사`에서
    `삼양홀딩스`로 바뀌었지만 `삼양사`는 지금 145990의 이름이다. 그런 문장은
    과거 사명 문제가 아니라 동명이인 문제이고, 정답이 하나로 정해지지 않는다.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    windows: dict[str, list[dict[str, str]]] = {}
    for window in payload.get("windows") or []:
        code = str(window.get("stockCode") or "")
        if code and window.get("name"):
            windows.setdefault(code, []).append(window)
    for entries in windows.values():
        entries.sort(key=lambda item: str(item.get("firstDate") or ""))
    live_names = {
        str(entries[-1]["name"]): code for code, entries in windows.items() if entries
    }
    changed: list[tuple[str, str, str, str]] = []
    for code, entries in windows.items():
        names = [str(entry["name"]) for entry in entries]
        if len({*names}) < 2:
            continue
        current = names[-1]
        for entry, name in zip(entries[:-1], names[:-1], strict=True):
            if name == current or live_names.get(name, code) != code:
                continue
            changed.append((name, current, code, str(entry.get("lastDate") or "")))
    changed.sort()
    return changed


def _ambiguous_names(stocks: Sequence[str]) -> list[tuple[str, str]]:
    """한 이름이 다른 이름의 접두사인 쌍. 짧은 쪽만 부르면 후보가 갈린다."""

    ordered = sorted({name for name in stocks if len(name) >= 2})
    pairs: list[tuple[str, str]] = []
    for index, short in enumerate(ordered):
        for longer in ordered[index + 1 :]:
            if not longer.startswith(short):
                break
            if longer != short:
                pairs.append((short, longer))
    return pairs


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

    if not arguments.krx_names.is_file():
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": (
                        "과거 사명 표본의 원천이 없습니다. "
                        "build_krx_name_windows.py를 먼저 실행하세요: "
                        f"{arguments.krx_names}"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 2
    past_names = _past_names(arguments.krx_names)
    ambiguous = _ambiguous_names(stocks)
    if not past_names or not ambiguous:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": "과거 사명 또는 유사명 쌍을 하나도 찾지 못했습니다.",
                },
                ensure_ascii=False,
            )
        )
        return 2

    rows: list[tuple[str, ...]] = []
    seen: set[str] = set()

    def _add(
        question_id: str,
        group: str,
        question: str,
        query_id: str,
        slots: dict[str, str],
        split: str,
    ) -> bool:
        if question in seen:
            return False
        seen.add(question)
        rows.append(
            (
                question_id,
                group,
                question,
                query_id,
                json.dumps(slots, ensure_ascii=False, sort_keys=True),
                split,
                "AI_DRAFT",
            )
        )
        return True

    for group, query_id, direction, templates in GROUPS:
        wanted = TEST_PER_GROUP + DEV_PER_GROUP
        made = 0
        guard = 0
        while made < wanted and guard < wanted * 60:
            guard += 1
            template = templates[made % len(templates)]
            question, slots = _fill(template, rng, themes, stocks, dates, catalysts, periods)
            if direction is not None:
                slots["direction"] = direction
            split = TEST if made < TEST_PER_GROUP else DEV
            if _add(f"{group}-{made:03d}", group, question, query_id, slots, split):
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

    # 17종 밖 문장 60건. 정적 문장이 섞여 있어 틀 색인은 guard로 돌린다 —
    # made로 돌리면 중복된 정적 문장에서 제자리걸음한다.
    made = 0
    guard = 0
    while made < REJECT_ROWS and guard < REJECT_ROWS * 60:
        template, reason = OUT_OF_SCOPE[guard % len(OUT_OF_SCOPE)]
        guard += 1
        question, _ = _fill(template, rng, themes, stocks, dates, catalysts, periods)
        if _add(f"REJECT-{made:03d}", "REJECT", question, reason, {}, TEST):
            made += 1
    if made < REJECT_ROWS:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": f"17종 밖 표본을 {REJECT_ROWS}건 채우지 못했습니다.",
                },
                ensure_ascii=False,
            )
        )
        return 2

    hard = 0
    # ① 상대 날짜 20건.
    for template, query_id, slot_key, slot_value, direction in RELATIVE_TEMPLATES:
        question, slots = _fill(template, rng, themes, stocks, dates, catalysts, periods)
        slots[slot_key] = slot_value
        if direction is not None:
            slots["direction"] = direction
        if _add(f"HARD-{hard:03d}", "HARD_SLOT", question, query_id, slots, TEST):
            hard += 1

    # ② 복수 슬롯 30건.
    guard = 0
    target = hard + 30
    while hard < target and guard < 30 * 60:
        template, query_id, direction = MULTI_SLOT_TEMPLATES[guard % len(MULTI_SLOT_TEMPLATES)]
        guard += 1
        question, slots = _fill(template, rng, themes, stocks, dates, catalysts, periods)
        if direction is not None:
            slots["direction"] = direction
        if _add(f"HARD-{hard:03d}", "HARD_SLOT", question, query_id, slots, TEST):
            hard += 1

    # ③ 과거 사명 20건. 이름이 유효했던 창 안의 날짜만 쓴다.
    guard = 0
    target = hard + 20
    while hard < target and guard < 20 * 60:
        template, query_id, direction = PAST_NAME_TEMPLATES[guard % len(PAST_NAME_TEMPLATES)]
        old, current, code, last_date = past_names[rng.randrange(len(past_names))]
        guard += 1
        slots = {"stock": f"ALIAS:{old}", "resolvedStock": current, "stockCode": code}
        question = template.replace("{old}", old)
        if "{date}" in question:
            within = [value for value in dates if value <= last_date]
            if not within:
                continue
            date = within[rng.randrange(len(within))]
            slots["date"] = date
            question = question.replace("{date}", date)
        if direction is not None:
            slots["direction"] = direction
        if _add(f"HARD-{hard:03d}", "HARD_SLOT", question, query_id, slots, TEST):
            hard += 1

    # ④ 유사명 충돌 10건. 짧은 쪽만 부르면 후보가 둘 이상이다.
    guard = 0
    target = hard + 10
    while hard < target and guard < 10 * 60:
        template, query_id, direction = AMBIGUOUS_NAME_TEMPLATES[
            guard % len(AMBIGUOUS_NAME_TEMPLATES)
        ]
        short, longer = ambiguous[rng.randrange(len(ambiguous))]
        guard += 1
        slots = {"stock": f"AMBIGUOUS:{short}", "candidates": f"{short}|{longer}"}
        if direction is not None:
            slots["direction"] = direction
        question = template.replace("{name}", short)
        if _add(f"HARD-{hard:03d}", "HARD_SLOT", question, query_id, slots, TEST):
            hard += 1

    if hard < HARD_SLOT_ROWS:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": f"난이도 표본을 {HARD_SLOT_ROWS}건 채우지 못했습니다({hard}건).",
                },
                ensure_ascii=False,
            )
        )
        return 2

    # 발행 전 "오늘" 20건.
    today = 0
    for template, query_id, direction in TODAY_TEMPLATES:
        question, slots = _fill(template, rng, themes, stocks, dates, catalysts, periods)
        slots["date"] = "RELATIVE:TODAY"
        slots["publicationState"] = "BEFORE_PUBLISH"
        if direction is not None:
            slots["direction"] = direction
        if _add(f"TODAY-{today:03d}", "TODAY_BEFORE_PUBLISH", question, query_id, slots, TEST):
            today += 1
    if today < TODAY_ROWS:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": f'발행 전 "오늘" 표본을 {TODAY_ROWS}건 채우지 못했습니다({today}건).',
                },
                ensure_ascii=False,
            )
        )
        return 2

    lines = [
        "# 질문 해석 gold set 초안 — 문장이 어느 질의 유형이고 슬롯이 무엇인지.",
        f"# seed {SAMPLE_SEED}. 표본군 {len(GROUPS)}개 × (test {TEST_PER_GROUP} + dev {DEV_PER_GROUP}).",
        "# REJECT군은 17종 밖 문장이며 gold_query_id가 거절 사유다.",
        "# HARD_SLOT군은 상대 날짜·복수 슬롯·과거 사명·유사명 충돌 회귀 케이스다.",
        '# TODAY_BEFORE_PUBLISH군은 발행 전 "오늘" 질문이다. 직전 거래일로 답하고',
        "#   실시간 값을 섞지 않는지 본다(계획서 4.0.1).",
        "# split=dev는 규칙 개선용, test는 측정 전용이다. 실패·난이도 160문장은",
        "#   회귀 고정용이라 전부 test다(계획서 11.1.1).",
        "# 문장은 스크립트가 만든 초안이라 실제 사용자 말투와 다르다(계획서 11.1.3).",
        "# review_status가 AI_DRAFT인 동안은 승격 판정에 쓰지 않는다(계획서 11.1.2).",
        "# 열: question_id, sample_group, question, gold_query_id, gold_slots, split, review_status",
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
                "todayRows": sum(1 for row in rows if row[1] == "TODAY_BEFORE_PUBLISH"),
                "splitCounts": {
                    "test": sum(1 for row in rows if row[5] == TEST),
                    "dev": sum(1 for row in rows if row[5] == DEV),
                },
                "reviewStatus": "AI_DRAFT",
                "slotPool": {
                    "themes": len(themes),
                    "stocks": len(stocks),
                    "dates": len(dates),
                    "pastNames": len(past_names),
                    "ambiguousPairs": len(ambiguous),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
