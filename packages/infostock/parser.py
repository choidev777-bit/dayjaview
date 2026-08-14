"""Strict parser for the committed synthetic collector-response fixture."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Never, cast

from .errors import FixtureValidationError
from .hashing import canonical_json, fixture_bundle_hash, sha256_json, sha256_text
from .models import (
    DailyBackfill,
    Direction,
    ImportBundle,
    QualityIssue,
    QualitySummary,
    RawSnapshot,
    StockReference,
    ThemeDetail,
    ThemeHistory,
    ThemeIndexItem,
    ThemeMembership,
)
from .policy import CommittedFixturePolicy, InfostockAccessPolicy

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STOCK_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
STOCK_PAIR_RE = re.compile(r"^([0-9A-Z]{6})-(.+)$")
THEME_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
KST = timezone(timedelta(hours=9))


def _fail(code: str, path: str, detail: str) -> Never:
    raise FixtureValidationError(code, path, detail)


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("MALFORMED_FIXTURE", path, "JSON object가 필요합니다.")
    return cast(dict[str, Any], value)


def _sequence(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("MALFORMED_FIXTURE", path, "JSON array가 필요합니다.")
    return cast(list[Any], value)


def _text(value: object, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail("MALFORMED_FIXTURE", path, "문자열이 필요합니다.")
    result = value.strip()
    if not allow_empty and not result:
        _fail("MALFORMED_FIXTURE", path, "빈 문자열은 허용되지 않습니다.")
    return result


def _source_identifier(value: object, path: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        _fail("MALFORMED_FIXTURE", path, "문자열 또는 정수 source ID가 필요합니다.")
    return _text(str(value), path)


def _aware_datetime(value: object, path: str) -> datetime:
    text = _text(value, path)
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        _fail("MALFORMED_FIXTURE", path, "ISO 8601 시각이 필요합니다.")
    if result.tzinfo is None or result.utcoffset() is None:
        _fail("MALFORMED_FIXTURE", path, "timezone이 있는 시각이 필요합니다.")
    return result


def _source_timestamp(value: object, path: str) -> datetime | None:
    if value in {None, ""}:
        return None
    text = _text(str(value), path)
    if not re.fullmatch(r"\d{14}", text):
        _fail("MALFORMED_FIXTURE", path, "YYYYMMDDhhmmss 시각이 필요합니다.")
    try:
        return datetime.fromisoformat(
            f"{text[:4]}-{text[4:6]}-{text[6:8]}T"
            f"{text[8:10]}:{text[10:12]}:{text[12:14]}+09:00"
        ).astimezone(KST)
    except ValueError:
        _fail("MALFORMED_FIXTURE", path, "유효하지 않은 source 시각입니다.")


def _event_date(value: object, path: str) -> date:
    text = _text(str(value), path)
    if not re.fullmatch(r"\d{8}", text):
        _fail("MALFORMED_FIXTURE", path, "YYYYMMDD 날짜가 필요합니다.")
    try:
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    except ValueError:
        _fail("MALFORMED_FIXTURE", path, "유효하지 않은 날짜입니다.")


def _hash(value: object, path: str) -> str:
    result = _text(value, path)
    if not SHA256_RE.fullmatch(result):
        _fail("MALFORMED_FIXTURE", path, "소문자 SHA-256 hex가 필요합니다.")
    return result


def _parse_snapshot(
    value: object,
    *,
    path: str,
    page_type: str,
    source_entity_id: str | None,
) -> tuple[RawSnapshot, dict[str, Any]]:
    snapshot = _mapping(value, path)
    raw_payload = _mapping(snapshot.get("rawPayload"), f"{path}.rawPayload")
    raw_hash = _hash(snapshot.get("rawHash"), f"{path}.rawHash")
    raw_text = canonical_json(raw_payload)
    if sha256_text(raw_text) != raw_hash:
        _fail("HASH_MISMATCH", f"{path}.rawHash", "rawPayload canonical SHA-256과 다릅니다.")
    if snapshot.get("isComplete") is not True:
        _fail(
            "INCOMPLETE_SNAPSHOT",
            f"{path}.isComplete",
            "partial snapshot은 현재 membership을 종료할 근거가 아닙니다.",
        )
    collected_at = _aware_datetime(snapshot.get("collectedAt"), f"{path}.collectedAt")
    return (
        RawSnapshot(
            page_type=cast(Any, page_type),
            source_entity_id=source_entity_id,
            source_url=_text(snapshot.get("sourceUrl"), f"{path}.sourceUrl"),
            collected_at=collected_at,
            as_of=collected_at,
            raw_hash=raw_hash,
            source_content_hash=raw_hash,
            raw_payload_text=raw_text,
            raw_format="JSON",
            is_complete=True,
        ),
        raw_payload,
    )


def _success_data(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    if raw.get("success") is not True:
        _fail("MALFORMED_FIXTURE", f"{path}.rawPayload.success", "true가 필요합니다.")
    return _mapping(raw.get("data"), f"{path}.rawPayload.data")


def _direction(raw_text: str) -> Direction:
    up = any(token in raw_text for token in ("상승", "강세", "급등"))
    down = any(token in raw_text for token in ("하락", "약세", "급락"))
    if up and down:
        return "MIXED"
    if up:
        return "UP"
    if down:
        return "DOWN"
    return "UNKNOWN"


def _stock_pairs(value: object, path: str) -> tuple[StockReference, ...]:
    if value in {None, ""}:
        return ()
    if not isinstance(value, str):
        _fail("MALFORMED_FIXTURE", path, "종목 basket 문자열이 필요합니다.")
    result: list[StockReference] = []
    for source_order, part in enumerate(value.split("|")):
        display = part.strip()
        if not display:
            continue
        match = STOCK_PAIR_RE.fullmatch(display)
        if match:
            code, name = match.groups()
            status = "OK"
            source_url = f"https://new.infostock.co.kr/stockitem?code={code}"
        else:
            code, name = None, display
            status = "SOURCE_CODE_MISSING"
            source_url = None
        result.append(
            StockReference(
                source_order=source_order,
                name=" ".join(name.split()),
                stock_code=code,
                source_url=source_url,
                display_value=display,
                quality_status=cast(Any, status),
            )
        )
    return tuple(result)


def _blocked_daily() -> DailyBackfill:
    issues = (
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "B-INFOSTOCK-AUTH",
            "BLOCKER",
            "BACKFILL",
            None,
            None,
            {"messageKo": "synthetic theme fixture에는 실제 Daily backfill이 없습니다."},
        ),
        QualityIssue(
            "DAILY_FEATURED_THEME",
            "B-DATA-RIGHTS",
            "BLOCKER",
            "BACKFILL",
            None,
            None,
            {"messageKo": "Daily production 권리 증거가 없습니다."},
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


def parse_fixture_payload(
    payload: Mapping[str, Any], *, source_text: str | None = None
) -> ImportBundle:
    fixture_version = _text(payload.get("fixtureVersion"), "$.fixtureVersion")
    dataset = _text(payload.get("dataset"), "$.dataset")
    source_provider = _text(payload.get("source"), "$.source")
    if source_provider != "INFOSTOCK":
        _fail("MALFORMED_FIXTURE", "$.source", "INFOSTOCK만 지원합니다.")
    rights_scope = _text(payload.get("rightsScope"), "$.rightsScope")
    InfostockAccessPolicy.require_import_scope(rights_scope)
    parser_version = _text(payload.get("parserVersion"), "$.parserVersion")
    expected_theme_count = payload.get("expectedThemeCount")
    if (
        isinstance(expected_theme_count, bool)
        or not isinstance(expected_theme_count, int)
        or expected_theme_count <= 0
    ):
        _fail("MALFORMED_FIXTURE", "$.expectedThemeCount", "1 이상의 정수가 필요합니다.")
    declared_hash = _hash(payload.get("bundleHash"), "$.bundleHash")
    actual_hash = fixture_bundle_hash(payload)
    if declared_hash != actual_hash:
        _fail("HASH_MISMATCH", "$.bundleHash", "fixture bundle SHA-256과 다릅니다.")

    index_snapshot, index_raw = _parse_snapshot(
        payload.get("indexSnapshot"),
        path="$.indexSnapshot",
        page_type="THEME_LIST",
        source_entity_id=None,
    )
    if index_snapshot.source_url != "https://infostock.co.kr/Theme/ThemeDB/ThemeAll":
        _fail("CONFLICTING_THEME", "$.indexSnapshot.sourceUrl", "목록 URL이 다릅니다.")
    index_values = _sequence(
        _success_data(index_raw, "$.indexSnapshot").get("items"),
        "$.indexSnapshot.rawPayload.data.items",
    )
    index_items: list[ThemeIndexItem] = []
    seen_ids: set[str] = set()
    for position, value in enumerate(index_values):
        path = f"$.indexSnapshot.rawPayload.data.items[{position}]"
        item = _mapping(value, path)
        theme_id = _source_identifier(item.get("code"), f"{path}.code")
        if not THEME_ID_RE.fullmatch(theme_id) or theme_id in seen_ids:
            _fail("DUPLICATE_THEME", f"{path}.code", "theme ID가 중복되었거나 안전하지 않습니다.")
        seen_ids.add(theme_id)
        theme_name = " ".join(_text(item.get("name"), f"{path}.name").split())
        index_items.append(
            ThemeIndexItem(
                source_order=position,
                source_theme_id=theme_id,
                theme_name=theme_name,
                source_url=f"https://infostock.co.kr/Theme/ThemeDB/{theme_id}",
            )
        )
    raw_details = _sequence(payload.get("detailSnapshots"), "$.detailSnapshots")
    if len(index_items) != expected_theme_count or len(raw_details) != expected_theme_count:
        _fail(
            "PARTIAL_FIXTURE",
            "$",
            "expectedThemeCount, index, detail 건수가 모두 같아야 합니다.",
        )
    detail_values: dict[str, tuple[int, object]] = {}
    for position, value in enumerate(raw_details):
        outer = _mapping(value, f"$.detailSnapshots[{position}]")
        theme_id = _text(outer.get("sourceThemeId"), f"$.detailSnapshots[{position}].sourceThemeId")
        if theme_id in detail_values:
            _fail("DUPLICATE_THEME", f"$.detailSnapshots[{position}]", "detail theme ID가 중복됩니다.")
        detail_values[theme_id] = (position, value)
    if set(detail_values) != seen_ids:
        _fail("PARTIAL_FIXTURE", "$.detailSnapshots", "index/detail theme ID 집합이 다릅니다.")

    details: list[ThemeDetail] = []
    issues: list[QualityIssue] = []
    stock_names: dict[str, set[str]] = defaultdict(set)
    leader_count = 0
    historical_membership_count = 0
    missing_leaders = 0
    missing_historical = 0
    duplicate_count = 0
    for index_item in index_items:
        position, raw_value = detail_values[index_item.source_theme_id]
        path = f"$.detailSnapshots[{position}]"
        snapshot, raw_payload = _parse_snapshot(
            raw_value,
            path=path,
            page_type="THEME_DETAIL",
            source_entity_id=index_item.source_theme_id,
        )
        data = _success_data(raw_payload, path)
        theme = _mapping(data.get("theme"), f"{path}.rawPayload.data.theme")
        theme_id = _source_identifier(theme.get("code"), f"{path}.rawPayload.data.theme.code")
        theme_name = " ".join(
            _text(theme.get("name"), f"{path}.rawPayload.data.theme.name").split()
        )
        if theme_id != index_item.source_theme_id or theme_name != index_item.theme_name:
            _fail("CONFLICTING_THEME", path, "index와 detail identity가 다릅니다.")
        if snapshot.source_url != index_item.source_url:
            _fail("CONFLICTING_THEME", f"{path}.sourceUrl", "theme URL이 다릅니다.")
        description = _text(
            theme.get("outline"), f"{path}.rawPayload.data.theme.outline", allow_empty=True
        )
        history_values = _sequence(data.get("items"), f"{path}.rawPayload.data.items")
        fingerprint_total: Counter[str] = Counter()
        seeds: list[tuple[dict[str, Any], date, str, str]] = []
        for history_position, value in enumerate(history_values):
            item = _mapping(value, f"{path}.rawPayload.data.items[{history_position}]")
            event_date = _event_date(item.get("showDate"), f"{path}.items[{history_position}].showDate")
            raw_text = _text(item.get("content"), f"{path}.items[{history_position}].content")
            fingerprint = sha256_json({"content": raw_text, "date": event_date.isoformat()})
            fingerprint_total[fingerprint] += 1
            seeds.append((item, event_date, raw_text, fingerprint))
        fingerprint_seen: Counter[str] = Counter()
        source_seen: Counter[str] = Counter()
        histories: list[ThemeHistory] = []
        for history_position, (item, event_date, raw_text, fingerprint) in enumerate(seeds):
            source_id_value = item.get("B2Bseq")
            source_id = (
                _source_identifier(source_id_value, f"{path}.items[{history_position}].B2Bseq")
                if source_id_value not in {None, ""}
                else None
            )
            base_key = f"source:{source_id}" if source_id else f"derived:{fingerprint}"
            occurrence = source_seen[base_key]
            source_seen[base_key] += 1
            source_key = base_key if occurrence == 0 else f"{base_key}:occurrence:{occurrence}"
            duplicate_occurrence = fingerprint_seen[fingerprint]
            fingerprint_seen[fingerprint] += 1
            if fingerprint_total[fingerprint] > 1:
                quality_status = (
                    "DUPLICATE_GROUP_HEAD" if duplicate_occurrence == 0 else "SOURCE_DUPLICATE"
                )
                if duplicate_occurrence:
                    duplicate_count += 1
            else:
                quality_status = "OK"
            leaders = _stock_pairs(item.get("LEAD_STOCK"), f"{path}.items[{history_position}].LEAD_STOCK")
            members = _stock_pairs(item.get("STOCKS"), f"{path}.items[{history_position}].STOCKS")
            for kind, references in (("LEADER", leaders), ("HISTORICAL_MEMBER", members)):
                for reference in references:
                    if reference.stock_code:
                        stock_names[reference.stock_code].add(reference.name)
                    else:
                        if kind == "LEADER":
                            missing_leaders += 1
                            issue_code = "LEADER_CODE_MISSING"
                        else:
                            missing_historical += 1
                            issue_code = "HISTORICAL_MEMBERSHIP_CODE_MISSING"
                        issues.append(
                            QualityIssue(
                                "THEME_DATABASE",
                                issue_code,
                                "WARNING",
                                kind,
                                f"{theme_id}/{source_key}/{reference.source_order}",
                                reference.source_order,
                                {"displayValue": reference.display_value},
                            )
                        )
            history_hash = sha256_json(
                {
                    "author": item.get("CREATE_WRITER"),
                    "chartFlag": item.get("CHART"),
                    "content": raw_text,
                    "createdAt": item.get("createTime"),
                    "date": event_date.isoformat(),
                    "leaders": [
                        {"name": ref.name, "sourceOrder": ref.source_order, "stockCode": ref.stock_code}
                        for ref in leaders
                    ],
                    "memberStocks": [
                        {"name": ref.name, "sourceOrder": ref.source_order, "stockCode": ref.stock_code}
                        for ref in members
                    ],
                    "sourceHistoryKey": source_key,
                    "updatedAt": item.get("lastUpdateTime"),
                }
            )
            histories.append(
                ThemeHistory(
                    source_order=history_position,
                    source_history_id=source_id,
                    source_history_key=source_key,
                    event_date=event_date,
                    source_date=str(item.get("showDate")),
                    source_created_at=_source_timestamp(
                        item.get("createTime"), f"{path}.items[{history_position}].createTime"
                    ),
                    source_updated_at=_source_timestamp(
                        item.get("lastUpdateTime"), f"{path}.items[{history_position}].lastUpdateTime"
                    ),
                    raw_text=raw_text,
                    direction=_direction(raw_text),
                    leaders=leaders,
                    member_stocks=members,
                    author=(str(item.get("CREATE_WRITER")).strip() if item.get("CREATE_WRITER") else None),
                    chart_flag=(str(item.get("CHART")).strip() if item.get("CHART") else None),
                    source_fingerprint=fingerprint,
                    quality_status=cast(Any, quality_status),
                    content_hash=history_hash,
                )
            )
        membership_values = _sequence(data.get("stockItems"), f"{path}.rawPayload.data.stockItems")
        memberships: list[ThemeMembership] = []
        seen_codes: set[str] = set()
        for membership_position, value in enumerate(membership_values):
            item = _mapping(value, f"{path}.stockItems[{membership_position}]")
            code = _text(item.get("code"), f"{path}.stockItems[{membership_position}].code")
            if not STOCK_CODE_RE.fullmatch(code):
                _fail("MALFORMED_FIXTURE", f"{path}.stockItems[{membership_position}].code", "6자리 source stock code가 필요합니다.")
            if code in seen_codes:
                _fail("DUPLICATE_MEMBERSHIP", path, "동일 theme의 current membership code가 중복됩니다.")
            seen_codes.add(code)
            name = " ".join(_text(item.get("name"), f"{path}.stockItems[{membership_position}].name").split())
            rationale = _text(
                item.get("outline"), f"{path}.stockItems[{membership_position}].outline", allow_empty=True
            )
            source_index = str(item.get("index")).strip() if item.get("index") not in {None, ""} else None
            stock_names[code].add(name)
            membership_hash = sha256_json(
                {
                    "rationale": rationale,
                    "sourceIndex": source_index,
                    "sourceOrder": membership_position,
                    "stockCode": code,
                    "stockName": name,
                }
            )
            memberships.append(
                ThemeMembership(
                    membership_position,
                    code,
                    name,
                    rationale,
                    source_index,
                    membership_hash,
                )
            )
        leader_count += sum(len(history.leaders) for history in histories)
        historical_membership_count += sum(len(history.member_stocks) for history in histories)
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

    for code, names in stock_names.items():
        if len(names) > 1:
            issues.append(
                QualityIssue(
                    "THEME_DATABASE",
                    "STOCK_NAME_VARIANT",
                    "INFO",
                    "STOCK",
                    code,
                    None,
                    {"names": sorted(names)},
                )
            )
    manifest_text = source_text if source_text is not None else canonical_json(payload)
    manifest_snapshot = RawSnapshot(
        page_type="IMPORT_MANIFEST",
        source_entity_id=dataset,
        source_url=f"urn:dayjaview:infostock:{dataset}",
        collected_at=index_snapshot.collected_at,
        as_of=index_snapshot.as_of,
        raw_hash=sha256_text(manifest_text),
        source_content_hash=actual_hash,
        raw_payload_text=manifest_text,
        raw_format="JSON",
        is_complete=True,
    )
    summary = QualitySummary(
        theme_count=len(details),
        history_count=sum(len(detail.history) for detail in details),
        related_stock_count=sum(len(detail.memberships) for detail in details),
        leader_count=leader_count,
        historical_membership_count=historical_membership_count,
        duplicate_history_count=duplicate_count,
        missing_history_date_count=0,
        missing_history_content_count=0,
        missing_leader_code_count=missing_leaders,
        missing_related_stock_code_count=0,
        missing_historical_membership_code_count=missing_historical,
        stock_name_variant_count=sum(len(names) > 1 for names in stock_names.values()),
    )
    return ImportBundle(
        fixture_version=fixture_version,
        dataset=dataset,
        source_provider=source_provider,
        rights_scope=rights_scope,
        parser_version=parser_version,
        expected_theme_count=cast(int, expected_theme_count),
        input_hash=actual_hash,
        dataset_hash=actual_hash,
        manifest_snapshot=manifest_snapshot,
        index_snapshot=index_snapshot,
        index_items=tuple(index_items),
        details=tuple(details),
        quality_summary=summary,
        quality_issues=tuple(issues),
        daily=_blocked_daily(),
    )


def load_committed_fixture(path: Path, policy: CommittedFixturePolicy) -> ImportBundle:
    approved_path = policy.validate(path)
    try:
        raw_text = approved_path.read_bytes().decode("utf-8")
        raw = json.loads(raw_text)
    except OSError as exc:
        raise FixtureValidationError(
            "FIXTURE_READ_FAILED", "$fixture", "승인된 fixture를 읽지 못했습니다."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureValidationError(
            "MALFORMED_FIXTURE", "$fixture", "유효한 UTF-8 JSON이 아닙니다."
        ) from exc
    return parse_fixture_payload(_mapping(raw, "$"), source_text=raw_text)
