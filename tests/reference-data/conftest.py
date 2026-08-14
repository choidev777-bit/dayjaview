from __future__ import annotations

from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def modules() -> dict[str, Any]:
    prefix = "packages." + "reference-data.reference_data"
    return {
        name: import_module(f"{prefix}.{name}")
        for name in (
            "adapters",
            "adjusted_price",
            "calendar",
            "errors",
            "free_float",
            "hashing",
            "models",
            "parsers",
            "store",
        )
    }


@pytest.fixture(scope="session")
def load_fixture(modules: dict[str, Any]):
    def load(name: str):
        return modules["parsers"].load_source_fixture(
            FIXTURE_ROOT / name,
            repository_root=REPOSITORY_ROOT,
        )

    return load


def aware(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    assert result.tzinfo is not None
    return result
