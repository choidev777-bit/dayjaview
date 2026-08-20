from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Protocol, cast

from .app_types import JsonObject, JsonValue

_META_KEYS = {
    "requestId",
    "apiVersion",
    "schemaVersion",
    "generatedAt",
    "marketContext",
    "versions",
}
_PRIVATE_PRODUCT_FIELDS = {
    "assignee",
    "cookie",
    "credential",
    "diagnosticContext",
    "internalContext",
    "internalNote",
    "internalReason",
    "operatorReason",
    "reviewStatus",
    "secret",
    "token",
}


def _is_private_field(name: str) -> bool:
    normalized = "".join(character for character in name.casefold() if character.isalnum())
    return (
        name in _PRIVATE_PRODUCT_FIELDS
        or normalized.endswith("token")
        or normalized.startswith(("internal", "operator"))
        or any(
            marker in normalized
            for marker in ("cookie", "credential", "password", "secret")
        )
        or normalized == "reviewstatus"
    )


def _copy_object(value: JsonObject) -> JsonObject:
    return cast(JsonObject, deepcopy(value))


def ensure_public_projection(value: JsonValue) -> None:
    if isinstance(value, list):
        for item in value:
            ensure_public_projection(item)
        return
    if not isinstance(value, dict):
        return
    invalid_keys = [key for key in value if not isinstance(key, str)]
    if invalid_keys:
        raise ValueError("public product fields must be strings")
    forbidden = {key for key in value if _is_private_field(key)}
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ValueError(f"private product fields are not allowed: {names}")
    for item in value.values():
        ensure_public_projection(item)


@dataclass(frozen=True, slots=True)
class ProductDocument:
    """One public read-model projection without request-specific metadata."""

    data: JsonObject
    market_context: JsonObject | None = None
    versions: JsonObject | None = None

    def __post_init__(self) -> None:
        ensure_public_projection(self.data)
        object.__setattr__(self, "data", _copy_object(self.data))
        if self.market_context is not None:
            ensure_public_projection(self.market_context)
            object.__setattr__(
                self,
                "market_context",
                _copy_object(self.market_context),
            )
        if self.versions is not None:
            ensure_public_projection(self.versions)
            object.__setattr__(self, "versions", _copy_object(self.versions))

    @classmethod
    def from_response(cls, response: JsonObject) -> ProductDocument:
        if set(response) != {"data", "meta"}:
            raise ValueError("product fixture must contain only data and meta")
        data = response["data"]
        meta = response["meta"]
        if not isinstance(data, dict) or not isinstance(meta, dict):
            raise TypeError("product fixture data and meta must be objects")
        if not set(meta).issubset(_META_KEYS):
            raise ValueError("product fixture contains unsupported metadata")
        market_context = meta.get("marketContext")
        versions = meta.get("versions")
        if market_context is not None and not isinstance(market_context, dict):
            raise ValueError("marketContext must be an object")
        if versions is not None and not isinstance(versions, dict):
            raise ValueError("versions must be an object")
        return cls(
            data=cast(JsonObject, data),
            market_context=cast(JsonObject | None, market_context),
            versions=cast(JsonObject | None, versions),
        )

    def copy_data(self) -> JsonObject:
        return _copy_object(self.data)

    def copy_market_context(self) -> JsonObject | None:
        if self.market_context is None:
            return None
        return _copy_object(self.market_context)

    def copy_versions(self) -> JsonObject | None:
        if self.versions is None:
            return None
        return _copy_object(self.versions)


class ProductReadRepository(Protocol):
    def market_session(self) -> ProductDocument | None: ...

    def rankings(self, market_date: str | None) -> ProductDocument | None: ...

    def treemap(self) -> ProductDocument | None: ...

    def theme_event(
        self,
        theme_id: str,
        event_id: str,
    ) -> ProductDocument | None: ...

    def theme_for_event(self, event_id: str) -> str | None: ...

    def evidence(
        self,
        event_id: str,
        cursor: str | None,
    ) -> ProductDocument | None: ...

    def similar_events(
        self,
        event_id: str,
        cursor: str | None,
        limit: int = 20,
    ) -> ProductDocument | None: ...

    def historical_event(
        self,
        event_id: str,
        context_event_id: str | None = None,
    ) -> ProductDocument | None: ...


