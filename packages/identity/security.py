from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import unquote, urlsplit

from .errors import InvalidCursor
from .models import SavedType


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class TokenSource(Protocol):
    def create(self, number_of_bytes: int = 32) -> str: ...


class SecureTokenSource:
    def create(self, number_of_bytes: int = 32) -> str:
        return secrets.token_urlsafe(number_of_bytes)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def parse_operator_bootstrap_emails(value: str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    return frozenset(
        normalized
        for part in value.split(",")
        if (normalized := normalize_email(part)) and "@" in normalized
    )


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def validate_internal_return_to(
    value: str | None,
    *,
    fallback: str = "/today",
    maximum_length: int = 2048,
) -> str:
    """Return a safe origin-relative path or the fixed fallback.

    Encoded protocol-relative paths, backslashes, and control characters are
    rejected before the value can reach a Location header.
    """

    if value is None or value == "":
        return fallback
    if len(value) > maximum_length or value != value.strip():
        return fallback
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return fallback

    candidate = value
    for _ in range(3):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded

    if candidate.startswith("//") or "\\" in candidate:
        return fallback
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in candidate):
        return fallback

    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return fallback

    original = urlsplit(value)
    if original.scheme or original.netloc:
        return fallback
    if not original.path.startswith("/") or original.path.startswith("//"):
        return fallback
    return value


@dataclass(frozen=True, slots=True)
class CursorPosition:
    saved_at: datetime
    target_id: str
    saved_type: SavedType


class SignedCursorCodec:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("cursor secret must contain at least 32 bytes")
        self._secret = secret

    def encode(
        self,
        *,
        user_id: str,
        saved_filter: str,
        position: CursorPosition,
    ) -> str:
        payload = {
            "filter": saved_filter,
            "savedAt": position.saved_at.isoformat(),
            "savedType": position.saved_type.value,
            "targetId": position.target_id,
            "userScope": self._user_scope(user_id),
            "version": 1,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return f"{_base64url(raw)}.{_base64url(signature)}"

    def decode(
        self,
        cursor: str,
        *,
        user_id: str,
        saved_filter: str,
    ) -> CursorPosition:
        try:
            encoded_payload, encoded_signature = cursor.split(".", 1)
            raw = _base64url_decode(encoded_payload)
            supplied_signature = _base64url_decode(encoded_signature)
            expected_signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise InvalidCursor
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise InvalidCursor
            if payload.get("version") != 1:
                raise InvalidCursor
            if payload.get("userScope") != self._user_scope(user_id):
                raise InvalidCursor
            if payload.get("filter") != saved_filter:
                raise InvalidCursor
            saved_at_raw = payload["savedAt"]
            target_id = payload["targetId"]
            saved_type_raw = payload["savedType"]
            if not isinstance(saved_at_raw, str) or not isinstance(target_id, str):
                raise InvalidCursor
            saved_at = datetime.fromisoformat(saved_at_raw)
            if saved_at.tzinfo is None:
                raise InvalidCursor
            return CursorPosition(saved_at, target_id, SavedType(saved_type_raw))
        except InvalidCursor:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise InvalidCursor from error

    def _user_scope(self, user_id: str) -> str:
        return hmac.new(
            self._secret,
            f"user-scope:{user_id}".encode(),
            hashlib.sha256,
        ).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
