from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone

from packages.catalyst import ThemeContext
from packages.news import NewsSourceType, RawNewsItem, RightsScope, SourceCursor

KST = timezone(timedelta(hours=9))
MARKET_DATE = date(2026, 8, 14)
WINDOW_START = datetime(2026, 8, 13, 15, 30, tzinfo=KST)
STOCK_DIRECTORY: Mapping[str, str] = {
    "한국원전": "stk_nuclear_leader",
    "원전기자재": "stk_nuclear_related",
    "바이오헬스": "stk_bio",
}
ENTITY_VOCABULARY = ("원전", "수주", "기술수출")


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 8, 14, hour, minute, second, tzinfo=KST)


def raw(
    *,
    source_id: str = "rss_example",
    source_type: NewsSourceType = NewsSourceType.RSS,
    source_item_id: str = "item-1",
    publisher: str = "예시 언론사",
    title: str = "[특징주] 한국원전, 신규 원전 수주 기대에 강세",
    description: str = "체코 신규 원전 관련 보도가 나왔다.",
    original_url: str = "https://example.com/news/123",
    published_at: datetime | None = None,
    retrieved_at: datetime | None = None,
    rights_scope: RightsScope = RightsScope.SUMMARY_ALLOWED,
    body: str = "",
) -> RawNewsItem:
    published = published_at if published_at is not None else at(10, 8)
    return RawNewsItem(
        source_id=source_id,
        source_type=source_type,
        source_item_id=source_item_id,
        publisher=publisher,
        title=title,
        description=description,
        original_url=original_url,
        published_at=published,
        retrieved_at=retrieved_at if retrieved_at is not None else at(10, 9),
        rights_scope=rights_scope,
        body=body,
    )


def nuclear_context(
    *,
    activated_at: datetime | None = None,
    known_catalyst_keys: frozenset[str] = frozenset(),
) -> ThemeContext:
    return ThemeContext(
        event_id="evt_nuclear",
        theme_id="thm_nuclear",
        display_name="원전",
        market_date=MARKET_DATE,
        activated_at=activated_at if activated_at is not None else at(10, 0),
        synonyms=("원자력",),
        leader_names=("한국원전",),
        leader_stock_ids=("stk_nuclear_leader",),
        related_stock_ids=("stk_nuclear_related",),
        entities=("원전",),
        known_catalyst_keys=known_catalyst_keys,
    )


def bio_context() -> ThemeContext:
    return ThemeContext(
        event_id="evt_bio",
        theme_id="thm_bio",
        display_name="비만치료제",
        market_date=MARKET_DATE,
        activated_at=at(10, 0),
        leader_names=("바이오헬스",),
        leader_stock_ids=("stk_bio",),
    )


class StubLlmClient:
    """정해진 응답만 돌려주는 결정적 client. 네트워크를 쓰지 않는다."""

    model_name = "stub-grounding-model"

    def __init__(self, responses: Sequence[Mapping[str, object]] | None = None, *, error: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[Mapping[str, object]] = []

    def structure(
        self,
        *,
        prompt_version: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("준비된 응답보다 많이 호출되었습니다")
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def grounded_response(
    *,
    stock_ids: Sequence[str] = ("stk_nuclear_leader",),
    theme_ids: Sequence[str] = ("thm_nuclear",),
    summary: str = "신규 원전 수주 기대 관련 보도",
    entities: Sequence[str] = ("원전",),
    confidence: float = 0.91,
    grounded: bool = True,
) -> dict[str, object]:
    return {
        "stockIds": list(stock_ids),
        "candidateThemeIds": list(theme_ids),
        "catalystSummary": summary,
        "eventEntities": list(entities),
        "confidence": confidence,
        "grounded": grounded,
    }


class StubSource:
    def __init__(
        self,
        source_id: str,
        batches: Sequence[Sequence[RawNewsItem]],
        *,
        source_type: NewsSourceType = NewsSourceType.RSS,
        error: Exception | None = None,
    ) -> None:
        self.source_id = source_id
        self.source_type = source_type
        self._batches = [tuple(batch) for batch in batches]
        self._error = error
        self.fetch_count = 0

    def fetch(self, cursor: SourceCursor) -> Sequence[RawNewsItem]:
        self.fetch_count += 1
        if self._error is not None:
            raise self._error
        if not self._batches:
            return ()
        return self._batches.pop(0)
