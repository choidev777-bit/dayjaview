"""Deterministic URL·제목 정규화와 중복 판정 키."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "igshid", "spm", "cmpid", "ref"})
_BRACKET = re.compile(r"[\[\(【][^\]\)】]*[\]\)】]")
_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")
# 제목이 `[특징주`로 여는 것만 받는다. `[특징주]`·`[특징주 강세]`·`[특징주 급등]`은
# 통과하고, `[ET특징주]`·`[투데이's 특징주]`처럼 매체 접두어가 앞에 붙은 것과
# 본문 중간에 "특징주"가 스치는 칼럼·시황은 걸러진다.
_FEATURED_OPENING = re.compile(r"^\s*[\[\(【]\s*특징주")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.netloc:
        raise ValueError("원문 URL이 http(s) 절대 주소가 아닙니다")
    host = parts.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    if parts.port is not None and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS
        and not key.lower().startswith(_TRACKING_PREFIXES)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https" if parts.scheme == "http" else parts.scheme, host, path, urlencode(query), ""))


def normalized_title(title: str) -> str:
    """매체가 붙이는 말머리와 문장부호를 제거한 중복 판정용 제목."""

    return _NON_WORD.sub(" ", _BRACKET.sub(" ", title).casefold()).strip()


def is_featured_stock_title(title: str) -> bool:
    return _FEATURED_OPENING.match(title) is not None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def title_hash(title: str) -> str:
    return _digest(normalized_title(title))


def content_hash(title: str, description: str) -> str:
    return _digest(f"{normalized_title(title)}\n{normalized_title(description)}")


def news_id(canonical: str) -> str:
    return f"news_{_digest(canonical)[:32]}"
