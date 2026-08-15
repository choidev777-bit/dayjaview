"""F-23 finding: 근거 기사 URL의 scheme이 어디에서도 http(s)로 제한되지 않는다.

공급원 RSS `<link>`가 그대로 `originalUrl`이 되어 웹의 유일한 동적 `href`
(`apps/web/src/pages/ThemeDetailPage.tsx:134`)로 흘러간다. 아래 테스트는
파이썬 쪽 경로(수집 → 정규화 → 투영)가 `javascript:` payload를 통과시키는
현재 동작을 고정한다. 수리하면 실패한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.news.ingestion import NewsIngestor
from packages.news.models import NewsSourceType, RawNewsItem
from packages.news.normalize import canonical_url
from packages.news.store import InMemoryNewsStore

NOW = datetime(2026, 8, 14, 5, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)

# netloc이 있는 javascript:·data: URL은 절대 주소 검사를 통과한다.
# 브라우저에서 `//host/`는 주석 줄이고 `%0a`가 줄바꿈이라 뒤가 실행된다.
SCRIPT_URL = "javascript://news.test/%0aalert(document.cookie)"


@pytest.mark.parametrize(
    "url",
    [
        SCRIPT_URL,
        "data://news.test/text,payload",
    ],
)
def test_canonical_url_accepts_non_http_schemes(url: str) -> None:
    """절대 주소 검사만 있고 scheme 허용목록이 없다."""

    assert canonical_url(url) == url


def test_canonical_url_still_rejects_relative_and_schemeless_urls() -> None:
    """지금 막고 있는 것은 여기까지다 — 이 방어는 유지되어야 한다."""

    for rejected in ("javascript:alert(1)", "/news/123", "news.test/a"):
        with pytest.raises(ValueError):
            canonical_url(rejected)


def test_ingestion_stores_script_url_and_projects_it_unchanged() -> None:
    """공급원이 준 `javascript:` URL이 저장되고 `originalUrl`로 그대로 나간다."""

    store = InMemoryNewsStore()
    ingestor = NewsIngestor(
        store,
        stock_directory={"삼성전자": "KRX:005930"},
    )
    report = ingestor.ingest(
        [
            RawNewsItem(
                source_id="rss-poisoned",
                source_type=NewsSourceType.RSS,
                source_item_id="item-1",
                publisher="오염된 공급원",
                title="특징주, 삼성전자 강세",
                description="삼성전자가 상승했다.",
                original_url=SCRIPT_URL,
                published_at=NOW,
                retrieved_at=NOW,
            )
        ],
        now=NOW,
        window_start=WINDOW_START,
    )

    assert report.rejected == ()
    assert len(report.stored) == 1
    item = report.stored[0]

    # 저장되는 값은 canonical이 아니라 공급원이 준 원문 그대로다.
    assert item.original_url == SCRIPT_URL
    # 그리고 그대로 투영된다 — 웹이 이 값을 href에 넣는다.
    assert item.to_source_metadata()["originalUrl"] == SCRIPT_URL
