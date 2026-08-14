from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from importlib import import_module
from typing import Any

import pytest
from conftest import FIXTURE_ROOT, aware


def test_store_is_idempotent_for_identical_input(modules: dict[str, Any]) -> None:
    store = modules["store"].InMemoryReferenceStore()
    first = store.apply(
        record_type="FREE_FLOAT",
        entity_key="A00001",
        effective_on=date(2026, 8, 14),
        known_at=aware("2026-08-14T08:00:00+09:00"),
        value={"ratio": "0.55"},
    )
    second = store.apply(
        record_type="FREE_FLOAT",
        entity_key="A00001",
        effective_on=date(2026, 8, 14),
        known_at=aware("2026-08-14T09:00:00+09:00"),
        value={"ratio": "0.55"},
    )

    assert first.created is True
    assert second.created is False
    assert second.revision.revision == 1
    assert len(
        store.versions(
            record_type="FREE_FLOAT",
            entity_key="A00001",
            effective_on=date(2026, 8, 14),
        )
    ) == 1


def test_changed_input_creates_revision_and_known_time_interval(
    modules: dict[str, Any],
) -> None:
    store = modules["store"].InMemoryReferenceStore()
    store.apply(
        record_type="FREE_FLOAT",
        entity_key="A00001",
        effective_on=date(2026, 8, 14),
        known_at=aware("2026-08-14T08:00:00+09:00"),
        value={"ratio": "0.55"},
    )
    changed = store.apply(
        record_type="FREE_FLOAT",
        entity_key="A00001",
        effective_on=date(2026, 8, 14),
        known_at=aware("2026-08-14T12:00:00+09:00"),
        value={"ratio": "0.54"},
    )
    versions = store.versions(
        record_type="FREE_FLOAT",
        entity_key="A00001",
        effective_on=date(2026, 8, 14),
    )

    assert changed.created is True
    assert changed.revision.revision == 2
    assert versions[0].known_to == aware("2026-08-14T12:00:00+09:00")
    assert versions[1].known_from == aware("2026-08-14T12:00:00+09:00")
    before = store.point_in_time(
        record_type="FREE_FLOAT",
        entity_key="A00001",
        effective_on_or_before=date(2026, 8, 14),
        decision_at=aware("2026-08-14T10:00:00+09:00"),
    )
    after = store.point_in_time(
        record_type="FREE_FLOAT",
        entity_key="A00001",
        effective_on_or_before=date(2026, 8, 14),
        decision_at=aware("2026-08-14T13:00:00+09:00"),
    )
    assert before is not None and before.value == {"ratio": "0.55"}
    assert after is not None and after.value == {"ratio": "0.54"}


def test_changed_input_cannot_insert_earlier_known_time(modules: dict[str, Any]) -> None:
    store = modules["store"].InMemoryReferenceStore()
    store.apply(
        record_type="CALENDAR",
        entity_key="KRX:2026-08-17",
        effective_on=date(2026, 8, 17),
        known_at=aware("2026-08-10T09:00:00+09:00"),
        value={"isTradingDay": False},
    )

    with pytest.raises(modules["errors"].TemporalConflictError):
        store.apply(
            record_type="CALENDAR",
            entity_key="KRX:2026-08-17",
            effective_on=date(2026, 8, 17),
            known_at=aware("2026-08-09T09:00:00+09:00"),
            value={"isTradingDay": True},
        )


def test_snapshot_ledger_preserves_source_revision(
    modules: dict[str, Any], load_fixture
) -> None:
    snapshot = load_fixture("krx-stock-daily.json")
    store = modules["store"].InMemoryReferenceStore()
    first = store.apply_snapshot(snapshot)
    repeated = store.apply_snapshot(snapshot)
    raw = modules["hashing"].parse_json_object(snapshot.raw_payload_text)
    raw["OutBlock_1"][0]["LIST_SHRS"] = "100,000,001"
    raw_text = modules["hashing"].canonical_json(raw)
    changed = modules["models"].SourceSnapshot(
        metadata=replace(
            snapshot.metadata,
            collected_at=aware("2026-08-13T19:00:00+09:00"),
            revision=2,
            lineage=("fixture:krx-stock-daily:revision-2",),
        ),
        raw_payload_text=raw_text,
        raw_hash=modules["hashing"].sha256_text(raw_text),
    )
    second = store.apply_snapshot(changed)

    assert first.revision.revision == 1
    assert repeated.created is False
    assert second.revision.revision == 2


def test_rejected_snapshot_revision_does_not_mutate_ledger(
    modules: dict[str, Any], load_fixture
) -> None:
    snapshot = load_fixture("krx-stock-daily.json")
    store = modules["store"].InMemoryReferenceStore()
    store.apply_snapshot(snapshot)
    raw = modules["hashing"].parse_json_object(snapshot.raw_payload_text)
    raw["OutBlock_1"][0]["LIST_SHRS"] = "100,000,002"
    raw_text = modules["hashing"].canonical_json(raw)
    invalid = modules["models"].SourceSnapshot(
        metadata=replace(
            snapshot.metadata,
            collected_at=aware("2026-08-13T19:00:00+09:00"),
            revision=3,
        ),
        raw_payload_text=raw_text,
        raw_hash=modules["hashing"].sha256_text(raw_text),
    )

    with pytest.raises(modules["errors"].TemporalConflictError):
        store.apply_snapshot(invalid)

    entity_key = "KRX_OPEN_API:KRX_STOCK_DAILY:KOSPI:2026-08-13"
    assert len(
        store.versions(
            record_type="SOURCE_SNAPSHOT",
            entity_key=entity_key,
            effective_on=date(2026, 8, 13),
        )
    ) == 1


def test_offline_worker_reports_fixture_success_and_live_blocker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker = import_module("apps.worker-batch.reference-data.import_fixture")
    exit_code = worker.main([str(FIXTURE_ROOT / "krx-stock-daily.json")])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "COMPLETE"
    assert output["fixtureContractStatus"] == "VERIFIED"
    assert output["liveValidationStatus"] == "UNVERIFIED"
    assert output["liveBlocker"] == "B-REFDATA-KEYS"
    assert output["liveRequestAttempted"] is False


def test_offline_worker_normalizes_opendart_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker = import_module("apps.worker-batch.reference-data.import_fixture")
    exit_code = worker.main(
        [
            str(FIXTURE_ROOT / "opendart-stock-total.json"),
            "--stock-code",
            "A00001",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["source"] == "OPENDART"
    assert output["normalizedCount"] == 3
