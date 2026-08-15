"""DailyFeaturedTheme 일일 증분 수집(D-14): 최근 구간만 받아 같은 저장 모델에 얹는다.

S1 전체 backfill과 같은 schema·revision·lineage를 재사용한다. 과거 전체는
재수집하지 않고 lookback 구간(기본 며칠)만 다시 관측한다 — 같은 게시물의
재관측은 revision 규칙이 흡수하고(내용이 같으면 last_seen만 갱신), 구간 안에서
사라진 게시물은 NOT_VISIBLE revision으로 남는다(수정·삭제 감지).

로그인 세션 설계(과거 미확정 항목의 결론): Daily API
(`api.infostock.co.kr:9081/web/flash/*`)는 무인증 공개 endpoint다 — S1의 과거
전체 4,655건이 인증 헤더 없는 `_default_transport`로 수집된 것이 실측 근거다.
따라서 자동 로그인을 만들지 않는다. 원천이 인증을 요구하기 시작하면(HTTP
401/403) 수집을 멈추고 AUTH_REQUIRED 상태로 보고한다. 재인증은 운영자 수동
절차이고 인증 UI를 public port로 노출하지 않는다(PRD FR-10).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

from .daily_api import DAILY_API_PARSER_VERSION, load_daily_api_backfill
from .hashing import sha256_json
from .importer import ImportResult, _result
from .models import ImportBundle, QualitySummary
from .policy import InfostockAccessPolicy
from .store import ApplyCounts, InfostockStore

INCREMENT_DATASET = "infostock-daily-featured-theme-increment"

# 운영자 상태 어휘(apps/api/operator_boundary.py의 _SERVICE_STATUSES 부분집합).
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_PARTIAL = "PARTIAL"
STATUS_AUTH_REQUIRED = "AUTH_REQUIRED"
STATUS_RATE_LIMITED = "RATE_LIMITED"
STATUS_FAILED = "FAILED"


def classify_collection_error(error: BaseException) -> str:
    """수집 실패를 운영자 상태로 분류한다. 인증 요구는 조용히 지나가지 않는다."""

    current: BaseException | None = error
    while current is not None:
        if isinstance(current, HTTPError):
            if current.code in (401, 403):
                return STATUS_AUTH_REQUIRED
            if current.code == 429:
                return STATUS_RATE_LIMITED
        current = current.__cause__
    return STATUS_FAILED


def build_daily_increment_bundle(
    directory: Path,
) -> tuple[ImportBundle, tuple[date, date]]:
    """수집 디렉터리를 검증해 증분 ImportBundle과 수집 구간을 만든다.

    theme 컴포넌트는 이 run의 범위가 아니므로 비어 있고(details·index 없음),
    manifest snapshot은 Daily manifest 그대로다. input_hash가 수집 내용에
    결정적이라 같은 수집분의 재적재는 저장소에서 reused로 끝난다.
    """

    daily, file_hashes = load_daily_api_backfill(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    start_raw = str(manifest.get("startDate") or "")
    end_raw = str(manifest.get("endDate") or "")
    window = (
        date(int(start_raw[:4]), int(start_raw[4:6]), int(start_raw[6:8])),
        date(int(end_raw[:4]), int(end_raw[4:6]), int(end_raw[6:8])),
    )
    dataset_hash = sha256_json(
        [
            {"filename": filename, "rawHash": file_hashes[filename]}
            for filename in sorted(file_hashes)
        ]
    )
    input_hash = sha256_json(
        {
            "dataset": INCREMENT_DATASET,
            "datasetHash": dataset_hash,
            "parserVersion": DAILY_API_PARSER_VERSION,
            "rightsScope": "LOCAL_AUDITED_IMPORT",
            "window": [start_raw, end_raw],
        }
    )
    manifest_snapshot = daily.pages[0]
    if manifest_snapshot.page_type != "DAILY_MANIFEST":
        raise RuntimeError("Daily 수집분의 첫 snapshot은 manifest여야 합니다.")
    bundle = ImportBundle(
        fixture_version="1.0.0",
        dataset=INCREMENT_DATASET,
        source_provider="INFOSTOCK",
        rights_scope="LOCAL_AUDITED_IMPORT",
        parser_version=DAILY_API_PARSER_VERSION,
        expected_theme_count=0,
        input_hash=input_hash,
        dataset_hash=dataset_hash,
        manifest_snapshot=manifest_snapshot,
        index_snapshot=manifest_snapshot,
        index_items=(),
        details=(),
        quality_summary=QualitySummary(
            theme_count=0,
            history_count=0,
            related_stock_count=0,
            leader_count=0,
            historical_membership_count=0,
            duplicate_history_count=0,
            missing_history_date_count=0,
            missing_history_content_count=0,
            missing_leader_code_count=0,
            missing_related_stock_code_count=0,
            missing_historical_membership_code_count=0,
        ),
        quality_issues=(),
        daily=daily,
    )
    return bundle, window


def import_daily_increment(
    bundle: ImportBundle,
    store: InfostockStore,
    *,
    window_start: date,
    window_end: date,
) -> ImportResult:
    """증분 번들을 한 트랜잭션으로 적재한다. 같은 입력의 재실행은 reused다.

    삭제 감지는 수집 구간 안으로 제한한다 — 구간 밖의 과거 게시물은 이 run이
    관측하지 않았으므로 visibility를 판단하지 않는다.
    """

    if bundle.dataset != INCREMENT_DATASET:
        raise ValueError("증분 적재에는 증분 bundle이 필요합니다.")
    InfostockAccessPolicy.require_import_scope(bundle.rights_scope)
    with store.transaction() as transaction:
        transaction.acquire_import_lock(bundle.input_hash)
        completed = transaction.find_completed_import(bundle.input_hash)
        if completed is not None:
            return _result(bundle, completed, reused=True)

        run_id = transaction.create_daily_increment_run(bundle)
        snapshot_ids: dict[tuple[str, str | None], int] = {}
        for snapshot in bundle.daily.pages:
            snapshot_ids[(snapshot.page_type, snapshot.source_entity_id)] = (
                transaction.record_snapshot(run_id, bundle, snapshot)
            )
        counts = transaction.apply_daily(
            run_id,
            bundle,
            snapshot_ids,
            missing_window=(window_start, window_end),
        )
        all_issues = (*bundle.quality_issues, *bundle.daily.quality_issues)
        counts += ApplyCounts(
            quality_issues=transaction.record_quality_issues(
                run_id, tuple(all_issues)
            )
        )
        stored = transaction.complete_daily_increment_run(
            run_id,
            bundle,
            snapshots_linked=len(bundle.daily.pages),
            counts=counts,
        )
        return _result(bundle, stored, reused=False)
