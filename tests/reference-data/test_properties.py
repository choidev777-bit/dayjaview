from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from conftest import aware


def _metadata(models: Any, dataset: Any, key: str, minute: int):
    timestamp = aware(f"2026-08-14T08:{minute:02d}:00+09:00")
    provider = (
        models.SourceProvider.KRX_OPEN_API
        if dataset is models.SourceDataset.KRX_STOCK_DAILY
        else models.SourceProvider.OPENDART
    )
    endpoint = (
        "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
        if provider is models.SourceProvider.KRX_OPEN_API
        else "https://opendart.fss.or.kr/api/list.json"
    )
    return models.SourceMetadata(
        provider=provider,
        dataset=dataset,
        endpoint=endpoint,
        source_key=key,
        as_of=timestamp,
        collected_at=timestamp,
        parser_version="property-test-v1",
        revision=1,
        lineage=(f"property:{key}",),
    )


def test_free_float_property_is_bounded_and_exact_after_dedup(
    modules: dict[str, Any],
) -> None:
    models = modules["models"]
    rng = random.Random(20260814)
    policy = models.ReferencePolicy(
        version="property-free-float-v1",
        free_float_stale_after=timedelta(days=365),
        sufficient_coverage_ratio=Decimal("0.8"),
        required_non_float_categories=(
            models.NonFloatCategory.TREASURY,
            models.NonFloatCategory.CONTROLLING_HOLDER,
            models.NonFloatCategory.STRATEGIC_LOCKUP,
        ),
    )
    effective = date(2026, 6, 30)
    decision = aware("2026-08-14T10:00:00+09:00")
    for index in range(1, 76):
        stock_code = f"P{index:05d}"
        issued = rng.randint(1_000_000, 1_000_000_000)
        treasury = rng.randint(0, issued // 10)
        controlling = rng.randint(0, issued // 3)
        remaining = issued - treasury - controlling
        strategic = rng.randint(0, max(0, remaining // 4))
        krx_meta = _metadata(
            models, models.SourceDataset.KRX_STOCK_DAILY, f"krx:{stock_code}", 0
        )
        stock_meta = _metadata(
            models, models.SourceDataset.OPENDART_STOCK_TOTAL, f"total:{stock_code}", 1
        )
        holder_meta = _metadata(
            models,
            models.SourceDataset.OPENDART_LARGEST_SHAREHOLDER,
            f"holder:{stock_code}",
            2,
        )
        treasury_meta = _metadata(
            models,
            models.SourceDataset.OPENDART_TREASURY_STATUS,
            f"treasury:{stock_code}",
            3,
        )
        shares = tuple(
            models.FieldObservation(
                stock_code=stock_code,
                field=models.EconomicField.LISTED_COMMON_SHARES,
                value=issued,
                effective_on=effective,
                share_class=models.ShareClass.COMMON,
                metadata=metadata,
            )
            for metadata in (krx_meta, stock_meta)
        )
        holdings = (
            models.NonFloatHolding(
                stock_code=stock_code,
                holder_id="ISSUER_TREASURY",
                holder_name="자기주식",
                category=models.NonFloatCategory.TREASURY,
                share_class=models.ShareClass.COMMON,
                shares=treasury,
                effective_on=effective,
                metadata=stock_meta,
            ),
            models.NonFloatHolding(
                stock_code=stock_code,
                holder_id="ISSUER_TREASURY",
                holder_name="자기주식",
                category=models.NonFloatCategory.TREASURY,
                share_class=models.ShareClass.COMMON,
                shares=treasury,
                effective_on=effective,
                metadata=treasury_meta,
            ),
            models.NonFloatHolding(
                stock_code=stock_code,
                holder_id="CONTROLLER",
                holder_name="최대주주",
                category=models.NonFloatCategory.CONTROLLING_HOLDER,
                share_class=models.ShareClass.COMMON,
                shares=controlling,
                effective_on=effective,
                metadata=holder_meta,
            ),
            models.NonFloatHolding(
                stock_code=stock_code,
                holder_id="STRATEGIC",
                holder_name="전략보유자",
                category=models.NonFloatCategory.STRATEGIC_LOCKUP,
                share_class=models.ShareClass.COMMON,
                shares=strategic,
                effective_on=effective,
                metadata=holder_meta,
            ),
        )
        coverage = tuple(
            models.HoldingCoverageDeclaration(
                stock_code=stock_code,
                category=category,
                status=models.CoverageDeclarationStatus.COMPLETE,
                effective_on=effective,
                metadata=holder_meta if category is not models.NonFloatCategory.TREASURY else treasury_meta,
            )
            for category in policy.required_non_float_categories
        )
        result = modules["free_float"].calculate_free_float(
            stock_code=stock_code,
            market_date=date(2026, 8, 14),
            decision_at=decision,
            share_observations=shares,
            holdings=holdings,
            coverage_declarations=coverage,
            policy=policy,
        )
        expected_deduction = treasury + controlling + strategic

        assert result.available is True
        assert result.duplicate_deductions_prevented == 1
        assert result.deducted_non_float_shares == expected_deduction
        assert result.free_float_shares == issued - expected_deduction
        assert result.ratio is not None
        assert Decimal(0) <= result.ratio <= Decimal(1)
        assert result.ratio * Decimal(issued) == Decimal(issued - expected_deduction)


def test_revision_store_repeated_apply_property_is_idempotent(
    modules: dict[str, Any],
) -> None:
    store = modules["store"].InMemoryReferenceStore()
    for index in range(50):
        value = {"index": index, "value": str(Decimal(index) / Decimal(10))}
        first = store.apply(
            record_type="PROPERTY",
            entity_key=f"entity-{index}",
            effective_on=date(2026, 8, 14),
            known_at=aware("2026-08-14T08:00:00+09:00"),
            value=value,
        )
        for minute in range(1, 5):
            repeated = store.apply(
                record_type="PROPERTY",
                entity_key=f"entity-{index}",
                effective_on=date(2026, 8, 14),
                known_at=aware(f"2026-08-14T08:0{minute}:00+09:00"),
                value=value,
            )
            assert repeated.created is False
            assert repeated.revision.revision == first.revision.revision == 1
