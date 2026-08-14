"""Deterministic, offline validation for DAYJAVIEW Stage 0 contracts."""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.readers import read_from_filename
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
FIXTURES = CONTRACTS / "fixtures"
SHARED_SCHEMA_PATH = CONTRACTS / "schemas" / "stage0.schema.json"
ASYNC_PROFILE_PATH = CONTRACTS / "meta" / "asyncapi-stage0.schema.json"
OPENAPI_PATH = CONTRACTS / "openapi.yaml"
ASYNCAPI_PATH = CONTRACTS / "asyncapi.yaml"
MANIFEST_PATH = FIXTURES / "manifest.json"
PROSE_PATH = ROOT / "docs" / "api_contract.md"


REQUIRED_HTTP_SURFACE = {
    "/auth/google": {"get"},
    "/auth/google/callback": {"get"},
    "/auth/session": {"get"},
    "/auth/logout": {"post"},
    "/v1/auth/realtime-ticket": {"post"},
    "/v1/market/session": {"get"},
    "/v1/themes/rankings": {"get"},
    "/v1/insights/treemap": {"get"},
    "/v1/themes/{themeId}/events/{eventId}": {"get"},
    "/v1/events/{eventId}/evidence": {"get"},
    "/v1/me/saved": {"get"},
    "/v1/me/saved/themes/{themeId}": {"put", "delete"},
    "/v1/me/saved/stocks/{stockId}": {"put", "delete"},
    "/v1/me/saved/events/{eventId}": {"put", "delete"},
    "/v1/me": {"delete"},
    "/v1/events/{eventId}/similar-events": {"get"},
    "/v1/events/{eventId}": {"get"},
    "/v1/operator/status": {"get"},
    "/v1/operator/jobs": {"get"},
    "/v1/operator/jobs/{runId}": {"get"},
    "/v1/operator/jobs/{runId}/retry": {"post"},
    "/v1/operator/jobs/{runId}/resume": {"post"},
    "/v1/operator/reviews": {"get"},
    "/v1/operator/reviews/{reviewId}": {"get"},
    "/v1/operator/reviews/{reviewId}/resolve": {"post"},
    "/v1/operator/audit": {"get"},
    "/v1/operator/infostock/auth-status": {"get"},
}

REQUIRED_ASYNC_MESSAGES = {
    "Auth",
    "Subscribe",
    "Pong",
    "Subscribed",
    "ThemeRankSnapshot",
    "ThemeTreemapSnapshot",
    "EventStateChanged",
    "Ping",
    "Error",
}

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


