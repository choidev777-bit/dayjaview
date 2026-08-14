"""Stable hashing helpers shared by fixture validation and persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_json(value: object) -> str:
    """Serialize JSON data deterministically without changing source strings."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    """Hash exact source bytes without JSON reserialization."""

    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def fixture_bundle_hash(payload: Mapping[str, Any]) -> str:
    hash_input = {key: value for key, value in payload.items() if key != "bundleHash"}
    return sha256_json(hash_input)
