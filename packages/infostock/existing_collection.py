"""Strict loader for the already collected Infostock full-sync directory."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Never, cast

from .daily import parse_legacy_daily_payload
from .daily_api import load_daily_api_backfill
from .errors import FixtureValidationError
from .hashing import sha256_bytes, sha256_json
from .models import (
    DailyBackfill,
    Direction,
    ImportBundle,
    QualityIssue,
    QualitySummary,
    RawSnapshot,
    ReferenceQualityStatus,
    StockReference,
    ThemeDetail,
    ThemeHistory,
    ThemeIndexItem,
    ThemeMembership,
)
from .policy import ExistingCollectionPolicy, InfostockAccessPolicy

PARSER_VERSION = "infostock-existing-collection/2.0.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STOCK_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
THEME_FILE_RE = re.compile(r"^theme-(\d+)\.json$")


def _fail(code: str, path: str, detail: str) -> Never:
    raise FixtureValidationError(code, path, detail)


def _read_json(path: Path, logical_path: str) -> tuple[bytes, str, dict[str, Any]]:
    try:
        raw_bytes = path.read_bytes()
        raw_text = raw_bytes.decode("utf-8")
        value = json.loads(raw_text)
    except OSError as exc:
        raise FixtureValidationError(
            "COLLECTION_READ_FAILED", logical_path, "수집본 파일을 읽지 못했습니다."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureValidationError(
            "COLLECTION_JSON_INVALID", logical_path, "유효한 UTF-8 JSON 파일이 아닙니다."
        ) from exc
    if not isinstance(value, dict):
        _fail("COLLECTION_JSON_INVALID", logical_path, "최상위 JSON object가 필요합니다.")
    return raw_bytes, raw_text, cast(dict[str, Any], value)


def _text(
    value: object, path: str, *, allow_empty: bool = False, optional: bool = False
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        _fail("COLLECTION_FIELD_INVALID", path, "문자열이 필요합니다.")
    result = cast(str, value).strip()
    if not result and not allow_empty:
        _fail("COLLECTION_FIELD_INVALID", path, "빈 문자열은 허용되지 않습니다.")
    return result


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail("COLLECTION_FIELD_INVALID", path, f"{minimum} 이상의 정수가 필요합니다.")
    return cast(int, value)


def _aware_datetime(value: object, path: str) -> datetime:
    text = _text(value, path)
    assert text is not None
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        _fail("COLLECTION_FIELD_INVALID", path, "ISO 8601 시각이 필요합니다.")
    if result.tzinfo is None or result.utcoffset() is None:
        _fail("COLLECTION_FIELD_INVALID", path, "timezone이 있는 시각이 필요합니다.")
    return result


def _optional_datetime(value: object, path: str) -> datetime | None:
    if value in {None, ""}:
        return None
    return _aware_datetime(value, path)


def _optional_date(value: object, path: str) -> date | None:
    if value in {None, ""}:
        return None
    text = _text(value, path)
    assert text is not None
    try:
        return date.fromisoformat(text)
    except ValueError:
        _fail("COLLECTION_FIELD_INVALID", path, "YYYY-MM-DD 날짜가 필요합니다.")


def _hash(value: object, path: str) -> str:
    text = _text(value, path)
    assert text is not None
    if not SHA256_RE.fullmatch(text):
        _fail("COLLECTION_FIELD_INVALID", path, "소문자 SHA-256 hex가 필요합니다.")
    return text


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("COLLECTION_FIELD_INVALID", path, "JSON object가 필요합니다.")
    return cast(dict[str, Any], value)


def _sequence(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("COLLECTION_FIELD_INVALID", path, "JSON array가 필요합니다.")
    return cast(list[Any], value)


def _validate_source_envelope(
    payload: Mapping[str, Any], *, source_type: str, path: str
) -> None:
    if payload.get("schemaVersion") != "1.0.0":
        _fail("COLLECTION_SCHEMA_UNSUPPORTED", f"{path}.schemaVersion", "1.0.0만 지원합니다.")
    if payload.get("source") != "infostock" or payload.get("sourceType") != source_type:
        _fail("COLLECTION_SOURCE_CONFLICT", path, "source/sourceType이 collector 계약과 다릅니다.")


def _reference(
    value: object,
    *,
    path: str,
    default_order: int,
) -> StockReference:
    item = _mapping(value, path)
    source_order = _integer(item.get("sourceOrder", default_order), f"{path}.sourceOrder")
    name = _text(item.get("name"), f"{path}.name")
    assert name is not None
    raw_code = item.get("stockCode")
    stock_code = str(raw_code).strip() if raw_code not in {None, ""} else None
    if stock_code is None:
        status: ReferenceQualityStatus = "SOURCE_CODE_MISSING"
    elif STOCK_CODE_RE.fullmatch(stock_code):
        status = "OK"
    else:
        status = "CODE_INVALID"
    source_url = _text(
        item.get("sourceUrl"), f"{path}.sourceUrl", allow_empty=False, optional=True
    )
    return StockReference(
        source_order=source_order,
        name=name,
        stock_code=stock_code,
        source_url=source_url,
        display_value=f"{stock_code}-{name}" if stock_code else name,
        quality_status=status,
    )


def _direction(raw_text: str) -> Direction:
    has_up = any(token in raw_text for token in ("상승", "강세", "급등"))
    has_down = any(token in raw_text for token in ("하락", "약세", "급락"))
    if has_up and has_down:
        return "MIXED"
    if has_up:
        return "UP"
    if has_down:
        return "DOWN"
    return "UNKNOWN"


def _empty_daily() -> DailyBackfill:
    issues = (
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "B-INFOSTOCK-AUTH",
            "BLOCKER",
            "BACKFILL",
            None,
            None,
            {"messageKo": "검증된 로그인 session이 없습니다."},
        ),
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "B-DATA-RIGHTS",
            "BLOCKER",
            "BACKFILL",
            None,
            None,
            {"messageKo": "저장·가공 권리 증거가 없습니다."},
        ),
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "DAILY_CAPTURE_MISSING",
            "BLOCKER",
            "BACKFILL",
            None,
            None,
            {"messageKo": "기존 Daily 수집본이 없습니다."},
        ),
    )
    return DailyBackfill(
        component_status="BLOCKED",
        pages=(),
        entries=(),
        posts=(),
        first_page=None,
        last_page=None,
        next_page=1,
        earliest_date=None,
        latest_date=None,
        coverage_complete=False,
        blockers=("B-INFOSTOCK-AUTH", "B-DATA-RIGHTS"),
        quality_issues=issues,
    )


def load_existing_collection(
    directory: Path,
    policy: ExistingCollectionPolicy | None = None,
    *,
    daily_backfill_directory: Path | None = None,
) -> ImportBundle:
    """Validate and load all 280 details plus any permitted existing Daily capture."""

    root = (policy or ExistingCollectionPolicy()).validate(directory)
    InfostockAccessPolicy.require_import_scope("LOCAL_AUDITED_IMPORT")
    manifest_bytes, manifest_text, manifest = _read_json(
        root / "manifest.json", "$.manifest"
    )
    index_bytes, index_text, index = _read_json(root / "theme-index.json", "$.index")
    if manifest.get("schemaVersion") != "1.0.0":
        _fail("COLLECTION_SCHEMA_UNSUPPORTED", "$.manifest.schemaVersion", "1.0.0만 지원합니다.")
    if manifest.get("dataset") != "infostock-theme-full-sync":
        _fail("COLLECTION_DATASET_CONFLICT", "$.manifest.dataset", "full-sync dataset이 아닙니다.")
    _validate_source_envelope(index, source_type="theme_index", path="$.index")
    index_captured = _aware_datetime(index.get("capturedAt"), "$.index.capturedAt")
    index_items_raw = _sequence(index.get("items"), "$.index.items")
    declared_index_hash = _hash(index.get("contentHash"), "$.index.contentHash")
    if sha256_json(index_items_raw) != declared_index_hash:
        _fail("SOURCE_HASH_MISMATCH", "$.index.contentHash", "collector index hash가 일치하지 않습니다.")

    index_items: list[ThemeIndexItem] = []
    seen_theme_ids: set[str] = set()
    for position, value in enumerate(index_items_raw):
        path = f"$.index.items[{position}]"
        item = _mapping(value, path)
        source_order = _integer(item.get("sourceOrder"), f"{path}.sourceOrder")
        if source_order != position:
            _fail("SOURCE_ORDER_CONFLICT", f"{path}.sourceOrder", "index sourceOrder가 연속적이지 않습니다.")
        theme_id = _text(item.get("themeId"), f"{path}.themeId")
        theme_name = _text(item.get("themeName"), f"{path}.themeName")
        source_url = _text(item.get("sourceUrl"), f"{path}.sourceUrl")
        assert theme_id is not None and theme_name is not None and source_url is not None
        if not theme_id.isdigit() or theme_id in seen_theme_ids:
            _fail("DUPLICATE_THEME", f"{path}.themeId", "theme ID가 중복되었거나 숫자가 아닙니다.")
        expected_url = f"https://infostock.co.kr/Theme/ThemeDB/{theme_id}"
        if source_url != expected_url:
            _fail("SOURCE_URL_CONFLICT", f"{path}.sourceUrl", "canonical theme URL이 아닙니다.")
        seen_theme_ids.add(theme_id)
        index_items.append(ThemeIndexItem(source_order, theme_id, theme_name, source_url))

    expected_count = _integer(
        manifest.get("requestedThemeCount"), "$.manifest.requestedThemeCount", minimum=1
    )
    if expected_count != 280 or len(index_items) != expected_count:
        _fail("PARTIAL_COLLECTION", "$.manifest", "280개 theme index가 모두 필요합니다.")
    if (
        manifest.get("completedThemeCount") != expected_count
        or manifest.get("failedThemeCount") != 0
        or manifest.get("failures") != []
    ):
        _fail("PARTIAL_COLLECTION", "$.manifest", "실패 없는 280/280 수집본이 필요합니다.")

    actual_theme_files = {
        match.group(1): path
        for path in root.glob("theme-*.json")
        if (match := THEME_FILE_RE.fullmatch(path.name))
    }
    if set(actual_theme_files) != seen_theme_ids:
        missing = sorted(seen_theme_ids - set(actual_theme_files), key=int)
        extra = sorted(set(actual_theme_files) - seen_theme_ids, key=int)
        _fail(
            "PARTIAL_COLLECTION",
            "$collection.themeFiles",
            f"index와 detail 파일이 다릅니다. missing={missing[:3]}, extra={extra[:3]}",
        )

    details: list[ThemeDetail] = []
    issues: list[QualityIssue] = []
    file_hashes: dict[str, str] = {
        "manifest.json": sha256_bytes(manifest_bytes),
        "theme-index.json": sha256_bytes(index_bytes),
    }
    stock_names: dict[str, set[str]] = defaultdict(set)
    history_count = 0
    related_count = 0
    leader_count = 0
    historical_membership_count = 0
    duplicate_count = 0
    missing_history_date_count = 0
    missing_history_content_count = 0
    missing_leader_code_count = 0
    missing_related_code_count = 0
    missing_historical_code_count = 0
    missing_historical_field_count = 0
    manifest_theme_rows = {
        str(row.get("themeId")): row
        for row in _sequence(manifest.get("themes"), "$.manifest.themes")
        if isinstance(row, dict)
    }

    for index_item in index_items:
        filename = f"theme-{index_item.source_theme_id}.json"
        raw_bytes, raw_text, payload = _read_json(actual_theme_files[index_item.source_theme_id], filename)
        file_hashes[filename] = sha256_bytes(raw_bytes)
        path = f"$.details[{index_item.source_theme_id}]"
        _validate_source_envelope(payload, source_type="theme_detail", path=path)
        theme_id = _text(payload.get("themeId"), f"{path}.themeId")
        theme_name = _text(payload.get("themeName"), f"{path}.themeName")
        description = _text(
            payload.get("description"), f"{path}.description", allow_empty=True
        )
        source_url = _text(payload.get("sourceUrl"), f"{path}.sourceUrl")
        assert theme_id is not None and theme_name is not None and description is not None
        assert source_url is not None
        if (
            theme_id != index_item.source_theme_id
            or theme_name != index_item.theme_name
            or source_url != index_item.source_url
        ):
            _fail("CONFLICTING_THEME", path, "index와 detail theme identity가 다릅니다.")
        if payload.get("historyComplete") is not True:
            _fail("INCOMPLETE_HISTORY", f"{path}.historyComplete", "전체 history 수집본이 아닙니다.")
        captured_at = _aware_datetime(payload.get("capturedAt"), f"{path}.capturedAt")
        raw_history = _sequence(payload.get("history"), f"{path}.history")
        raw_memberships = _sequence(payload.get("relatedStocks"), f"{path}.relatedStocks")
        hash_input = {
            "themeId": theme_id,
            "themeName": theme_name,
            "description": description,
            "history": raw_history,
            "relatedStocks": raw_memberships,
        }
        declared_detail_hash = _hash(payload.get("contentHash"), f"{path}.contentHash")
        recomputed_detail_hash = sha256_json(hash_input)
        source_hash_verified = recomputed_detail_hash == declared_detail_hash
        if not source_hash_verified:
            # A resumed legacy capture can contain categories/expansion and a
            # source hash produced by an untracked collector version. Preserve
            # both that declaration and the independently verified file hash.
            issues.append(
                QualityIssue(
                    "THEME_DATABASE",
                    "SOURCE_CONTENT_HASH_UNVERIFIABLE",
                    "WARNING",
                    "THEME_DETAIL_SNAPSHOT",
                    theme_id,
                    index_item.source_order,
                    {
                        "declaredContentHash": declared_detail_hash,
                        "recomputedTrackedShapeHash": recomputed_detail_hash,
                        "extraFields": sorted(
                            set(payload)
                            - {
                                "apiEndpoint",
                                "capturedAt",
                                "collectionAuthorization",
                                "contentHash",
                                "description",
                                "history",
                                "historyComplete",
                                "relatedStocks",
                                "schemaVersion",
                                "source",
                                "sourceType",
                                "sourceUrl",
                                "themeId",
                                "themeName",
                            }
                        ),
                    },
                )
            )
        snapshot = RawSnapshot(
            page_type="THEME_DETAIL",
            source_entity_id=theme_id,
            source_url=source_url,
            collected_at=captured_at,
            as_of=captured_at,
            raw_hash=file_hashes[filename],
            source_content_hash=declared_detail_hash,
            raw_payload_text=raw_text,
            raw_format="JSON",
            is_complete=True,
            quality_status=("OK" if source_hash_verified else "SOURCE_HASH_UNVERIFIED"),
        )

        fingerprint_counts: Counter[str] = Counter()
        history_seed: list[tuple[dict[str, Any], str]] = []
        for position, value in enumerate(raw_history):
            item = _mapping(value, f"{path}.history[{position}]")
            raw_content = str(item.get("content") or "").strip()
            parsed_date = _optional_date(item.get("date"), f"{path}.history[{position}].date")
            source_fingerprint = sha256_json(
                {
                    "content": raw_content,
                    "date": parsed_date.isoformat() if parsed_date else None,
                }
            )
            fingerprint_counts[source_fingerprint] += 1
            history_seed.append((item, source_fingerprint))
        fingerprint_seen: Counter[str] = Counter()
        source_key_seen: Counter[str] = Counter()
        histories: list[ThemeHistory] = []
        for position, (item, source_fingerprint) in enumerate(history_seed):
            item_path = f"{path}.history[{position}]"
            source_order = _integer(item.get("sourceOrder"), f"{item_path}.sourceOrder")
            if source_order != position:
                _fail("SOURCE_ORDER_CONFLICT", f"{item_path}.sourceOrder", "history sourceOrder가 연속적이지 않습니다.")
            event_date = _optional_date(item.get("date"), f"{item_path}.date")
            source_date = _text(
                item.get("sourceDate"), f"{item_path}.sourceDate", allow_empty=False, optional=True
            )
            raw_content = str(item.get("content") or "").strip()
            source_id = _text(
                item.get("sourceId"), f"{item_path}.sourceId", allow_empty=False, optional=True
            )
            base_key = f"source:{source_id}" if source_id else f"derived:{source_fingerprint}"
            occurrence = source_key_seen[base_key]
            source_key_seen[base_key] += 1
            source_key = base_key if occurrence == 0 else f"{base_key}:occurrence:{occurrence}"
            duplicate_occurrence = fingerprint_seen[source_fingerprint]
            fingerprint_seen[source_fingerprint] += 1
            if fingerprint_counts[source_fingerprint] > 1:
                if duplicate_occurrence == 0:
                    quality_status = "DUPLICATE_GROUP_HEAD"
                else:
                    quality_status = "SOURCE_DUPLICATE"
                    duplicate_count += 1
                    issues.append(
                        QualityIssue(
                            "THEME_DATABASE",
                            "SOURCE_DUPLICATE_HISTORY",
                            "WARNING",
                            "THEME_HISTORY",
                            f"{theme_id}/{source_key}",
                            source_order,
                            {
                                "eventDate": event_date.isoformat() if event_date else None,
                                "sourceFingerprint": source_fingerprint,
                                "duplicateOccurrence": duplicate_occurrence,
                            },
                        )
                    )
            elif event_date is None:
                quality_status = "DATE_MISSING"
            elif not raw_content:
                quality_status = "CONTENT_MISSING"
            else:
                quality_status = "OK"
            if event_date is None:
                missing_history_date_count += 1
                issues.append(
                    QualityIssue(
                        "THEME_DATABASE",
                        "HISTORY_DATE_MISSING",
                        "ERROR",
                        "THEME_HISTORY",
                        f"{theme_id}/{source_key}",
                        source_order,
                        {},
                    )
                )
            if not raw_content:
                missing_history_content_count += 1
                issues.append(
                    QualityIssue(
                        "THEME_DATABASE",
                        "HISTORY_CONTENT_MISSING",
                        "ERROR",
                        "THEME_HISTORY",
                        f"{theme_id}/{source_key}",
                        source_order,
                        {},
                    )
                )
            leaders = tuple(
                _reference(
                    value,
                    path=f"{item_path}.leaders[{reference_order}]",
                    default_order=reference_order,
                )
                for reference_order, value in enumerate(
                    _sequence(item.get("leaders"), f"{item_path}.leaders")
                )
            )
            member_values = item.get("memberStocks")
            if member_values is None:
                member_values = []
                missing_historical_field_count += 1
                issues.append(
                    QualityIssue(
                        "THEME_DATABASE",
                        "HISTORICAL_MEMBERSHIP_FIELD_MISSING",
                        "WARNING",
                        "THEME_HISTORY",
                        f"{theme_id}/{source_key}",
                        source_order,
                        {"messageKo": "legacy snapshot에 memberStocks field가 없습니다."},
                    )
                )
            member_stocks = tuple(
                _reference(
                    value,
                    path=f"{item_path}.memberStocks[{reference_order}]",
                    default_order=reference_order,
                )
                for reference_order, value in enumerate(
                    _sequence(member_values, f"{item_path}.memberStocks")
                )
            )
            for kind, references in (("LEADER", leaders), ("HISTORICAL_MEMBER", member_stocks)):
                for reference in references:
                    if reference.stock_code and STOCK_CODE_RE.fullmatch(reference.stock_code):
                        stock_names[reference.stock_code].add(reference.name)
                    if reference.quality_status != "OK":
                        if kind == "LEADER":
                            missing_leader_code_count += 1
                            issue_code = "LEADER_CODE_MISSING"
                        else:
                            missing_historical_code_count += 1
                            issue_code = "HISTORICAL_MEMBERSHIP_CODE_MISSING"
                        issues.append(
                            QualityIssue(
                                "THEME_DATABASE",
                                issue_code,
                                "WARNING",
                                kind,
                                f"{theme_id}/{source_key}/{reference.source_order}",
                                reference.source_order,
                                {
                                    "displayValue": reference.display_value,
                                    "qualityStatus": reference.quality_status,
                                },
                            )
                        )
            content_hash = sha256_json(
                {
                    "author": item.get("author"),
                    "chartFlag": item.get("chartFlag"),
                    "content": raw_content,
                    "createdAt": item.get("createdAt"),
                    "date": event_date.isoformat() if event_date else None,
                    "leaders": [
                        {
                            "name": ref.name,
                            "sourceOrder": ref.source_order,
                            "sourceUrl": ref.source_url,
                            "stockCode": ref.stock_code,
                        }
                        for ref in leaders
                    ],
                    "memberStocks": [
                        {
                            "name": ref.name,
                            "sourceOrder": ref.source_order,
                            "sourceUrl": ref.source_url,
                            "stockCode": ref.stock_code,
                        }
                        for ref in member_stocks
                    ],
                    "sourceDate": source_date,
                    "sourceHistoryKey": source_key,
                    "sourceId": source_id,
                    "updatedAt": item.get("updatedAt"),
                }
            )
            histories.append(
                ThemeHistory(
                    source_order=source_order,
                    source_history_id=source_id,
                    source_history_key=source_key,
                    event_date=event_date,
                    source_date=source_date,
                    source_created_at=_optional_datetime(
                        item.get("createdAt"), f"{item_path}.createdAt"
                    ),
                    source_updated_at=_optional_datetime(
                        item.get("updatedAt"), f"{item_path}.updatedAt"
                    ),
                    raw_text=raw_content,
                    direction=_direction(raw_content),
                    leaders=leaders,
                    member_stocks=member_stocks,
                    author=_text(
                        item.get("author"), f"{item_path}.author", optional=True
                    ),
                    chart_flag=_text(
                        item.get("chartFlag"), f"{item_path}.chartFlag", optional=True
                    ),
                    source_fingerprint=source_fingerprint,
                    quality_status=cast(Any, quality_status),
                    content_hash=content_hash,
                )
            )

        memberships: list[ThemeMembership] = []
        seen_membership_codes: set[str] = set()
        for position, value in enumerate(raw_memberships):
            item_path = f"{path}.relatedStocks[{position}]"
            item = _mapping(value, item_path)
            source_order = _integer(item.get("sourceOrder"), f"{item_path}.sourceOrder")
            if source_order != position:
                _fail("SOURCE_ORDER_CONFLICT", f"{item_path}.sourceOrder", "related stock sourceOrder가 연속적이지 않습니다.")
            stock_name = _text(item.get("name"), f"{item_path}.name")
            assert stock_name is not None
            raw_code = item.get("stockCode")
            stock_code = str(raw_code).strip() if raw_code not in {None, ""} else None
            if stock_code is None:
                membership_status: ReferenceQualityStatus = "SOURCE_CODE_MISSING"
            elif STOCK_CODE_RE.fullmatch(stock_code):
                membership_status = "OK"
            else:
                membership_status = "CODE_INVALID"
            if membership_status != "OK":
                missing_related_code_count += 1
                issues.append(
                    QualityIssue(
                        "THEME_DATABASE",
                        "RELATED_STOCK_CODE_MISSING",
                        "ERROR",
                        "CURRENT_MEMBERSHIP",
                        f"{theme_id}/{source_order}",
                        source_order,
                        {"stockName": stock_name, "qualityStatus": membership_status},
                    )
                )
            elif stock_code is not None:
                if stock_code in seen_membership_codes:
                    _fail("DUPLICATE_RELATED_STOCK", item_path, "동일 theme의 current stock code가 중복됩니다.")
                seen_membership_codes.add(stock_code)
                stock_names[stock_code].add(stock_name)
            rationale = _text(
                item.get("rationale"), f"{item_path}.rationale", allow_empty=True
            )
            assert rationale is not None
            source_index = _text(
                item.get("sourceIndex"), f"{item_path}.sourceIndex", optional=True
            )
            membership_hash = sha256_json(
                {
                    "rationale": rationale,
                    "sourceIndex": source_index,
                    "sourceOrder": source_order,
                    "stockCode": stock_code,
                    "stockName": stock_name,
                }
            )
            memberships.append(
                ThemeMembership(
                    source_order,
                    stock_code,
                    stock_name,
                    rationale,
                    source_index,
                    membership_hash,
                    membership_status,
                )
            )

        manifest_row = manifest_theme_rows.get(theme_id)
        if not isinstance(manifest_row, dict):
            _fail("MANIFEST_THEME_MISSING", path, "manifest theme record가 없습니다.")
        if (
            manifest_row.get("themeName") != theme_name
            or manifest_row.get("historyCount") != len(histories)
            or manifest_row.get("relatedStockCount") != len(memberships)
            or manifest_row.get("contentHash") != declared_detail_hash
        ):
            _fail("MANIFEST_THEME_CONFLICT", path, "manifest와 detail 집계가 다릅니다.")
        history_count += len(histories)
        related_count += len(memberships)
        leader_count += sum(len(item.leaders) for item in histories)
        historical_membership_count += sum(len(item.member_stocks) for item in histories)
        details.append(
            ThemeDetail(
                source_theme_id=theme_id,
                theme_name=theme_name,
                description=description,
                theme_revision_hash=sha256_json(
                    {"description": description, "themeName": theme_name}
                ),
                history=tuple(histories),
                memberships=tuple(memberships),
                snapshot=snapshot,
            )
        )

    for stock_code, names in sorted(stock_names.items()):
        if len(names) <= 1:
            continue
        issues.append(
            QualityIssue(
                "THEME_DATABASE",
                "STOCK_NAME_VARIANT",
                "INFO",
                "STOCK",
                stock_code,
                None,
                {"names": sorted(names), "variantCount": len(names)},
            )
        )
    stock_name_variant_count = sum(len(names) > 1 for names in stock_names.values())

    declared_quality = _mapping(manifest.get("quality"), "$.manifest.quality")
    expected_manifest_values = {
        "historyCount": history_count,
        "relatedStockCount": related_count,
    }
    for field, actual in expected_manifest_values.items():
        if manifest.get(field) != actual:
            _fail("MANIFEST_COUNT_MISMATCH", f"$.manifest.{field}", f"재계산 값 {actual:,}건과 다릅니다.")
    expected_quality_values = {
        "duplicateHistoryCount": duplicate_count,
        "missingHistoryDateCount": missing_history_date_count,
        "missingHistoryContentCount": missing_history_content_count,
        "missingLeaderCodeCount": missing_leader_code_count,
        "missingRelatedStockCodeCount": missing_related_code_count,
    }
    for field, actual in expected_quality_values.items():
        if declared_quality.get(field) != actual:
            _fail("MANIFEST_QUALITY_MISMATCH", f"$.manifest.quality.{field}", f"재계산 값 {actual:,}건과 다릅니다.")

    summary = QualitySummary(
        theme_count=len(details),
        history_count=history_count,
        related_stock_count=related_count,
        leader_count=leader_count,
        historical_membership_count=historical_membership_count,
        duplicate_history_count=duplicate_count,
        missing_history_date_count=missing_history_date_count,
        missing_history_content_count=missing_history_content_count,
        missing_leader_code_count=missing_leader_code_count,
        missing_related_stock_code_count=missing_related_code_count,
        missing_historical_membership_code_count=missing_historical_code_count,
        missing_historical_membership_field_count=missing_historical_field_count,
        stock_name_variant_count=stock_name_variant_count,
    )
    manifest_finished = _aware_datetime(manifest.get("finishedAt"), "$.manifest.finishedAt")
    manifest_snapshot = RawSnapshot(
        page_type="IMPORT_MANIFEST",
        source_entity_id=str(manifest.get("dataset")),
        source_url=str(manifest.get("apiBaseUrl")),
        collected_at=manifest_finished,
        as_of=manifest_finished,
        raw_hash=file_hashes["manifest.json"],
        source_content_hash=None,
        raw_payload_text=manifest_text,
        raw_format="JSON",
        is_complete=True,
    )
    index_snapshot = RawSnapshot(
        page_type="THEME_LIST",
        source_entity_id=None,
        source_url=str(index.get("sourceUrl")),
        collected_at=index_captured,
        as_of=index_captured,
        raw_hash=file_hashes["theme-index.json"],
        source_content_hash=declared_index_hash,
        raw_payload_text=index_text,
        raw_format="JSON",
        is_complete=True,
    )

    daily_path = root / "daily-featured-theme-page-1.json"
    if daily_backfill_directory is not None:
        daily, daily_file_hashes = load_daily_api_backfill(
            daily_backfill_directory
        )
        file_hashes.update(daily_file_hashes)
    elif daily_path.is_file() and not daily_path.is_symlink():
        daily_bytes, daily_text, daily_payload = _read_json(daily_path, "$.daily.page1")
        file_hashes[daily_path.name] = sha256_bytes(daily_bytes)
        daily = parse_legacy_daily_payload(
            daily_payload, raw_text=daily_text, parser_version=PARSER_VERSION
        )
    else:
        daily = _empty_daily()

    dataset_hash = sha256_json(
        [{"filename": filename, "rawHash": file_hashes[filename]} for filename in sorted(file_hashes)]
    )
    input_hash = sha256_json(
        {
            "datasetHash": dataset_hash,
            "parserVersion": PARSER_VERSION,
            "rightsScope": "LOCAL_AUDITED_IMPORT",
        }
    )
    return ImportBundle(
        fixture_version="1.0.0",
        dataset="infostock-full-sync-with-daily",
        source_provider="INFOSTOCK",
        rights_scope="LOCAL_AUDITED_IMPORT",
        parser_version=PARSER_VERSION,
        expected_theme_count=expected_count,
        input_hash=input_hash,
        dataset_hash=dataset_hash,
        manifest_snapshot=manifest_snapshot,
        index_snapshot=index_snapshot,
        index_items=tuple(index_items),
        details=tuple(details),
        quality_summary=summary,
        quality_issues=tuple(issues),
        daily=daily,
    )


def machine_quality_report(bundle: ImportBundle) -> dict[str, object]:
    """Return a stable machine-readable audit without local filesystem paths."""

    quality = bundle.quality_summary
    daily = bundle.daily
    issue_counts = Counter(issue.issue_code for issue in (*bundle.quality_issues, *daily.quality_issues))
    return {
        "schemaVersion": "1.0.0",
        "dataset": bundle.dataset,
        "datasetHash": bundle.dataset_hash,
        "inputHash": bundle.input_hash,
        "parserVersion": bundle.parser_version,
        "rightsScope": bundle.rights_scope,
        "components": {
            "themeDatabase": {
                "status": "COMPLETE",
                "themes": quality.theme_count,
                "historyRows": quality.history_count,
                "relatedStocks": quality.related_stock_count,
                "leaders": quality.leader_count,
                "historicalMemberships": quality.historical_membership_count,
                "quality": {
                    "duplicateHistoryRows": quality.duplicate_history_count,
                    "missingHistoryDates": quality.missing_history_date_count,
                    "missingHistoryContent": quality.missing_history_content_count,
                    "missingLeaderCodes": quality.missing_leader_code_count,
                    "missingRelatedStockCodes": quality.missing_related_stock_code_count,
                    "missingHistoricalMembershipCodes": quality.missing_historical_membership_code_count,
                    "missingHistoricalMembershipFields": quality.missing_historical_membership_field_count,
                    "stockCodesWithNameVariants": quality.stock_name_variant_count,
                },
                "rawSnapshots": 2 + len(bundle.details),
            },
            "dailyFeaturedTheme": {
                "status": daily.component_status,
                "blockers": list(daily.blockers),
                "capturedPages": [daily.first_page, daily.last_page],
                "nextPage": daily.next_page,
                "coverageComplete": daily.coverage_complete,
                "listEntries": len(daily.entries),
                "posts": len(daily.posts),
                "bodies": daily.body_count,
                "relations": daily.relation_count,
                "rawSnapshots": len(daily.pages),
                "earliestDate": daily.earliest_date.isoformat() if daily.earliest_date else None,
                "latestDate": daily.latest_date.isoformat() if daily.latest_date else None,
            },
        },
        "qualityIssueCounts": dict(sorted(issue_counts.items())),
        "overallStatus": "PARTIAL" if daily.component_status != "COMPLETE" else "SUCCEEDED",
    }


def human_quality_report(bundle: ImportBundle) -> str:
    report = machine_quality_report(bundle)
    daily = cast(dict[str, object], cast(dict[str, object], report["components"])["dailyFeaturedTheme"])
    quality = bundle.quality_summary
    if bundle.daily.component_status == "COMPLETE":
        daily_lines = (
            f"- DailyFeaturedTheme: {daily['status']}",
            f"  - 확보 목록: {len(bundle.daily.entries):,}건, 본문: {bundle.daily.body_count:,}건, 관계: {bundle.daily.relation_count:,}건",
            f"  - pagination: {bundle.daily.first_page}~{bundle.daily.last_page} page, 전체 기간 완료",
            f"  - 기간: {bundle.daily.earliest_date}~{bundle.daily.latest_date}",
            "",
            "Daily 실제 전체 backfill과 Theme DB 적재가 모두 완료됐습니다.",
            "",
        )
    else:
        blocker_text = ", ".join(bundle.daily.blockers) or "없음(누락·수집·파싱 오류 확인 필요)"
        if bundle.daily.first_page == bundle.daily.last_page:
            pagination_text = (
                f"  - pagination: {bundle.daily.first_page}페이지만 확보, "
                f"next={bundle.daily.next_page}, 전체 기간 미완료"
            )
        else:
            pagination_text = (
                f"  - pagination: {bundle.daily.first_page}~{bundle.daily.last_page} page, "
                f"next={bundle.daily.next_page}, 전체 기간 미완료"
            )
        daily_lines = (
            f"- DailyFeaturedTheme: {daily['status']}",
            f"  - 확보 목록: {len(bundle.daily.entries):,}건, 본문: {bundle.daily.body_count:,}건, 관계: {bundle.daily.relation_count:,}건",
            pagination_text,
            f"  - blocker: {blocker_text}",
            "",
            "Daily 실제 전체 backfill이 완료되지 않았으므로 S1 전체 DB 상태는 PARTIAL입니다.",
            "",
        )
    return "\n".join(
        (
            "# Infostock 기존 수집본 품질 보고",
            "",
            f"- 전체 상태: {report['overallStatus']}",
            f"- dataset hash: `{bundle.dataset_hash}`",
            f"- parser version: `{bundle.parser_version}`",
            "- Theme DB: COMPLETE",
            f"  - theme: {quality.theme_count:,}/280",
            f"  - history: {quality.history_count:,}건",
            f"  - current related stock: {quality.related_stock_count:,}건",
            f"  - leader: {quality.leader_count:,}건",
            f"  - historical membership: {quality.historical_membership_count:,}건",
            f"  - 원본 history 중복: {quality.duplicate_history_count:,}건(보존)",
            f"  - leader code 누락: {quality.missing_leader_code_count:,}건(보존)",
            f"  - historical membership code 누락: {quality.missing_historical_membership_code_count:,}건(보존)",
            f"  - legacy history의 memberStocks field 누락: {quality.missing_historical_membership_field_count:,}건",
            *daily_lines,
        )
    )