class ContractValidationError(AssertionError):
    """A deterministic contract validation failure."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    shared = _read_json(SHARED_SCHEMA_PATH)
    manifest = _read_json(MANIFEST_PATH)
    openapi = _read_yaml(OPENAPI_PATH)
    asyncapi = _read_yaml(ASYNCAPI_PATH)
    return shared, manifest, openapi, asyncapi


def _registry(shared: Mapping[str, Any]) -> Registry:
    return Registry().with_resource(
        str(shared["$id"]),
        Resource.from_contents(shared),
    )


def schema_validator(
    shared: Mapping[str, Any], schema_name: str
) -> Draft202012Validator:
    if schema_name not in shared["$defs"]:
        raise ContractValidationError(f"unknown shared schema: {schema_name}")
    reference = {"$ref": f"{shared['$id']}#/$defs/{schema_name}"}
    return Draft202012Validator(
        reference,
        registry=_registry(shared),
        format_checker=FormatChecker(),
    )


def validate_instance(
    instance: Any,
    schema_name: str,
    *,
    shared: Mapping[str, Any] | None = None,
    label: str = "instance",
) -> None:
    shared_document = shared if shared is not None else _read_json(SHARED_SCHEMA_PATH)
    errors = sorted(
        schema_validator(shared_document, schema_name).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        rendered = []
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            rendered.append(f"{label}:{location}: {error.message}")
        raise ContractValidationError("\n".join(rendered))


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer in {"", "#"}:
        return document
    if pointer.startswith("#"):
        pointer = pointer[1:]
    if not pointer.startswith("/"):
        raise ContractValidationError(f"invalid JSON pointer: {pointer}")
    current = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


def _walk_refs(value: Any, location: str = "<root>") -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        if "$ref" in value:
            yield str(value["$ref"]), location
        for key, child in value.items():
            yield from _walk_refs(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_refs(child, f"{location}/{index}")


def _validate_references(
    document: Mapping[str, Any],
    *,
    label: str,
    shared: Mapping[str, Any],
) -> None:
    for reference, location in _walk_refs(document):
        try:
            if reference.startswith("#"):
                _json_pointer(document, reference)
            elif reference.startswith("./schemas/stage0.schema.json#"):
                _, fragment = reference.split("#", 1)
                _json_pointer(shared, f"#{fragment}")
            else:
                raise ContractValidationError(
                    f"{label}:{location}: unsupported non-local reference {reference}"
                )
        except (KeyError, IndexError, ValueError) as exc:
            raise ContractValidationError(
                f"{label}:{location}: unresolved reference {reference}"
            ) from exc


def validate_specs(
    shared: Mapping[str, Any],
    openapi: Mapping[str, Any],
    asyncapi: Mapping[str, Any],
) -> None:
    Draft202012Validator.check_schema(shared)
    for reference, location in _walk_refs(shared, "shared-schema"):
        if not reference.startswith("#"):
            raise ContractValidationError(
                f"{location}: shared schema reference must remain local: {reference}"
            )
        _json_pointer(shared, reference)

    spec, base_uri = read_from_filename(str(OPENAPI_PATH))
    validate_openapi(spec, base_uri=base_uri)
    _validate_references(openapi, label="openapi", shared=shared)

    async_profile = _read_json(ASYNC_PROFILE_PATH)
    Draft202012Validator.check_schema(async_profile)
    profile_errors = sorted(
        Draft202012Validator(async_profile).iter_errors(asyncapi),
        key=lambda error: list(error.absolute_path),
    )
    if profile_errors:
        rendered = []
        for error in profile_errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            rendered.append(f"asyncapi:{location}: {error.message}")
        raise ContractValidationError("\n".join(rendered))
    _validate_references(asyncapi, label="asyncapi", shared=shared)


def _operation_methods(path_item: Mapping[str, Any]) -> set[str]:
    return {key for key in path_item if key in HTTP_METHODS}


def validate_surface(openapi: Mapping[str, Any], asyncapi: Mapping[str, Any]) -> None:
    paths = openapi["paths"]
    actual_surface = {
        path: _operation_methods(path_item) for path, path_item in paths.items()
    }
    if actual_surface != REQUIRED_HTTP_SURFACE:
        missing = sorted(set(REQUIRED_HTTP_SURFACE) - set(actual_surface))
        extra = sorted(set(actual_surface) - set(REQUIRED_HTTP_SURFACE))
        method_drift = {
            path: {
                "expected": sorted(REQUIRED_HTTP_SURFACE[path]),
                "actual": sorted(actual_surface[path]),
            }
            for path in set(actual_surface) & set(REQUIRED_HTTP_SURFACE)
            if actual_surface[path] != REQUIRED_HTTP_SURFACE[path]
        }
        raise ContractValidationError(
            f"HTTP surface drift: missing={missing}, extra={extra}, methods={method_drift}"
        )

    operation_ids: list[str] = []
    for path, path_item in paths.items():
        for method in _operation_methods(path_item):
            operation = path_item[method]
            operation_ids.append(operation["operationId"])
            if method in {"post", "put", "delete"}:
                parameter_refs = {
                    parameter.get("$ref")
                    for parameter in operation.get("parameters", [])
                    if isinstance(parameter, Mapping)
                }
                required_csrf = {
                    "#/components/parameters/Origin",
                    "#/components/parameters/CsrfToken",
                }
                if not required_csrf <= parameter_refs:
                    raise ContractValidationError(
                        f"{method.upper()} {path} must require Origin and CSRF headers"
                    )
            if path.startswith("/v1/operator/"):
                if operation.get("x-required-role") != "OPERATOR":
                    raise ContractValidationError(
                        f"{method.upper()} {path} must require the OPERATOR role"
                    )
                if "403" not in operation["responses"]:
                    raise ContractValidationError(
                        f"{method.upper()} {path} must contract a USER 403 response"
                    )
                if method == "post" and (
                    "#/components/parameters/IdempotencyKey" not in parameter_refs
                ):
                    raise ContractValidationError(
                        f"{method.upper()} {path} must require Idempotency-Key"
                    )
            if path.startswith("/v1/events/") and (
                path.endswith("/similar-events") or path == "/v1/events/{eventId}"
            ):
                if operation.get("x-feature-gate") != "ONTOLOGY_VALIDATION_REQUIRED":
                    raise ContractValidationError(
                        f"{method.upper()} {path} must remain historical-gate controlled"
                    )
    if len(operation_ids) != len(set(operation_ids)):
        raise ContractValidationError("OpenAPI operationId values must be unique")

    sort_parameter = next(
        parameter
        for parameter in paths["/v1/events/{eventId}/similar-events"]["get"]["parameters"]
        if parameter.get("name") == "sort"
    )
    if "outcome" in sort_parameter["schema"]["enum"]:
        raise ContractValidationError("historical results must not support outcome sorting")

    shared_defs = _read_json(SHARED_SCHEMA_PATH)["$defs"]
    public_serialized = json.dumps(
        {
            "ThemeDetailData": shared_defs["ThemeDetailData"],
            "EventStateSummary": shared_defs["EventStateSummary"],
            "RankingItem": shared_defs["RankingItem"],
        },
        ensure_ascii=False,
    )
    if "reviewStatus" in public_serialized:
        raise ContractValidationError("operator reviewStatus leaked into a public schema")
    if "reviewStatus" not in json.dumps(shared_defs["OperatorReview"]):
        raise ContractValidationError("operator review projection must retain reviewStatus")

    messages = set(asyncapi["components"]["messages"])
    if messages != REQUIRED_ASYNC_MESSAGES:
        raise ContractValidationError(
            f"AsyncAPI message surface drift: expected={sorted(REQUIRED_ASYNC_MESSAGES)}, "
            f"actual={sorted(messages)}"
        )
    event_payload = asyncapi["components"]["messages"]["EventStateChanged"]["payload"]["$ref"]
    if not event_payload.endswith("/WsEventStateChanged"):
        raise ContractValidationError(
            "event_state_changed must carry the full public event summary"
        )


def _fixture_declarations(
    openapi: Mapping[str, Any], asyncapi: Mapping[str, Any]
) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    for path_item in openapi["paths"].values():
        for method in _operation_methods(path_item):
            operation = path_item[method]
            for fixture in operation.get("x-stage0-fixtures", []):
                schema_ref = (
                    operation["responses"]["200"]["content"]["application/json"]["schema"][
                        "$ref"
                    ]
                )
                declarations.append((fixture, schema_ref.rsplit("/", 1)[-1]))
    for message in asyncapi["components"]["messages"].values():
        schema_name = message["payload"]["$ref"].rsplit("/", 1)[-1]
        for fixture in message.get("x-stage0-fixtures", []):
            declarations.append((fixture, schema_name))
    return declarations


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ratio_close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0, abs_tol=0.0001)


def validate_invariants(value: Any, *, label: str = "value") -> None:
    failures: list[str] = []

    def fail(location: str, message: str) -> None:
        failures.append(f"{label}:{location}: {message}")

    def visit(node: Any, location: str) -> None:
        if isinstance(node, Mapping):
            if isinstance(node.get("asOf"), str) and isinstance(
                node.get("generatedAt"), str
            ):
                if _parse_timestamp(node["asOf"]) > _parse_timestamp(node["generatedAt"]):
                    fail(location, "asOf must not be later than generatedAt")
            meta = node.get("meta")
            if isinstance(meta, Mapping):
                market_context = meta.get("marketContext")
                if isinstance(market_context, Mapping) and isinstance(
                    market_context.get("asOf"), str
                ):
                    if _parse_timestamp(market_context["asOf"]) > _parse_timestamp(
                        meta["generatedAt"]
                    ):
                        fail(location, "marketContext.asOf must not exceed meta.generatedAt")
            if isinstance(node.get("publishedAt"), str) and isinstance(
                node.get("receivedAt"), str
            ):
                if _parse_timestamp(node["publishedAt"]) > _parse_timestamp(
                    node["receivedAt"]
                ):
                    fail(location, "publishedAt must not be later than receivedAt")

            observed = node.get("observedCount")
            total = node.get("totalCount")
            ratio = node.get("countRatio")
            if isinstance(observed, int) and isinstance(total, int):
                if observed > total:
                    fail(location, "observedCount must not exceed totalCount")
                if total == 0 and ratio is not None:
                    fail(location, "zero denominator requires a null countRatio")
                if total > 0 and (
                    not isinstance(ratio, (int, float))
                    or not _ratio_close(float(ratio), observed / total)
                ):
                    fail(location, "countRatio must match observedCount / totalCount")

            eligible = node.get("eligibleCount")
            positive = node.get("positiveCount")
            if all(isinstance(item, int) for item in (eligible, observed, positive)):
                if not positive <= observed <= eligible:
                    fail(
                        location,
                        "positiveCount must not exceed observedCount or eligibleCount",
                    )

            coverage = node.get("coverage")
            if (
                isinstance(coverage, Mapping)
                and coverage.get("status") == "INSUFFICIENT"
                and node.get("weightedReturn") == 0
            ):
                fail(location, "INSUFFICIENT coverage must not be exposed as zero return")

            if node.get("reconciliationStatus") == "UNMATCHED":
                historical = node.get("historicalAccess")
                if isinstance(historical, Mapping) and historical.get("status") == "AVAILABLE":
                    fail(location, "UNMATCHED events must not auto-enable historical access")

            availability = node.get("availability")
            if availability == "UNAVAILABLE" and "currentState" in node:
                if node.get("currentState") is not None or not node.get(
                    "unavailableReason"
                ):
                    fail(
                        location,
                        "unavailable saved items require a reason and null currentState",
                    )
            if availability == "GATED" and (
                node.get("items") not in (None, [])
                or node.get("summary") not in (None, [])
            ):
                fail(location, "gated historical responses must contain no results")

            outcome_status = node.get("status")
            if outcome_status == "OBSERVED" and "return" in node:
                if node.get("return") is None or node.get("unavailableReason") is not None:
                    fail(
                        location,
                        "OBSERVED outcome requires return and no unavailableReason",
                    )
            if outcome_status in {"UNAVAILABLE", "PENDING"} and "return" in node:
                if node.get("return") is not None or not node.get("unavailableReason"):
                    fail(
                        location,
                        "unobserved outcome requires null return and an unavailableReason",
                    )

            evidence_status = node.get("evidenceStatus")
            source_count = node.get("sourceCount")
            if source_count == 0 and (
                node.get("summary") is not None
                or node.get("latestPublishedAt") is not None
            ):
                fail(location, "zero evidence sources cannot produce a cause summary")
            if (
                evidence_status in {"SINGLE_SOURCE", "MULTI_SOURCE_CONFIRMED"}
                and isinstance(source_count, int)
                and source_count < 1
            ):
                fail(location, "confirmed evidence status requires at least one source")

            if node.get("type") in {
                "theme_rank_snapshot",
                "theme_treemap_snapshot",
                "event_state_changed",
            } and node.get("type") != node.get("topic"):
                fail(location, "WebSocket snapshot type and topic must match")

            for key, child in node.items():
                visit(child, f"{location}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{location}[{index}]")

    visit(value, "<root>")
    if failures:
        raise ContractValidationError("\n".join(failures))


def validate_fixtures(
    shared: Mapping[str, Any],
    manifest: Mapping[str, Any],
    openapi: Mapping[str, Any],
    asyncapi: Mapping[str, Any],
) -> int:
    entries = manifest["fixtures"]
    declared_paths = [entry["path"] for entry in entries]
    if len(declared_paths) != len(set(declared_paths)):
        raise ContractValidationError("fixture manifest contains duplicate paths")

    actual_paths = sorted(
        path.relative_to(FIXTURES).as_posix()
        for path in FIXTURES.rglob("*.json")
        if path != MANIFEST_PATH
    )
    if sorted(declared_paths) != actual_paths:
        missing = sorted(set(actual_paths) - set(declared_paths))
        stale = sorted(set(declared_paths) - set(actual_paths))
        raise ContractValidationError(
            f"fixture manifest drift: unmapped={missing}, missing_files={stale}"
        )

    entry_schemas = {entry["path"]: entry["schema"] for entry in entries}
    for fixture_path, expected_schema in _fixture_declarations(openapi, asyncapi):
        if entry_schemas.get(fixture_path) != expected_schema:
            raise ContractValidationError(
                f"spec fixture mapping drift for {fixture_path}: "
                f"expected {expected_schema}, manifest has {entry_schemas.get(fixture_path)}"
            )

    public_prefixes = ("market/", "rankings/", "treemap/", "event/", "evidence/", "saved/")
    for entry in entries:
        fixture_path = FIXTURES / entry["path"]
        instance = _read_json(fixture_path)
        validate_instance(
            instance,
            entry["schema"],
            shared=shared,
            label=entry["path"],
        )
        validate_invariants(instance, label=entry["path"])
        if entry["path"].startswith(public_prefixes) and _contains_key(
            instance, "reviewStatus"
        ):
            raise ContractValidationError(
                f"{entry['path']}: operator reviewStatus leaked into a public fixture"
            )

    for series in manifest.get("invariantSeries", []):
        documents = [_read_json(FIXTURES / path) for path in series["paths"]]
        event_ids = [document["data"]["eventId"] for document in documents]
        if len(set(event_ids)) != 1:
            raise ContractValidationError(
                f"{series['name']}: eventId changed across the lifecycle series"
            )
        versions = [
            document["data"]["classification"]["classificationVersion"]
            for document in documents
        ]
        if versions != sorted(versions):
            raise ContractValidationError(
                f"{series['name']}: classificationVersion is not monotonic"
            )
        changed_at = [
            _parse_timestamp(document["data"]["classification"]["changedAt"])
            for document in documents
        ]
        if changed_at != sorted(changed_at):
            raise ContractValidationError(
                f"{series['name']}: classification changedAt is not monotonic"
            )

    live = _read_json(FIXTURES / "realtime" / "ranking-snapshot.json")
    reconnect = _read_json(FIXTURES / "realtime" / "reconnect-full-snapshot.json")
    if reconnect["sequence"] >= live["sequence"]:
        raise ContractValidationError(
            "reconnect fixture must demonstrate a lower sequence in a new stream"
        )
    if reconnect["streamId"] == live["streamId"]:
        raise ContractValidationError(
            "a reset sequence is only valid when reconnect starts a new streamId"
        )
    return len(entries)


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def _infer_prose_schema(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if "error" in value and "meta" in value:
        return "ErrorResponse"
    if "savedType" in value and "currentState" in value:
        return "SavedItem"
    if "newsId" in value and "originalUrl" in value:
        return "EvidenceItem"
    if "coverageStatus" in value and "themeId" in value:
        return "TreemapItem"
    if value.get("type") == "subscribe":
        return "WsSubscribe"
    if value.get("type") == "subscribed":
        return "WsSubscribed"
    if value.get("type") == "theme_rank_snapshot":
        return "WsRankingSnapshot"
    if value.get("type") == "ping":
        return "WsPing"
    if value.get("type") == "error":
        return "WsError"
    data = value.get("data")
    if not isinstance(data, Mapping) or "meta" not in value:
        return None
    if "sessionPhase" in data:
        return "MarketSessionResponse"
    if "currentReaction" in data:
        return "ThemeDetailResponse"
    if "decisionAt" in data and "summary" in data:
        return "SimilarEventsResponse"
    if "snapshotId" in data and data.get("items"):
        first = data["items"][0]
        if isinstance(first, Mapping) and "rank" in first:
            return "RankingResponse"
    return None


def _prose_json_examples(text: str) -> list[tuple[int, Any]]:
    examples: list[tuple[int, Any]] = []
    pattern = r"\x60{3}json\s*\n(.*?)\n\x60{3}"
    for match in re.finditer(pattern, text, flags=re.DOTALL):
        line = text.count("\n", 0, match.start()) + 1
        try:
            examples.append((line, json.loads(match.group(1))))
        except json.JSONDecodeError as exc:
            raise ContractValidationError(
                f"docs/api_contract.md:{line}: invalid JSON example: {exc}"
            ) from exc
    return examples


def validate_prose(shared: Mapping[str, Any]) -> int:
    text = PROSE_PATH.read_text(encoding="utf-8")
    saved_section = text.split("### 10.6 사용자 관심·저장", 1)[1].split(
        "### 10.7 내부 운영자 API", 1
    )[0]
    if '"weightedReturn": 0.0342' not in saved_section:
        raise ContractValidationError(
            "saved-item prose example must encode 3.42% as decimal 0.0342"
        )
    if '"weightedReturn": 3.42' in saved_section:
        raise ContractValidationError(
            "saved-item prose example still uses a percent-number return"
        )

    public_detail = text.split("### 10.4 ", 1)[1].split("### 10.5", 1)[0]
    if '"reviewStatus"' in public_detail:
        raise ContractValidationError(
            "general-user theme detail prose example exposes reviewStatus"
        )

    examples = _prose_json_examples(text)
    for line, example in examples:
        validate_invariants(example, label=f"docs/api_contract.md:{line}")
        inferred = _infer_prose_schema(example)
        if inferred:
            validate_instance(
                example,
                inferred,
                shared=shared,
                label=f"docs/api_contract.md:{line}",
            )
    return len(examples)


def run_validation() -> dict[str, int]:
    shared, manifest, openapi, asyncapi = load_contracts()
    validate_specs(shared, openapi, asyncapi)
    validate_surface(openapi, asyncapi)
    fixture_count = validate_fixtures(shared, manifest, openapi, asyncapi)
    prose_example_count = validate_prose(shared)
    return {
        "http_operations": sum(
            len(_operation_methods(path_item))
            for path_item in openapi["paths"].values()
        ),
        "websocket_messages": len(asyncapi["components"]["messages"]),
        "schemas": len(shared["$defs"]),
        "fixtures": fixture_count,
        "prose_json_examples": prose_example_count,
    }


def main() -> int:
    try:
        counts = run_validation()
    except Exception as exc:
        print(f"contract validation failed: {exc}", file=sys.stderr)
        return 1
    rendered = ", ".join(f"{key}={value}" for key, value in counts.items())
    print(f"contract validation passed: {rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
