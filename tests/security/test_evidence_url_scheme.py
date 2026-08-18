"""F-23 수리: 근거 기사 URL의 scheme을 수집 경계에서 http(s)로 제한한다.

공급원 RSS `<link>`가 그대로 `originalUrl`이 되어 웹의 동적 `href`
(`apps/web/src/pages/ThemeDetailPage.tsx:134`)로 흘러가므로, 저장되기 전에
막는다. 오염된 항목 하나가 배치 전체를 죽이지 않도록 예외가 아니라 거부
사유로 처리한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.news.ingestion import NewsIngestor
from packages.news.models import NewsSourceType, RawNewsItem, RejectionReason
from packages.news.normalize import canonical_url
from packages.news.store import InMemoryNewsStore

NOW = datetime(2026, 8, 14, 5, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)

# netloc이 있어서 "절대 주소" 검사만으로는 통과하던 payload.
# 브라우저에서 `//host/`는 주석 줄이고 `%0a`가 줄바꿈이라 뒤가 실행된다.
SCRIPT_URL = "javascript://news.test/%0aalert(document.cookie)"


@pytest.mark.parametrize(
    "url",
    [
        SCRIPT_URL,
        "data://news.test/text,payload",
        "vbscript://news.test/x",
        "javascript:alert(1)",
        "/news/123",
        "news.test/a",
    ],
)
def test_canonical_url_rejects_everything_but_http(url: str) -> None:
    with pytest.raises(ValueError):
        canonical_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://news.test/a/", "https://www.news.test/a?utm_source=x"],
)
def test_canonical_url_still_accepts_http_and_https(url: str) -> None:
    assert canonical_url(url).startswith("https://news.test/a")


def test_ingestion_rejects_script_url_instead_of_storing_it() -> None:
    """오염된 항목은 거부 사유로 남고, 같은 배치의 정상 항목은 저장된다."""

    store = InMemoryNewsStore()
    ingestor = NewsIngestor(
        store,
        stock_directory={"삼성전자": "KRX:005930"},
    )

    def item(source_item_id: str, url: str) -> RawNewsItem:
        return RawNewsItem(
            source_id="rss-poisoned",
            source_type=NewsSourceType.RSS,
            source_item_id=source_item_id,
            publisher="오염된 공급원",
            title="[특징주] 삼성전자 강세",
            description="삼성전자가 상승했다.",
            original_url=url,
            published_at=NOW,
            retrieved_at=NOW,
        )

    report = ingestor.ingest(
        [item("item-1", SCRIPT_URL), item("item-2", "https://news.test/a")],
        now=NOW,
        window_start=WINDOW_START,
    )

    assert [(rejected.source_item_id, rejected.reason) for rejected in report.rejected] == [
        ("item-1", RejectionReason.INVALID_URL)
    ]
    assert len(report.stored) == 1
    assert report.stored[0].original_url == "https://news.test/a"
