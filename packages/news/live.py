"""실공급원 어댑터: 언론사 RSS와 NAVER API HUB 뉴스 검색.

환경변수 설정:
- ``NEWS_RSS_SOURCES``: ``source_id|매체명|feed URL`` 항목을 ``;``로 구분.
  이용 조건을 확인한 언론사 RSS만 등록한다 (ADR-008).
- ``NAVER_API_HUB_CLIENT_ID`` / ``NAVER_API_HUB_CLIENT_SECRET``:
  NAVER API HUB 뉴스 검색 인증값. 없으면 해당 공급원은 조립되지 않는다.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from .models import NewsSourceType, RawNewsItem, RightsScope
from .sources import NewsSource, SourceCursor

NAVER_API_HUB_NEWS_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
FEATURED_QUERY = "특징주"
# 상류가 침해되거나 오작동해도 메모리를 고갈시키지 못하게 하는 상한.
MAX_FEED_BYTES = 32 * 1024 * 1024
FEATURED_POLL_DISPLAY = 30
SUPPLEMENTAL_DISPLAY = 10
SUPPLEMENTAL_MAX_TERMS = 3

_HTML_TAG = re.compile(r"<[^>]+>")

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(_HTML_TAG.sub("", value)).strip()


def _parse_rfc822(value: str | None) -> datetime | None:
    """RFC 822 발행 시각. timezone이 없으면 지어내지 않고 None."""

    if value is None or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_new(published_at: datetime | None, cursor: SourceCursor) -> bool:
    if published_at is None or cursor.last_published_at is None:
        return True
    return published_at > cursor.last_published_at


def _chronological(items: Sequence[RawNewsItem]) -> tuple[RawNewsItem, ...]:
    return tuple(
        sorted(items, key=lambda item: item.published_at or item.retrieved_at)
    )


@dataclass(frozen=True, slots=True)
class RssFeedConfig:
    """이용 조건을 확인한 언론사 RSS 하나."""

    source_id: str
    publisher: str
    feed_url: str
    rights_scope: RightsScope = RightsScope.SUMMARY_ALLOWED

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_id", self.source_id),
            ("publisher", self.publisher),
            ("feed_url", self.feed_url),
        ):
            if not value.strip():
                raise ValueError(f"RSS 설정의 {field_name}이 비어 있습니다")


class RssNewsSource:
    """RSS 2.0 feed를 polling해 새 항목만 돌려준다."""

    def __init__(
        self,
        config: RssFeedConfig,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
        clock: Clock = _utc_now,
    ) -> None:
        self.source_id = config.source_id
        self.source_type = NewsSourceType.RSS
        self._config = config
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._clock = clock

    def fetch(self, cursor: SourceCursor) -> Sequence[RawNewsItem]:
        with self._client.stream("GET", self._config.feed_url) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_FEED_BYTES:
                    raise ValueError(
                        f"RSS 응답 본문 상한 {MAX_FEED_BYTES} bytes를 넘었습니다"
                    )
        root = ElementTree.fromstring(bytes(body))
        retrieved_at = self._clock()
        items = [
            item
            for element in root.iterfind(".//item")
            if (item := self._to_raw(element, retrieved_at)) is not None
            and _is_new(item.published_at, cursor)
        ]
        return _chronological(items)

    def _to_raw(
        self, element: ElementTree.Element, retrieved_at: datetime
    ) -> RawNewsItem | None:
        title = _clean_text(element.findtext("title"))
        link = (element.findtext("link") or "").strip()
        if not title or not link:
            return None
        guid = (element.findtext("guid") or "").strip()
        return RawNewsItem(
            source_id=self.source_id,
            source_type=NewsSourceType.RSS,
            source_item_id=guid or link,
            publisher=self._config.publisher,
            title=title,
            description=_clean_text(element.findtext("description")),
            original_url=link,
            published_at=_parse_rfc822(element.findtext("pubDate")),
            retrieved_at=retrieved_at,
            rights_scope=self._config.rights_scope,
        )


class NaverApiHubClient:
    """NAVER API HUB 뉴스 검색 호출과 항목 변환."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
        clock: Clock = _utc_now,
    ) -> None:
        if not client_id.strip() or not client_secret.strip():
            raise ValueError("NAVER API HUB 인증값이 비어 있습니다")
        self._headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        }
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._clock = clock

    def search(
        self,
        query: str,
        *,
        source_id: str,
        source_type: NewsSourceType,
        display: int,
    ) -> tuple[RawNewsItem, ...]:
        response = self._client.get(
            NAVER_API_HUB_NEWS_URL,
            params={"query": query, "sort": "date", "display": display},
            headers=self._headers,
        )
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise ValueError("NAVER API HUB 응답에 items 목록이 없습니다")
        retrieved_at = self._clock()
        return tuple(
            item
            for entry in entries
            if isinstance(entry, dict)
            and (item := self._to_raw(entry, source_id, source_type, retrieved_at))
            is not None
        )

    @staticmethod
    def _to_raw(
        entry: Mapping[str, object],
        source_id: str,
        source_type: NewsSourceType,
        retrieved_at: datetime,
    ) -> RawNewsItem | None:
        original = str(entry.get("originallink") or "").strip() or str(
            entry.get("link") or ""
        ).strip()
        title = _clean_text(str(entry.get("title") or ""))
        if not original or not title:
            return None
        host = urlsplit(original).hostname or ""
        publisher = host[4:] if host.startswith("www.") else host
        if not publisher:
            return None
        return RawNewsItem(
            source_id=source_id,
            source_type=source_type,
            source_item_id=original,
            publisher=publisher,
            title=title,
            description=_clean_text(str(entry.get("description") or "")),
            original_url=original,
            published_at=_parse_rfc822(str(entry.get("pubDate") or "")),
            retrieved_at=retrieved_at,
        )


