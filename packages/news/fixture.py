"""Tracked synthetic JSON만 읽는 offline news provider."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from .errors import NewsSourceContractError
from .models import ProviderBatch, ProviderFetchRequest
from .normalization import text, timestamp


class FixtureNewsProvider:
    """Network 기능이 전혀 없는 deterministic provider test double."""

    def __init__(self, batch: ProviderBatch) -> None:
        self._batch = batch
        self.fetch_calls: list[ProviderFetchRequest] = []

    def fetch(self, request: ProviderFetchRequest) -> ProviderBatch:
        self.fetch_calls.append(request)
        if request.source_id != self._batch.source_id:
            raise NewsSourceContractError(
                "FIXTURE_SOURCE_MISMATCH",
                "$.sourceId",
                "fixture source와 요청 source가 일치하지 않습니다.",
            )
        if len(self._batch.items) > request.limit:
            raise NewsSourceContractError(
                "FIXTURE_LIMIT_EXCEEDED",
                "$.items",
                "fixture item 수가 요청 limit을 초과합니다.",
            )
        return self._batch


def load_fixture_provider(
    path: Path,
    *,
    repository_root: Path,
    relative_fixture_root: Path = Path("tests/evidence/fixtures"),
) -> FixtureNewsProvider:
    fixture_root = (repository_root.resolve() / relative_fixture_root).resolve()
    resolved = path.resolve()
    if (
        not resolved.is_relative_to(fixture_root)
        or resolved.suffix.casefold() != ".json"
        or not resolved.is_file()
    ):
        raise NewsSourceContractError(
            "UNAPPROVED_FIXTURE_PATH",
            "$fixture",
            "tests/evidence/fixtures 아래의 tracked JSON만 읽을 수 있습니다.",
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NewsSourceContractError(
            "FIXTURE_INVALID", "$fixture", "UTF-8 JSON fixture를 읽을 수 없습니다."
        ) from exc
    if not isinstance(payload, dict):
        raise NewsSourceContractError(
            "FIXTURE_INVALID", "$", "fixture 최상위 값은 object여야 합니다."
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or any(
        not isinstance(item, dict) for item in raw_items
    ):
        raise NewsSourceContractError(
            "FIXTURE_ITEMS_INVALID", "$.items", "fixture items는 object 배열이어야 합니다."
        )
    as_of = cast(datetime, timestamp(payload.get("asOf"), path="$.asOf"))
    fetched_at = cast(
        datetime, timestamp(payload.get("fetchedAt"), path="$.fetchedAt")
    )
    next_cursor_raw = payload.get("nextCursor")
    next_cursor = (
        None
        if next_cursor_raw is None
        else text(next_cursor_raw, path="$.nextCursor")
    )
    batch = ProviderBatch(
        source_id=text(payload.get("sourceId"), path="$.sourceId"),
        provider_version=text(
            payload.get("providerVersion"), path="$.providerVersion"
        ),
        as_of=as_of,
        fetched_at=fetched_at,
        items=tuple(cast(Mapping[str, object], item) for item in raw_items),
        next_cursor=next_cursor,
    )
    return FixtureNewsProvider(batch)
