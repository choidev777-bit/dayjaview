"""Tracked KRX/OpenDART fixture를 검증하는 offline batch entrypoint."""

from __future__ import annotations

import argparse
import json
from importlib import import_module
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KRX/OpenDART 기준정보 fixture를 live 호출 없이 검증합니다."
    )
    parser.add_argument("fixture", type=Path)
    parser.add_argument(
        "--stock-code",
        help="OpenDART fixture를 정규화할 6자리 종목코드",
    )
    return parser


def _normalize_count(parsers: Any, models: Any, snapshot: Any, stock_code: str | None) -> int:
    if snapshot.metadata.dataset is models.SourceDataset.KRX_STOCK_DAILY:
        return len(parsers.parse_krx_stock_daily(snapshot))
    if not stock_code:
        raise ValueError("OpenDART fixture에는 --stock-code가 필요합니다.")
    normalized = parsers.parse_open_dart(snapshot, stock_code=stock_code)
    return (
        len(normalized.issued_share_observations)
        + len(normalized.non_float_holdings)
        + len(normalized.coverage_declarations)
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[3]
    package = "packages." + "reference-data.reference_data"
    parsers = import_module(f"{package}.parsers")
    models = import_module(f"{package}.models")
    try:
        snapshot = parsers.load_source_fixture(
            args.fixture,
            repository_root=repository_root,
        )
        normalized_count = _normalize_count(
            parsers,
            models,
            snapshot,
            args.stock_code,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "FAILED", "messageKo": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    metadata = snapshot.metadata
    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "fixtureContractStatus": "VERIFIED",
                "liveValidationStatus": "UNVERIFIED",
                "liveBlocker": "B-REFDATA-KEYS",
                "liveRequestAttempted": False,
                "source": metadata.provider.value,
                "dataset": metadata.dataset.value,
                "asOf": metadata.as_of.isoformat(),
                "collectedAt": metadata.collected_at.isoformat(),
                "revision": metadata.revision,
                "lineage": list(metadata.lineage),
                "normalizedCount": normalized_count,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
