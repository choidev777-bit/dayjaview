from __future__ import annotations

from datetime import datetime
from decimal import Decimal

type JsonScalar = str | int | float | Decimal | bool | None
type JsonValue = JsonScalar | datetime | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
