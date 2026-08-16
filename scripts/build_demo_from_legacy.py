#!/usr/bin/env python3
"""구 DB에서 화면 시연용 데이터를 만든다.

현재 파이프라인은 `core.reference_daily_prices`에 상장주식수를 필수로 요구해서 구 DB 종가를
그대로 못 넣는다(PD-001의 유동시총 가중 분모). 그래서 백엔드를 통과시키는 대신, 화면이 읽는
모양으로 바로 만들어 둔다. 기준정보가 오면 이 파일은 버리고 실제 파이프라인을 쓰면 된다.

수익률은 **동일가중**이다. 정본 지표(상한형 유동시총 가중)와 다르므로 `weightMethod`를 그대로
두지 않고 품질 플래그로 드러낸다. 지어낸 값은 없고 전부 구 DB에서 계산했다.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

CURRENT = "2026-08-10"
PREVIOUS = "2026-08-07"
TOP_N = 10
HORIZONS = (1, 5, 20)
# `engine.event_outcomes`는 사건 하나에 버전 두 벌을 갖고 있다.
#  - leader_ew_v1 : 당시 기록된 주도주 동일가중 (16,575건)
#  - stocks_ew_v1 : 주도주 표기가 없을 때의 전 종목 폴백 (39,530건)
# 그냥 join하면 같은 사건이 두 번 나오고 수익률도 서로 달라진다. 정본 바스켓 정의
# (screen_spec 10.5 `사건 당시 인포스탁이 기록한 주도주를 동일가중한다`)에 따라
# 주도주 버전을 먼저 쓰고 없을 때만 폴백을 쓴다.
OUTCOME = """
        left join lateral (
          select e.ret_t1, e.ret_t5, e.ret_t20
          from engine.event_outcomes e
          where e.occurrence_id = o.occurrence_id
          order by (e.outcome_version = 'leader_ew_v1') desc, e.outcome_version
          limit 1
        ) e on true