class EmptyProductReadRepository:
    def market_session(self) -> ProductDocument | None:
        return None

    def rankings(self, market_date: str | None) -> ProductDocument | None:
        return None

    def treemap(self) -> ProductDocument | None:
        return None

    def theme_event(
        self,
        theme_id: str,
        event_id: str,
    ) -> ProductDocument | None:
        return None

    def theme_for_event(self, event_id: str) -> str | None:
        return None

    def evidence(
        self,
        event_id: str,
        cursor: str | None,
    ) -> ProductDocument | None:
        return None

    def similar_events(
        self,
        event_id: str,
        cursor: str | None,
        limit: int = 20,
    ) -> ProductDocument | None:
        return None

    def historical_event(
        self,
        event_id: str,
        context_event_id: str | None = None,
    ) -> ProductDocument | None:
        return None


class InMemoryProductReadRepository(EmptyProductReadRepository):
    """Deterministic fixture read repository; it never calls a live source."""

    def __init__(self) -> None:
        self._market_session: ProductDocument | None = None
        self._rankings: dict[str | None, ProductDocument] = {}
        self._treemap: ProductDocument | None = None
        self._theme_events: dict[tuple[str, str], ProductDocument] = {}
        self._themes_by_event: dict[str, str] = {}
        self._evidence: dict[tuple[str, str | None], ProductDocument] = {}
        self._similar: dict[tuple[str, str | None], ProductDocument] = {}
        self._historical: dict[str, ProductDocument] = {}
        self._lock = RLock()

    def put_market_session(self, document: ProductDocument) -> None:
        with self._lock:
            self._market_session = document

    def put_rankings(
        self,
        document: ProductDocument,
        *,
        market_date: str | None = None,
    ) -> None:
        with self._lock:
            self._rankings[market_date] = document

    def put_treemap(self, document: ProductDocument) -> None:
        with self._lock:
            self._treemap = document

    def put_theme_event(
        self,
        theme_id: str,
        event_id: str,
        document: ProductDocument,
    ) -> None:
        with self._lock:
            previous = self._themes_by_event.get(event_id)
            if previous is not None and previous != theme_id:
                raise ValueError("one Event cannot have two current fixture themes")
            self._theme_events[(theme_id, event_id)] = document
            self._themes_by_event[event_id] = theme_id

    def put_evidence(
        self,
        event_id: str,
        document: ProductDocument,
        *,
        cursor: str | None = None,
    ) -> None:
        with self._lock:
            self._evidence[(event_id, cursor)] = document

    def put_similar_events(
        self,
        event_id: str,
        document: ProductDocument,
        *,
        cursor: str | None = None,
    ) -> None:
        with self._lock:
            self._similar[(event_id, cursor)] = document

    def put_historical_event(
        self,
        event_id: str,
        document: ProductDocument,
    ) -> None:
        with self._lock:
            self._historical[event_id] = document

    def market_session(self) -> ProductDocument | None:
        with self._lock:
            return self._market_session

    def rankings(self, market_date: str | None) -> ProductDocument | None:
        with self._lock:
            document = self._rankings.get(market_date)
            if document is None and market_date is None:
                document = self._rankings.get(None)
            return document

    def treemap(self) -> ProductDocument | None:
        with self._lock:
            return self._treemap

    def theme_event(
        self,
        theme_id: str,
        event_id: str,
    ) -> ProductDocument | None:
        with self._lock:
            return self._theme_events.get((theme_id, event_id))

    def theme_for_event(self, event_id: str) -> str | None:
        with self._lock:
            return self._themes_by_event.get(event_id)

    def evidence(
        self,
        event_id: str,
        cursor: str | None,
    ) -> ProductDocument | None:
        with self._lock:
            return self._evidence.get((event_id, cursor))

    def similar_events(
        self,
        event_id: str,
        cursor: str | None,
        limit: int = 20,
    ) -> ProductDocument | None:
        with self._lock:
            return self._similar.get((event_id, cursor))

    def historical_event(
        self,
        event_id: str,
        context_event_id: str | None = None,
    ) -> ProductDocument | None:
        with self._lock:
            return self._historical.get(event_id)
