"""뉴스 URL과 provider scalar를 보수적으로 정규화한다."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import NewsSourceContractError
from .hashing import normalize_display_text

_TRACKING_KEYS = {"fbclid", "gclid", "igshid"}


def canonicalize_url(value: str, *, allowed_hosts: frozenset[str], path: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise NewsSourceContractError(
            "URL_INVALID", path, "유효한 HTTPS 원문 URL이 필요합니다."
        ) from exc
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not host or host not in allowed_hosts:
        raise NewsSourceContractError(
            "URL_NOT_ALLOWED",
            path,
            "허용 목록에 있는 HTTPS 원문 host만 사용할 수 있습니다.",
        )
    port = parsed.port
    netloc = host if port in {None, 443} else f"{host}:{port}"
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    normalized_path = parsed.path or "/"
    return urlunsplit(("https", netloc, normalized_path, urlencode(sorted(query)), ""))


def text(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NewsSourceContractError(
            "TEXT_REQUIRED", path, "비어 있지 않은 문자열이 필요합니다."
        )
    return normalize_display_text(value)


def optional_text(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return text(value, path=path)


def timestamp(value: object, *, path: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise NewsSourceContractError(
            "TIMESTAMP_REQUIRED", path, "ISO 8601 timestamp가 필요합니다."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NewsSourceContractError(
            "TIMESTAMP_INVALID", path, "유효한 ISO 8601 timestamp가 필요합니다."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NewsSourceContractError(
            "TIMESTAMP_TIMEZONE_REQUIRED", path, "timezone이 있는 timestamp가 필요합니다."
        )
    return parsed


def string_tuple(value: object, *, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise NewsSourceContractError(
            "ARRAY_REQUIRED", path, "문자열 배열이 필요합니다."
        )
    normalized = tuple(text(item, path=f"{path}[]") for item in value)
    if len(set(normalized)) != len(normalized):
        raise NewsSourceContractError(
            "ARRAY_DUPLICATE", path, "배열 항목은 중복될 수 없습니다."
        )
    return normalized
