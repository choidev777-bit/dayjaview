from __future__ import annotations

from datetime import datetime

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | datetime | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
