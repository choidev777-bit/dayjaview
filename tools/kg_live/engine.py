"""시연용 조립: 운영과 같은 리서치 엔진을 로컬 프로세스에서 그대로 돌린다.

DB는 `prod_db_link.sh tunnel`이 열어 둔 127.0.0.1:5433(운영 Postgres 읽기 전용),
가격은 로컬 `research/data/daily_prices.sqlite`를 쓴다. 해석·계산·문장은 전부
`packages.ontology`의 운영 코드가 한다. 이 파일에는 답을 만드는 로직이 없다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from apps.api.research import ResearchBoundary
from packages.ontology.outcomes import SqliteOutcomeReader
from packages.ontology.query_answers import QueryAvailability
from packages.ontology.query_contracts import QueryType
from packages.ontology.query_planning import plan_question
from packages.ontology.research_postgres import (
    PostgresResearchRepository,
    load_question_catalog,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))
DEFAULT_PRICE_CORPUS = REPOSITORY_ROOT / "research" / "data" / "daily_prices.sqlite"


def read_env_local() -> dict[str, str]:
    """`.env.local`의 값을 읽는다. 값은 반환만 하고 출력하지 않는다."""

    values: dict[str, str] = {}
    path = REPOSITORY_ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            values[name.strip()] = value.strip()
    values.update({key: value for key, value in os.environ.items() if key in values})
    return values


@dataclass(frozen=True, slots=True)
class LiveEngine:
    boundary: ResearchBoundary
    catalog: Any
    availability: QueryAvailability
    llm_enabled: bool

    def today(self) -> date:
        return datetime.now(tz=KST).date()

    def plan(self, question: str) -> Any:
        """운영과 같은 해석기. 답을 만들기 전의 조회 계획을 그대로 준다."""

        return plan_question(question, catalog=self.catalog, today=self.today())

    def answer(self, question: str) -> Mapping[str, Any]:
        return self.boundary.answer(question, today=self.today())


def build_engine(environment: Mapping[str, str] | None = None) -> LiveEngine:
    values = dict(environment or read_env_local())
    dsn = values.get("KG_LIVE_DATABASE_DSN", "").strip()
    if not dsn:
        raise SystemExit(
            ".env.local에 KG_LIVE_DATABASE_DSN이 없습니다. "
            "먼저 `bash tools/kg_live/prod_db_link.sh dsn`을 실행하세요."
        )

    import psycopg

    connection = psycopg.connect(dsn, connect_timeout=15)
    price_path = values.get("KG_LIVE_PRICE_CORPUS_PATH", "").strip() or str(
        DEFAULT_PRICE_CORPUS
    )
    price_reader = SqliteOutcomeReader(price_path)
    verified = frozenset(
        QueryType(name.strip())
        for name in values.get(
            "RESEARCH_VERIFIED_QUERY_TYPES",
            ",".join(query_type.value for query_type in QueryType),
        ).split(",")
        if name.strip()
    )
    availability = QueryAvailability(
        human_verified=verified,
        serve_unverified=values.get("RESEARCH_SERVE_UNVERIFIED", "1").strip() == "1",
        outcome_gate_open=True,
        outcome_range_from=price_reader.price_range_from(),
    )
    llm = None
    if values.get("OPENAI_API_KEY", "").strip():
        from packages.llm import create_live_llm_client

        llm = create_live_llm_client(values)
    catalog = load_question_catalog(cast(Any, connection))
    boundary = ResearchBoundary(
        catalog=catalog,
        repository=PostgresResearchRepository(
            cast(Any, connection), price_reader=price_reader
        ),
        availability=availability,
        llm=llm,
    )
    return LiveEngine(
        boundary=boundary,
        catalog=catalog,
        availability=availability,
        llm_enabled=llm is not None,
    )


if __name__ == "__main__":
    import json
    import sys

    question = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "과거 로봇 산업 육성 정책이 발표됐을 때 어떤 테마가 반응했고, 당시 주도주는 5거래일 뒤 어떻게 움직였어?"
    )
    engine = build_engine()
    print(json.dumps(engine.answer(question), ensure_ascii=False, indent=2))