class NaverNewsSearchSource:
    """`특징주` 최신순 조회를 polling 공급원으로 쓴다."""

    def __init__(
        self,
        client: NaverApiHubClient,
        *,
        source_id: str = "naver_api_hub_featured",
        query: str = FEATURED_QUERY,
        display: int = FEATURED_POLL_DISPLAY,
    ) -> None:
        self.source_id = source_id
        self.source_type = NewsSourceType.NAVER_NEWS_SEARCH
        self._client = client
        self._query = query
        self._display = display

    def fetch(self, cursor: SourceCursor) -> Sequence[RawNewsItem]:
        items = self._client.search(
            self._query,
            source_id=self.source_id,
            source_type=NewsSourceType.NAVER_NEWS_SEARCH,
            display=self._display,
        )
        return _chronological(
            [item for item in items if _is_new(item.published_at, cursor)]
        )


class _QueryTermsRequest(Protocol):
    query_terms: tuple[str, ...]


class NaverSupplementalSearchSource:
    """로컬 근거가 없을 때만 게이트를 통과한 요청으로 호출된다."""

    def __init__(
        self,
        client: NaverApiHubClient,
        *,
        source_id: str = "naver_api_hub_supplemental",
        display: int = SUPPLEMENTAL_DISPLAY,
        max_terms: int = SUPPLEMENTAL_MAX_TERMS,
    ) -> None:
        self.source_id = source_id
        self.source_type = NewsSourceType.SUPPLEMENTAL_SEARCH
        self._client = client
        self._display = display
        self._max_terms = max_terms

    def search(self, request: _QueryTermsRequest) -> Sequence[RawNewsItem]:
        seen: dict[str, RawNewsItem] = {}
        for term in request.query_terms[: self._max_terms]:
            for item in self._client.search(
                term,
                source_id=self.source_id,
                source_type=NewsSourceType.SUPPLEMENTAL_SEARCH,
                display=self._display,
            ):
                seen.setdefault(item.source_item_id, item)
        return tuple(seen.values())


def parse_rss_feed_configs(raw: str) -> tuple[RssFeedConfig, ...]:
    """``source_id|매체명|feed URL`` 항목들을 ``;``로 구분해 파싱한다."""

    configs: list[RssFeedConfig] = []
    for entry in raw.split(";"):
        if not entry.strip():
            continue
        parts = [part.strip() for part in entry.split("|")]
        if len(parts) != 3:
            raise ValueError(
                f"RSS 설정 형식이 잘못됐습니다 (source_id|매체명|URL): {entry!r}"
            )
        source_id, publisher, feed_url = parts
        configs.append(RssFeedConfig(source_id, publisher, feed_url))
    return tuple(configs)


def create_live_news_sources(
    environ: Mapping[str, str],
    *,
    http_client: httpx.Client | None = None,
    clock: Clock = _utc_now,
) -> tuple[NewsSource, ...]:
    """환경변수에 설정된 공급원만 조립한다. 설정이 없으면 빈 tuple."""

    sources: list[NewsSource] = [
        RssNewsSource(config, http_client=http_client, clock=clock)
        for config in parse_rss_feed_configs(environ.get("NEWS_RSS_SOURCES", ""))
    ]
    client = _naver_client_from_env(environ, http_client=http_client, clock=clock)
    if client is not None:
        sources.append(NaverNewsSearchSource(client))
    return tuple(sources)


def create_supplemental_search_source(
    environ: Mapping[str, str],
    *,
    http_client: httpx.Client | None = None,
    clock: Clock = _utc_now,
) -> NaverSupplementalSearchSource | None:
    client = _naver_client_from_env(environ, http_client=http_client, clock=clock)
    if client is None:
        return None
    return NaverSupplementalSearchSource(client)


def _naver_client_from_env(
    environ: Mapping[str, str],
    *,
    http_client: httpx.Client | None,
    clock: Clock,
) -> NaverApiHubClient | None:
    client_id = environ.get("NAVER_API_HUB_CLIENT_ID", "").strip()
    client_secret = environ.get("NAVER_API_HUB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return NaverApiHubClient(
        client_id, client_secret, http_client=http_client, clock=clock
    )
