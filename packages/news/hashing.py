"""뉴스 source metadata와 dedupe에 쓰는 결정적 정규화·hash helper."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence

_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^0-9a-z가-힣]+")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_display_text(value: str) -> str:
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value).strip())


def normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _TOKEN_RE.sub("", normalized)


def article_content_hash(
    *,
    title: str,
    grounding_text: str,
    theme_ids: Sequence[str],
    theme_terms: Sequence[str],
    stock_codes: Sequence[str],
    stock_names: Sequence[str],
) -> str:
    content: Mapping[str, object] = {
        "groundingText": normalize_display_text(grounding_text),
        "stockCodes": sorted(set(stock_codes)),
        "stockNames": sorted(
            {normalize_display_text(item) for item in stock_names}
        ),
        "themeIds": sorted(set(theme_ids)),
        "themeTerms": sorted(
            {normalize_display_text(item) for item in theme_terms}
        ),
        "title": normalize_display_text(title),
    }
    return sha256_text(canonical_json(content))
