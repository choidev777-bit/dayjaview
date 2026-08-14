"""Resumable public DailyFeaturedTheme API capture and strict backfill loader."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .daily import DAILY_LIST_URL, derive_daily_post_key, parse_daily_html_body
from .errors import FixtureValidationError
from .hashing import sha256_bytes, sha256_json, sha256_text
from .models import (
    ComponentStatus,
    DailyBackfill,
    DailyListEntry,
    DailyPost,
    DailyRelation,
    QualityIssue,
    RawSnapshot,
)

DAILY_API_BASE_URL = "https://api.infostock.co.kr:9081/web"
DAILY_LIST_ENDPOINT = f"{DAILY_API_BASE_URL}/flash/list"
DAILY_DETAIL_ENDPOINT = f"{DAILY_API_BASE_URL}/flash/html"
DAILY_API_PARSER_VERSION = "infostock-daily-api/1.0.0"
DAILY_DATASET = "infostock-daily-featured-theme-full-backfill"
_DATE8_RE = re.compile(r"^\d{8}$")
_SAFE_ID_RE = re.compile(r"^[0-9A-Za-z_-]+$")
_SEOUL = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class DailyApiObservation:
    raw_bytes: bytes
    status_code: int
    content_type: str | None
    collected_at: datetime


DailyApiTransport = Callable[[str, Mapping[str, object]], DailyApiObservation]


def _fail(code: str, path: str, detail: str) -> NoReturn:
    raise FixtureValidationError(code, path, detail)


def _validate_date8(value: str, path: str) -> date:
    if not _DATE8_RE.fullmatch(value):
        _fail("DAILY_DATE_INVALID", path, "YYYYMMDD 날짜가 필요합니다.")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError as exc:
        raise FixtureValidationError(
            "DAILY_DATE_INVALID", path, "유효하지 않은 날짜입니다."
        ) from exc


def _aware_datetime(value: object, path: str) -> datetime:
    if not isinstance(value, str):
        _fail("DAILY_MANIFEST_INVALID", path, "ISO 8601 시각이 필요합니다.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FixtureValidationError(
            "DAILY_MANIFEST_INVALID", path, "유효한 ISO 8601 시각이 필요합니다."
        ) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        _fail("DAILY_MANIFEST_INVALID", path, "timezone이 있는 시각이 필요합니다.")
    return result


def _json_object(raw_bytes: bytes, path: str) -> dict[str, Any]:
    try:
        value = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureValidationError(
            "DAILY_JSON_INVALID", path, "UTF-8 JSON 응답이 필요합니다."
        ) from exc
    if not isinstance(value, dict):
        _fail("DAILY_JSON_INVALID", path, "최상위 JSON object가 필요합니다.")
    return cast(dict[str, Any], value)


def _atomic_write(path: Path, raw_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(raw_bytes)
    temporary.replace(path)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    raw = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(path, raw)


def _default_transport(
    endpoint: str, payload: Mapping[str, object]
) -> DailyApiObservation:
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "DAYJAVIEW-Infostock-Backfill/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        raw_bytes = response.read()
        return DailyApiObservation(
            raw_bytes=raw_bytes,
            status_code=int(response.status),
            content_type=response.headers.get("Content-Type"),
            collected_at=datetime.now(UTC),
        )


def _request_with_retry(
    transport: DailyApiTransport,
    endpoint: str,
    payload: Mapping[str, object],
    *,
    retry_delays: tuple[float, ...],
) -> DailyApiObservation:
    last_error: Exception | None = None
    for attempt in range(len(retry_delays) + 1):
        try:
            observation = transport(endpoint, payload)
            if observation.status_code != 200:
                raise RuntimeError(f"HTTP {observation.status_code}")
            return observation
        except (HTTPError, URLError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt >= len(retry_delays):
                break
            time.sleep(retry_delays[attempt])
    assert last_error is not None
    raise RuntimeError(
        f"Daily API 요청 실패: {type(last_error).__name__}: {last_error}"
    ) from last_error


def _validate_api_success(payload: Mapping[str, Any], path: str) -> dict[str, Any]:
    if payload.get("success") is not True:
        _fail(
            "DAILY_API_FAILED",
            path,
            str(payload.get("message") or "API success=false 응답입니다."),
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        _fail("DAILY_API_INVALID", f"{path}.data", "data object가 필요합니다.")
    return cast(dict[str, Any], data)


def _validate_api_list(payload: Mapping[str, Any], path: str) -> dict[str, Any]:
    if (
        payload.get("success") is False
        and "데이터가 없습니다" in str(payload.get("message") or "")
    ):
        return {"items": [], "nextKey": None}
    return _validate_api_success(payload, path)


def _window_end_from_null_cursor(value: str, path: str) -> str | None:
    if not value.endswith("null"):
        return None
    candidate = value[:8]
    _validate_date8(candidate, path)
    return candidate


def _relative_file(root: Path, relative: object, path: str) -> Path:
    if not isinstance(relative, str) or not relative:
        _fail("DAILY_MANIFEST_INVALID", path, "상대 파일 경로가 필요합니다.")
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        _fail("DAILY_PATH_INVALID", path, "수집 디렉터리 밖 경로는 허용하지 않습니다.")
    if candidate.is_symlink():
        _fail("DAILY_PATH_INVALID", path, "symbolic link 입력은 허용하지 않습니다.")
    return candidate


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        return _json_object(path.read_bytes(), "$.dailyManifest")
    except OSError as exc:
        raise FixtureValidationError(
            "DAILY_COLLECTION_READ_FAILED",
            "$.dailyManifest",
            "Daily manifest를 읽지 못했습니다.",
        ) from exc


def _checkpoint(manifest: Mapping[str, Any]) -> dict[str, object]:
    posts = manifest.get("posts")
    failures = manifest.get("failures")
    return {
        "schemaVersion": "1.0.0",
        "dataset": DAILY_DATASET,
        "phase": (
            "COMPLETE"
            if manifest.get("coverageComplete") is True
            else "DETAILS"
            if manifest.get("paginationComplete") is True
            else "LISTS"
        ),
        "nextKey": manifest.get("nextKey"),
        "listPagesCompleted": len(cast(list[object], manifest.get("pages") or [])),
        "postsDiscovered": int(manifest.get("postsDiscovered") or 0),
        "detailsCompleted": len(cast(dict[str, object], posts or {})),
        "failures": len(cast(dict[str, object], failures or {})),
        "updatedAt": datetime.now(UTC).isoformat(),
    }


def _save_collection_state(root: Path, manifest: dict[str, Any]) -> None:
    _atomic_json(root / "manifest.json", manifest)
    _atomic_json(root / "checkpoint.json", _checkpoint(manifest))


def _list_items_from_pages(
    root: Path, pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, page in enumerate(pages):
        target = _relative_file(root, page.get("file"), f"$.pages[{position}].file")
        raw_bytes = target.read_bytes()
        raw_hash = sha256_bytes(raw_bytes)
        if raw_hash != page.get("rawHash"):
            _fail(
                "DAILY_HASH_MISMATCH",
                f"$.pages[{position}].rawHash",
                "저장된 목록 응답 hash가 manifest와 다릅니다.",
            )
        response = _json_object(raw_bytes, f"$.pages[{position}].response")
        data = _validate_api_list(response, f"$.pages[{position}].response")
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            _fail(
                "DAILY_API_INVALID",
                f"$.pages[{position}].response.data.items",
                "items array가 필요합니다.",
            )
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                _fail("DAILY_API_INVALID", "$.daily.items", "게시물 object가 필요합니다.")
            item = cast(dict[str, Any], raw_item)
            source_id = str(item.get("id") or "").strip()
            if not source_id:
                _fail("DAILY_SOURCE_ID_MISSING", "$.daily.items.id", "source ID가 없습니다.")
            if source_id in seen_ids:
                _fail(
                    "DAILY_SOURCE_ID_DUPLICATE",
                    "$.daily.items.id",
                    f"중복 source ID: {source_id}",
                )
            seen_ids.add(source_id)
            items.append(item)
    return items


def collect_daily_api_backfill(
    output_dir: Path,
    *,
    start_date: str,
    end_date: str,
    approved: bool = False,
    page_size: int = 100,
    request_delay_seconds: float = 1.0,
    retry_delays: tuple[float, ...] = (15.0, 30.0, 60.0, 90.0, 120.0),
    max_pages: int = 10_000,
    resume: bool = True,
    transport: DailyApiTransport | None = None,
) -> dict[str, Any]:
    """Capture every list cursor and detail response with an atomic checkpoint."""

    if not approved:
        raise RuntimeError("Daily live 수집에는 명시적 --approved 승인이 필요합니다.")
    first_date = _validate_date8(start_date, "$.startDate")
    last_date = _validate_date8(end_date, "$.endDate")
    if first_date > last_date:
        raise ValueError("start_date는 end_date보다 늦을 수 없습니다.")
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size는 1~100이어야 합니다.")
    if request_delay_seconds < 0:
        raise ValueError("request_delay_seconds는 0 이상이어야 합니다.")

    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        if not resume:
            raise RuntimeError("기존 manifest가 있습니다. --resume을 사용하세요.")
        manifest = _read_manifest(manifest_path)
        expected = {
            "schemaVersion": "1.0.0",
            "dataset": DAILY_DATASET,
            "startDate": start_date,
            "endDate": end_date,
            "pageSize": page_size,
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise RuntimeError(f"resume manifest의 {key} 값이 요청과 다릅니다.")
        if manifest.get("coverageComplete") is True:
            return manifest
    else:
        if any(root.iterdir()):
            raise RuntimeError("빈 output directory 또는 기존 manifest가 필요합니다.")
        now = datetime.now(UTC).isoformat()
        manifest = {
            "schemaVersion": "1.0.0",
            "dataset": DAILY_DATASET,
            "sourceProvider": "INFOSTOCK",
            "sourcePageUrl": DAILY_LIST_URL,
            "listEndpoint": DAILY_LIST_ENDPOINT,
            "detailEndpoint": DAILY_DETAIL_ENDPOINT,
            "parserVersion": DAILY_API_PARSER_VERSION,
            "collectionApproval": "USER_CONFIRMED",
            "startDate": start_date,
            "endDate": end_date,
            "pageSize": page_size,
            "startedAt": now,
            "finishedAt": None,
            "paginationComplete": False,
            "coverageComplete": False,
            "nextKey": None,
            "continuationEndDate": end_date,
            "pages": [],
            "posts": {},
            "failures": {},
            "postsDiscovered": 0,
        }
        _save_collection_state(root, manifest)

    active_transport = transport or _default_transport
    pages = cast(list[dict[str, Any]], manifest["pages"])
    if manifest.get("paginationComplete") is not True:
        seen_cursors = {
            str(page.get("nextKey")) for page in pages if page.get("nextKey")
        }
        next_key = str(manifest.get("nextKey") or "") or None
        active_end_date = str(manifest.get("continuationEndDate") or end_date)
        if next_key:
            recovered_end_date = _window_end_from_null_cursor(
                next_key, "$.dailyManifest.nextKey"
            )
            if recovered_end_date is not None:
                active_end_date = recovered_end_date
                next_key = None
                manifest["nextKey"] = None
                manifest["continuationEndDate"] = active_end_date
                _save_collection_state(root, manifest)
        for page_number in range(len(pages) + 1, max_pages + 1):
            request_payload: dict[str, object] = {
                "menuType": "MENU_DAILY_FEATURED_THEME",
                "count": page_size,
                "startDate": start_date,
                "endDate": active_end_date,
            }
            if next_key:
                request_payload["nextKey"] = next_key
            observation = _request_with_retry(
                active_transport,
                DAILY_LIST_ENDPOINT,
                request_payload,
                retry_delays=retry_delays,
            )
            response = _json_object(
                observation.raw_bytes, f"$.pages[{page_number - 1}].response"
            )
            data = _validate_api_list(
                response, f"$.pages[{page_number - 1}].response"
            )
            raw_items = data.get("items")
            if not isinstance(raw_items, list):
                _fail("DAILY_API_INVALID", "$.daily.items", "items array가 필요합니다.")
            new_key_value = data.get("nextKey")
            api_next_key = str(new_key_value).strip() if new_key_value else None
            continuation_end_date = (
                _window_end_from_null_cursor(
                    api_next_key, f"$.pages[{page_number - 1}].response.data.nextKey"
                )
                if api_next_key
                else None
            )
            new_key = api_next_key if continuation_end_date is None else None
            if not raw_items and new_key:
                _fail(
                    "DAILY_CURSOR_INVALID",
                    "$.daily.nextKey",
                    "빈 page는 다음 cursor를 가질 수 없습니다.",
                )
            if new_key and new_key in seen_cursors:
                _fail("DAILY_CURSOR_LOOP", "$.daily.nextKey", "cursor가 반복됐습니다.")
            relative = f"lists/page-{page_number:05d}.json"
            target = root / relative
            _atomic_write(target, observation.raw_bytes)
            dates = [
                str(item.get("sendDate"))
                for item in raw_items
                if isinstance(item, dict) and item.get("sendDate")
            ]
            page_record = {
                "pageNumber": page_number,
                "file": relative,
                "request": request_payload,
                "statusCode": observation.status_code,
                "contentType": observation.content_type,
                "collectedAt": observation.collected_at.isoformat(),
                "rawHash": sha256_bytes(observation.raw_bytes),
                "itemCount": len(raw_items),
                "firstDate": dates[0] if dates else None,
                "lastDate": dates[-1] if dates else None,
                "nextKey": new_key,
                "apiNextKey": api_next_key,
                "continuationEndDate": continuation_end_date,
            }
            pages.append(page_record)
            if new_key:
                seen_cursors.add(new_key)
            next_key = new_key
            manifest["nextKey"] = next_key
            if continuation_end_date is not None:
                active_end_date = continuation_end_date
            manifest["continuationEndDate"] = active_end_date
            manifest["paginationComplete"] = (
                next_key is None and continuation_end_date is None
            )
            _save_collection_state(root, manifest)
            if request_delay_seconds:
                time.sleep(request_delay_seconds)
            if manifest["paginationComplete"] is True:
                break
        else:
            _fail(
                "DAILY_PAGE_LIMIT",
                "$.daily.pages",
                f"pagination이 {max_pages:,} page를 초과했습니다.",
            )

    items = _list_items_from_pages(root, pages)
    manifest["postsDiscovered"] = len(items)
    posts = cast(dict[str, dict[str, Any]], manifest["posts"])
    failures = cast(dict[str, dict[str, Any]], manifest["failures"])
    for source_order, item in enumerate(items):
        source_id = str(item.get("id") or "").strip()
        existing = posts.get(source_id)
        if existing is not None:
            existing_path = _relative_file(
                root, existing.get("file"), f"$.posts.{source_id}.file"
            )
            if (
                existing_path.is_file()
                and sha256_bytes(existing_path.read_bytes()) == existing.get("rawHash")
            ):
                continue
            _fail(
                "DAILY_HASH_MISMATCH",
                f"$.posts.{source_id}.rawHash",
                "기존 detail 파일 hash가 manifest와 다릅니다.",
            )
        news_type = str(item.get("newsType1") or "MARKET_THEME_DAILY")
        request_payload = {"id": source_id, "newsType": news_type}
        try:
            observation = _request_with_retry(
                active_transport,
                DAILY_DETAIL_ENDPOINT,
                request_payload,
                retry_delays=retry_delays,
            )
            response = _json_object(
                observation.raw_bytes, f"$.posts.{source_id}.response"
            )
            data = _validate_api_success(response, f"$.posts.{source_id}.response")
            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                _fail(
                    "DAILY_BODY_MISSING",
                    f"$.posts.{source_id}.response.data.content",
                    "본문 content가 없습니다.",
                )
        except (FixtureValidationError, RuntimeError) as exc:
            failures[source_id] = {
                "sourceOrder": source_order,
                "errorType": type(exc).__name__,
                "messageKo": str(exc),
                "failedAt": datetime.now(UTC).isoformat(),
            }
            _save_collection_state(root, manifest)
            continue
        filename_id = source_id if _SAFE_ID_RE.fullmatch(source_id) else sha256_text(source_id)
        relative = f"details/{filename_id}.json"
        _atomic_write(root / relative, observation.raw_bytes)
        source_date = str(item.get("sendDate") or "")
        posts[source_id] = {
            "sourceOrder": source_order,
            "sourcePostId": source_id,
            "sourceDate": source_date,
            "title": str(item.get("title") or "").strip(),
            "newsType": news_type,
            "canonicalSourceUrl": (
                f"{DAILY_LIST_URL}?sendDate={source_date}" if source_date else DAILY_LIST_URL
            ),
            "file": relative,
            "statusCode": observation.status_code,
            "contentType": observation.content_type,
            "collectedAt": observation.collected_at.isoformat(),
            "rawHash": sha256_bytes(observation.raw_bytes),
            "bodyHash": sha256_text(content),
        }
        failures.pop(source_id, None)
        _save_collection_state(root, manifest)
        if request_delay_seconds:
            time.sleep(request_delay_seconds)

    manifest["coverageComplete"] = (
        manifest.get("paginationComplete") is True
        and len(posts) == len(items)
        and not failures
    )
    if manifest["coverageComplete"]:
        manifest["finishedAt"] = datetime.now(UTC).isoformat()
    _save_collection_state(root, manifest)
    return manifest


def _observation_file(
    root: Path,
    record: Mapping[str, Any],
    *,
    path: str,
) -> tuple[Path, bytes, str]:
    target = _relative_file(root, record.get("file"), f"{path}.file")
    try:
        raw_bytes = target.read_bytes()
    except OSError as exc:
        raise FixtureValidationError(
            "DAILY_COLLECTION_READ_FAILED", f"{path}.file", "raw 파일을 읽지 못했습니다."
        ) from exc
    raw_hash = sha256_bytes(raw_bytes)
    if record.get("rawHash") != raw_hash:
        _fail("DAILY_HASH_MISMATCH", f"{path}.rawHash", "raw hash가 다릅니다.")
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FixtureValidationError(
            "DAILY_JSON_INVALID", f"{path}.file", "UTF-8 raw 파일이 필요합니다."
        ) from exc
    return target, raw_bytes, raw_text


def _normalized_daily_hash(
    *,
    title: str,
    published: date,
    raw_body: str | None,
    body_status: str,
    relations: tuple[DailyRelation, ...],
) -> str:
    return sha256_json(
        {
            "body": raw_body,
            "bodyStatus": body_status,
            "publishedDate": published.isoformat(),
            "relations": [
                {
                    "description": relation.description,
                    "rawText": relation.raw_text,
                    "sourceStockCode": relation.source_stock_code,
                    "sourceStockName": relation.source_stock_name,
                    "sourceThemeName": relation.source_theme_name,
                    "type": relation.relation_type,
                }
                for relation in relations
            ],
            "title": title,
            "visibility": "VISIBLE",
        }
    )


def load_daily_api_backfill(
    directory: Path,
) -> tuple[DailyBackfill, dict[str, str]]:
    """Validate a completed API capture and build raw plus normalized projections."""

    root = directory.resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("DAILY_PATH_INVALID", "$.dailyDirectory", "Daily 수집 디렉터리가 필요합니다.")
    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = _json_object(manifest_bytes, "$.dailyManifest")
    expected_values = {
        "schemaVersion": "1.0.0",
        "dataset": DAILY_DATASET,
        "sourceProvider": "INFOSTOCK",
        "parserVersion": DAILY_API_PARSER_VERSION,
        "listEndpoint": DAILY_LIST_ENDPOINT,
        "detailEndpoint": DAILY_DETAIL_ENDPOINT,
    }
    for key, value in expected_values.items():
        if manifest.get(key) != value:
            _fail(
                "DAILY_MANIFEST_INVALID",
                f"$.dailyManifest.{key}",
                f"{key} 값이 수집 계약과 다릅니다.",
            )
    _aware_datetime(manifest.get("startedAt"), "$.dailyManifest.startedAt")
    finished_value = manifest.get("finishedAt")
    finished_at = (
        _aware_datetime(finished_value, "$.dailyManifest.finishedAt")
        if finished_value
        else datetime.now(UTC)
    )
    pages_value = manifest.get("pages")
    posts_value = manifest.get("posts")
    failures_value = manifest.get("failures")
    if not isinstance(pages_value, list) or not isinstance(posts_value, dict):
        _fail("DAILY_MANIFEST_INVALID", "$.dailyManifest", "pages/posts가 필요합니다.")
    pages = cast(list[dict[str, Any]], pages_value)
    post_records = cast(dict[str, dict[str, Any]], posts_value)
    failures = cast(dict[str, dict[str, Any]], failures_value or {})
    file_hashes: dict[str, str] = {
        "daily/manifest.json": sha256_bytes(manifest_bytes)
    }
    snapshots: list[RawSnapshot] = [
        RawSnapshot(
            page_type="DAILY_MANIFEST",
            source_entity_id=DAILY_DATASET,
            source_url=DAILY_LIST_URL,
            collected_at=finished_at,
            as_of=finished_at,
            raw_hash=file_hashes["daily/manifest.json"],
            source_content_hash=None,
            raw_payload_text=manifest_bytes.decode("utf-8"),
            raw_format="JSON",
            is_complete=manifest.get("coverageComplete") is True,
            quality_status=(
                "OK" if manifest.get("coverageComplete") is True else "PARTIAL_BACKFILL"
            ),
            parser_version=DAILY_API_PARSER_VERSION,
        )
    ]

    raw_items: list[dict[str, Any]] = []
    for page_position, page in enumerate(pages):
        _, raw_bytes, raw_text = _observation_file(
            root, page, path=f"$.dailyManifest.pages[{page_position}]"
        )
        response = _json_object(raw_bytes, f"$.daily.pages[{page_position}].response")
        data = _validate_api_list(response, f"$.daily.pages[{page_position}].response")
        items = data.get("items")
        if not isinstance(items, list):
            _fail("DAILY_API_INVALID", "$.daily.items", "items array가 필요합니다.")
        raw_items.extend(cast(list[dict[str, Any]], items))
        page_number = int(page.get("pageNumber") or page_position + 1)
        collected_at = _aware_datetime(
            page.get("collectedAt"), f"$.dailyManifest.pages[{page_position}].collectedAt"
        )
        relative = str(page.get("file"))
        file_hashes[f"daily/{relative}"] = sha256_bytes(raw_bytes)
        snapshots.append(
            RawSnapshot(
                page_type="DAILY_LIST",
                source_entity_id=f"page:{page_number}",
                source_url=DAILY_LIST_ENDPOINT,
                collected_at=collected_at,
                as_of=collected_at,
                raw_hash=sha256_bytes(raw_bytes),
                source_content_hash=sha256_bytes(raw_bytes),
                raw_payload_text=raw_text,
                raw_format="JSON",
                is_complete=True,
                parser_version=DAILY_API_PARSER_VERSION,
            )
        )

    if manifest.get("postsDiscovered") != len(raw_items):
        _fail(
            "DAILY_MANIFEST_COUNT_MISMATCH",
            "$.dailyManifest.postsDiscovered",
            "목록 원본에서 재계산한 게시물 수와 다릅니다.",
        )

    entries: list[DailyListEntry] = []
    posts: list[DailyPost] = []
    issues: list[QualityIssue] = []
    seen_ids: set[str] = set()
    dates: list[date] = []
    for source_order, item in enumerate(raw_items):
        if not isinstance(item, dict):
            _fail("DAILY_API_INVALID", "$.daily.items", "게시물 object가 필요합니다.")
        source_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        source_date = str(item.get("sendDate") or "").strip()
        if not source_id or source_id in seen_ids:
            _fail(
                "DAILY_SOURCE_ID_DUPLICATE",
                "$.daily.items.id",
                f"누락 또는 중복 source ID: {source_id}",
            )
        seen_ids.add(source_id)
        if not title:
            _fail("DAILY_TITLE_MISSING", "$.daily.items.title", "제목이 없습니다.")
        published = _validate_date8(source_date, "$.daily.items.sendDate")
        dates.append(published)
        canonical_url = f"{DAILY_LIST_URL}?sendDate={source_date}"
        source_key = derive_daily_post_key(
            source_post_id=source_id, published_date=published, title=title
        )
        entries.append(
            DailyListEntry(
                source_order=source_order,
                source_post_key=source_key,
                source_post_id=source_id,
                source_url=canonical_url,
                title=title,
                published_date=published,
                source_date=source_date,
                quality_status="OK",
            )
        )
        record = post_records.get(source_id)
        raw_body: str | None = None
        relations: tuple[DailyRelation, ...] = ()
        body_status = "MISSING"
        detail_snapshot: RawSnapshot | None = None
        if record is None:
            issues.append(
                QualityIssue(
                    "DAILY_FEATURED_THEME",
                    "BODY_MISSING",
                    "ERROR",
                    "DAILY_POST",
                    source_key,
                    source_order,
                    {"sourcePostId": source_id, "title": title},
                )
            )
        else:
            if str(record.get("sourcePostId") or "") != source_id:
                _fail(
                    "DAILY_POST_LINEAGE_MISMATCH",
                    f"$.dailyManifest.posts.{source_id}.sourcePostId",
                    "detail record의 source ID가 목록과 다릅니다.",
                )
            _, detail_bytes, detail_text = _observation_file(
                root, record, path=f"$.dailyManifest.posts.{source_id}"
            )
            detail_response = _json_object(
                detail_bytes, f"$.daily.posts.{source_id}.response"
            )
            detail_data = _validate_api_success(
                detail_response, f"$.daily.posts.{source_id}.response"
            )
            content = detail_data.get("content")
            if isinstance(content, str) and content.strip():
                raw_body = content
                if record.get("bodyHash") != sha256_text(content):
                    _fail(
                        "DAILY_BODY_HASH_MISMATCH",
                        f"$.dailyManifest.posts.{source_id}.bodyHash",
                        "본문 hash가 manifest와 다릅니다.",
                    )
                relations, body_status = parse_daily_html_body(content)
            collected_at = _aware_datetime(
                record.get("collectedAt"),
                f"$.dailyManifest.posts.{source_id}.collectedAt",
            )
            relative = str(record.get("file"))
            raw_hash = sha256_bytes(detail_bytes)
            file_hashes[f"daily/{relative}"] = raw_hash
            detail_snapshot = RawSnapshot(
                page_type="DAILY_DETAIL",
                source_entity_id=source_key,
                source_url=DAILY_DETAIL_ENDPOINT,
                collected_at=collected_at,
                as_of=datetime.combine(published, datetime_time.min, tzinfo=_SEOUL),
                raw_hash=raw_hash,
                source_content_hash=raw_hash,
                raw_payload_text=detail_text,
                raw_format="JSON",
                is_complete=raw_body is not None,
                quality_status=body_status,
                parser_version=DAILY_API_PARSER_VERSION,
            )
            snapshots.append(detail_snapshot)
        if body_status == "PARSE_PARTIAL":
            issues.append(
                QualityIssue(
                    "DAILY_FEATURED_THEME",
                    "BODY_PARSE_PARTIAL",
                    "WARNING",
                    "DAILY_POST",
                    source_key,
                    source_order,
                    {"sourcePostId": source_id, "relationCount": len(relations)},
                )
            )
        elif body_status == "PARSE_FAILED":
            issues.append(
                QualityIssue(
                    "DAILY_FEATURED_THEME",
                    "BODY_PARSE_FAILED",
                    "ERROR",
                    "DAILY_POST",
                    source_key,
                    source_order,
                    {"sourcePostId": source_id},
                )
            )
        posts.append(
            DailyPost(
                source_post_key=source_key,
                source_post_id=source_id,
                source_url=canonical_url,
                title=title,
                published_date=published,
                source_date=source_date,
                raw_body=raw_body,
                body_hash=sha256_text(raw_body) if raw_body is not None else None,
                normalized_hash=_normalized_daily_hash(
                    title=title,
                    published=published,
                    raw_body=raw_body,
                    body_status=body_status,
                    relations=relations,
                ),
                body_status=body_status,  # type: ignore[arg-type]
                visibility_status="VISIBLE",
                relations=relations,
                detail_snapshot=detail_snapshot,
            )
        )

    for source_id, failure in failures.items():
        issues.append(
            QualityIssue(
                "DAILY_FEATURED_THEME",
                "DETAIL_FETCH_FAILED",
                "ERROR",
                "DAILY_POST",
                f"source:{source_id}",
                int(failure.get("sourceOrder") or 0),
                {"errorType": str(failure.get("errorType") or "UNKNOWN")},
            )
        )
    complete = (
        manifest.get("paginationComplete") is True
        and manifest.get("coverageComplete") is True
        and len(posts) == len(entries)
        and all(post.raw_body is not None for post in posts)
        and not failures
    )
    status: ComponentStatus = (
        "COMPLETE" if complete else "PARTIAL" if entries else "FAILED"
    )
    return (
        DailyBackfill(
            component_status=status,
            pages=tuple(snapshots),
            entries=tuple(entries),
            posts=tuple(posts),
            first_page=1 if pages else None,
            last_page=len(pages) if pages else None,
            next_page=None if complete else len(pages) + 1,
            earliest_date=min(dates) if dates else None,
            latest_date=max(dates) if dates else None,
            coverage_complete=complete,
            blockers=(),
            quality_issues=tuple(issues),
        ),
        file_hashes,
    )