"""


def _pct(value: Decimal | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _leaders(
    cur,
    raw: str | None,
    day,
    *,
    limit: int | None = None,
    keep_missing: bool = False,
) -> list[dict[str, object]]:
    """사건 당시 주도주와 그날 등락률(T-1 종가 대비 T0 종가).

    `keep_missing=False`(오늘 화면): 가격을 못 찾은 종목은 목록에서 뺀다. `0.0%`로 적으면
    보합과 구분되지 않는다 (screen_spec 8.9 `계산 불가는 —, 0 표시 금지`).

    `keep_missing=True`(과거 사건 상세): 못 찾은 종목도 `return: null`로 남긴다.
    screen_spec 10.6이 `바스켓에서 제외된 종목을 조용히 숨기지 않음`을 요구한다.
    """
    rows: list[dict[str, object]] = []
    for chunk in (raw or "").split("|"):
        code, _, name = chunk.strip().partition("-")
        code, name = code.strip(), name.strip()
        if not code or not name:
            continue
        # `prices_daily`에는 같은 (종목, 날짜)가 두 번 들어 있는 행이 50만 개 있다(수집 원천 2벌).
        # 그냥 최근 2행을 뽑으면 같은 날이 두 번 잡혀 등락률이 0%로 나온다. 날짜당 한 행만 쓴다.
        cur.execute(
            """
            select distinct on (p.session_date) p.adjusted_close
            from market.prices_daily p
            where p.security_id = (
                select m.security_id from theme.occurrence_members m
                where m.source_security_code = %s limit 1)
              and p.session_date <= %s
            order by p.session_date desc, p.adjusted_close
            limit 2
            """,
            (code, day),
        )
        closes = [r[0] for r in cur.fetchall()]
        usable = len(closes) >= 2 and bool(closes[1])
        if not usable and not keep_missing:
            continue
        rows.append(
            {
                "stockId": f"stk_{code}",
                "symbol": code,
                "name": name,
                "return": _pct(closes[0] / closes[1] - 1) if usable else None,
                "role": "LEADER",
            }
        )
        if limit is not None and len(rows) >= limit:
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="구 DB로 화면 시연 데이터를 만듭니다.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    with psycopg.connect(args.source) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            with px as (
              select security_id,
                     max(adjusted_close) filter (where session_date = %s) c,
                     max(adjusted_close) filter (where session_date = %s) p0
              from market.prices_daily where session_date in (%s, %s) group by 1),
            mem as (
              select h.theme_code, m.security_id, s.display_name
              from theme.membership_snapshot_headers h
              join theme.membership_snapshot_members m on m.snapshot_id = h.snapshot_id
              join market.securities s on s.security_id = m.security_id)
            select t.theme_code, t.name,
                   count(*) filter (where px.c is not null and px.p0 > 0) as valid,
                   count(*) filter (where px.c > px.p0) as up,
                   avg(px.c / px.p0 - 1) filter (where px.c is not null and px.p0 > 0) as ret
            from mem join theme.themes t on t.theme_code = mem.theme_code
            left join px on px.security_id = mem.security_id
            group by t.theme_code, t.name
            having count(*) filter (where px.c is not null and px.p0 > 0) >= 5
            order by ret desc nulls last limit %s
            """,
            (CURRENT, PREVIOUS, CURRENT, PREVIOUS, TOP_N),
        )
        ranked = cur.fetchall()

        themes: list[dict[str, object]] = []
        for rank, (theme_code, name, valid, up, ret) in enumerate(ranked, start=1):
            # 그날 그 테마의 사건 문장과 주도주
            cur.execute(
                """
                select occurrence_id, content, lead_stock_raw
                from theme.occurrences
                where theme_code = %s and session_date <= %s
                order by session_date desc limit 1
                """,
                (theme_code, CURRENT),
            )
            row = cur.fetchone()
            today_occ, content, lead_raw = (row or (None, None, None))

            cur.execute(
                """
                select k.display_name
                from keyword.occurrence_keyword_links l
                join keyword.keywords k on k.keyword_id = l.keyword_id
                where l.occurrence_id = %s
                """,
                (today_occ,),
            )
            today_keywords = {r[0] for r in cur.fetchall()}

            # 오늘의 주도 종목. 가격을 못 찾은 종목은 0%로 적지 않고 뺀다.
            leaders = [
                {k: v for k, v in row.items() if k != "role"}
                for row in _leaders(cur, lead_raw, CURRENT, limit=5)
            ]

            # 과거 유사사례: 같은 테마의 지난 사건 + 이미 계산된 T+N 결과
            cur.execute(
                """
                select o.occurrence_id, o.session_date, o.content, o.lead_stock_raw,
                       e.ret_t1, e.ret_t5, e.ret_t20
                from theme.occurrences o"""
                + OUTCOME
                + """
                where o.theme_code = %s and o.session_date < %s
                order by o.session_date desc limit 6
                """,
                (theme_code, CURRENT),
            )
            similar = []
            lead_cur = conn.cursor()
            for occ_id, day, text, lead, r1, r5, r20 in list(cur.fetchall()):
                tag_cur = conn.cursor()
                tag_cur.execute(
                    """
                    select k.display_name
                    from keyword.occurrence_keyword_links l
                    join keyword.keywords k on k.keyword_id = l.keyword_id
                    where l.occurrence_id = %s order by k.display_name limit 3
                    """,
                    (occ_id,),
                )
                tags = [r[0] for r in tag_cur.fetchall() if r[0]]
                tag_cur.close()
                outcomes = []
                for horizon, value in zip(HORIZONS, (r1, r5, r20), strict=True):
                    outcomes.append(
                        {
                            "horizonTradingDays": horizon,
                            "return": _pct(value),
                            "status": "OBSERVED" if value is not None else "PENDING",
                            "unavailableReason": None,
                        }
                    )
                similar.append(
                    {
                        "matchedEventId": f"evt_{occ_id}",
                        "marketDate": day.isoformat(),
                        "displayNameAtEvent": name,
                        "normalizedCatalystSummary": (text or "").split("(주도주")[0].strip()[:60],
                        "similarityReasons": tags,
                        "outcomes": outcomes,
                        "leaders": _leaders(lead_cur, lead, day, keep_missing=True),
                    }
                )
            lead_cur.close()

            # 소재 유형: 온톨로지 라벨이 아직 없어 그 테마 과거 사건에 붙은 키워드로 대신한다.
            cur.execute(
                """
                select k.display_name, count(distinct o.occurrence_id) as eligible,
                       count(e.ret_t1) as observed_1,
                       count(*) filter (where e.ret_t1 > 0) as up_1,
                       percentile_cont(0.5) within group (order by e.ret_t1) as med_1,
                       count(e.ret_t5) as observed_5,
                       count(*) filter (where e.ret_t5 > 0) as up_5,
                       percentile_cont(0.5) within group (order by e.ret_t5) as med_5,
                       count(e.ret_t20) as observed_20,
                       count(*) filter (where e.ret_t20 > 0) as up_20,
                       percentile_cont(0.5) within group (order by e.ret_t20) as med_20
                from theme.occurrences o
                join keyword.occurrence_keyword_links l on l.occurrence_id = o.occurrence_id
                join keyword.keywords k on k.keyword_id = l.keyword_id"""
                + OUTCOME
                + """
                where o.theme_code = %s and o.session_date < %s
                group by k.display_name
                order by count(distinct o.occurrence_id) desc, k.display_name limit 3
                """,
                (theme_code, CURRENT),
            )
            catalysts = []
            for (
                kw, eligible,
                ob1, up1, med1,
                ob5, up5, med5,
                ob20, up20, med20,
            ) in list(cur.fetchall()):
                ev_cur = conn.cursor()
                ev_cur.execute(
                    """
                    select o.occurrence_id, o.session_date, o.content, o.lead_stock_raw,
                           e.ret_t1, e.ret_t5, e.ret_t20
                    from theme.occurrences o
                    join keyword.occurrence_keyword_links l on l.occurrence_id = o.occurrence_id
                    join keyword.keywords k on k.keyword_id = l.keyword_id"""
                    + OUTCOME
                    + """
                    where o.theme_code = %s and o.session_date < %s and k.display_name = %s
                    order by o.session_date desc limit 8
                    """,
                    (theme_code, CURRENT, kw),
                )
                px_cur = conn.cursor()
                events = [
                    {
                        "matchedEventId": f"evt_{oid}",
                        "marketDate": d.isoformat(),
                        "displayNameAtEvent": name,
                        "normalizedCatalystSummary": (c or "").split("(주도주")[0].strip()[:60],
                        "sameDayReturn": _pct(r1),
                        "leaderName": (lead or "").split("|")[0].strip().partition("-")[2] or None,
                        "similarityReasons": [kw],
                        "outcomes": [
                            {
                                "horizonTradingDays": h,
                                "return": _pct(v),
                                "status": "OBSERVED" if v is not None else "PENDING",
                                "unavailableReason": None,
                            }
                            for h, v in zip(HORIZONS, (r1, r5, r20), strict=True)
                        ],
                        "leaders": _leaders(px_cur, lead, d, keep_missing=True),
                    }
                    for oid, d, c, lead, r1, r5, r20 in ev_cur.fetchall()
                ]
                px_cur.close()
                ev_cur.close()
                catalysts.append(
                    {
                        "catalystId": f"ctl_{theme_code}_{kw}",
                        "catalystName": kw,
                        "matchesToday": kw in today_keywords,
                        "horizons": [
                            {
                                "horizonTradingDays": h,
                                "eligibleCount": int(eligible),
                                "observedCount": int(o),
                                "positiveCount": int(u),
                                "medianReturn": _pct(m),
                            }
                            for h, o, u, m in (
                                (1, ob1, up1, med1),
                                (5, ob5, up5, med5),
                                (20, ob20, up20, med20),
                            )
                        ],
                        "events": events,
                    }
                )

            cur.execute(
                """
                select count(distinct session_date)
                from market.prices_daily
                where session_date > (
                    select max(session_date) from theme.occurrences
                    where theme_code = %s and session_date < %s)
                  and session_date <= %s
                """,
                (theme_code, CURRENT, CURRENT),
            )
            gap_row = cur.fetchone()
            attention_gap = int(gap_row[0]) if gap_row and gap_row[0] else None

            themes.append(
                {
                    "rank": rank,
                    "attentionGapTradingDays": attention_gap,
                    "themeId": f"thm_{theme_code}",
                    "eventId": f"evt_day_{theme_code}",
                    "displayName": name,
                    "weightedReturn": _pct(ret),
                    "advancingCount": up,
                    "validCount": valid,
                    "reason": (content or "").split("(주도주")[0].strip(),
                    "leaders": leaders,
                    "catalysts": catalysts,
                    "similar": similar,
                    "evidence": (
                        [
                            {
                                "newsId": f"news_{theme_code}",
                                "sourceName": "인포스탁 테마 기록",
                                "title": (content or "").split("(주도주")[0].strip(),
                                "summary": (content or "").strip(),
                            }
                        ]
                        if content
                        else []
                    ),
                }
            )

    payload = {
        "generatedAt": generated_at,
        "marketDate": CURRENT,
        "previousDate": PREVIOUS,
        "weightMethod": "EQUAL_WEIGHT_LEGACY",
        "note": "구 DB 종가로 계산한 동일가중 수익률. 정본의 상한형 유동시총 가중이 아니다.",
        "themes": themes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"테마 {len(themes)} → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
