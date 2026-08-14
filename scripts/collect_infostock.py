#!/usr/bin/env python3
"""Collect the approved Infostock theme master and complete theme details.

The collector uses the same JSON endpoints as the Infostock web application.
It never reads or stores a username, password, cookie, or browser session.
Daily Featured Theme content is intentionally excluded because that endpoint
uses the paid browser session and is collected by the separate automated
browser worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCHEMA_VERSION = "1.0.0"
SOURCE = "infostock"
DEFAULT_BASE_URL = "https://api.infostock.co.kr:9081/web"
DEFAULT_OUTPUT_DIR = "./data/infostock/import"
KST = timezone(timedelta(hours=9))
PAIR_RE = re.compile(r"^(\d{6})-(.+)$")
REQUEST_LOCK = threading.Lock()
NEXT_REQUEST_AT = 0.0


class CollectionError(RuntimeError):
    """Raised when an Infostock response violates the expected contract."""


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    retries: int,
    request_delay_seconds: float,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "User-Agent": "DAYJAVIEW-Kiwoom-Digital-Academy/1.0",
    }

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        throttle_requests(request_delay_seconds)
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
            if not isinstance(result, dict):
                raise CollectionError(f"{path}: response is not an object")
            if result.get("success") is not True:
                raise CollectionError(
                    f"{path}: success=false: {result.get('message', 'unknown error')}"
                )
            return result
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {403, 408, 425, 429, 500, 502, 503, 504}:
                break
        except (URLError, TimeoutError, json.JSONDecodeError, CollectionError) as exc:
            last_error = exc

        if attempt < retries:
            delay = min(8.0, 0.5 * (2**attempt)) + random.uniform(0.0, 0.25)
            time.sleep(delay)

    raise CollectionError(f"POST {path} failed after {retries + 1} attempts: {last_error}")


def throttle_requests(delay_seconds: float) -> None:
    """Apply one process-wide delay between outbound API calls."""
    global NEXT_REQUEST_AT
    if delay_seconds <= 0:
        return
    with REQUEST_LOCK:
        now = time.monotonic()
        wait_seconds = max(0.0, NEXT_REQUEST_AT - now)
        if wait_seconds:
            time.sleep(wait_seconds)
        NEXT_REQUEST_AT = time.monotonic() + delay_seconds


def parse_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        return None
    return datetime.strptime(text, "%Y%m%d").date().isoformat()


def parse_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{14}", text):
        return None
    return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=KST).isoformat()


def parse_stock_pairs(value: Any) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for source_order, part in enumerate(str(value or "").split("|")):
        part = part.strip()
        if not part:
            continue
        match = PAIR_RE.fullmatch(part)
        if match:
            code, name = match.groups()
        else:
            code, name = None, part
        pairs.append(
            {
                "sourceOrder": source_order,
                "name": name.strip(),
                "stockCode": code,
                "sourceUrl": (
                    f"https://new.infostock.co.kr/stockitem?code={code}"
                    if code
                    else None
                ),
            }
        )
    return pairs


def normalize_theme_detail(
    theme_id: str,
    response_data: dict[str, Any],
    captured_at: str,
) -> dict[str, Any]:
    theme = response_data.get("theme")
    history_items = response_data.get("items")
    stock_items = response_data.get("stockItems")
    if not isinstance(theme, dict):
        raise CollectionError(f"theme {theme_id}: missing theme object")
    if not isinstance(history_items, list):
        raise CollectionError(f"theme {theme_id}: missing history items")
    if not isinstance(stock_items, list):
        raise CollectionError(f"theme {theme_id}: missing stock items")
    if str(theme.get("code")) != theme_id:
        raise CollectionError(
            f"theme {theme_id}: response code mismatch ({theme.get('code')})"
        )

    history: list[dict[str, Any]] = []
    for source_order, item in enumerate(history_items):
        if not isinstance(item, dict):
            raise CollectionError(f"theme {theme_id}: invalid history item")
        history.append(
            {
                "sourceOrder": source_order,
                "sourceId": str(item.get("B2Bseq") or "") or None,
                "date": parse_date(item.get("showDate")),
                "sourceDate": str(item.get("showDate") or "") or None,
                "createdAt": parse_timestamp(item.get("createTime")),
                "updatedAt": parse_timestamp(item.get("lastUpdateTime")),
                "content": str(item.get("content") or "").strip(),
                "leaders": parse_stock_pairs(item.get("LEAD_STOCK")),
                "memberStocks": parse_stock_pairs(item.get("STOCKS")),
                "author": str(item.get("CREATE_WRITER") or "").strip() or None,
                "chartFlag": str(item.get("CHART") or "").strip() or None,
            }
        )

    related_stocks: list[dict[str, Any]] = []
    for source_order, item in enumerate(stock_items):
        if not isinstance(item, dict):
            raise CollectionError(f"theme {theme_id}: invalid related stock item")
        stock_code = str(item.get("code") or "").strip() or None
        related_stocks.append(
            {
                "sourceOrder": source_order,
                "name": str(item.get("name") or "").strip(),
                "stockCode": stock_code,
                "rationale": str(item.get("outline") or "").strip(),
                "sourceIndex": str(item.get("index") or "").strip() or None,
            }
        )

    hash_input = {
        "themeId": theme_id,
        "themeName": str(theme.get("name") or "").strip(),
        "description": str(theme.get("outline") or "").strip(),
        "history": history,
        "relatedStocks": related_stocks,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": SOURCE,
        "sourceType": "theme_detail",
        "sourceUrl": f"https://infostock.co.kr/Theme/ThemeDB/{theme_id}",
        "apiEndpoint": "/theme/detail",
        "capturedAt": captured_at,
        "themeId": theme_id,
        "themeName": hash_input["themeName"],
        "description": hash_input["description"],
        "historyComplete": True,
        "history": history,
        "relatedStocks": related_stocks,
        "contentHash": canonical_hash(hash_input),
    }


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def fetch_theme_index(
    base_url: str,
    *,
    timeout_seconds: float,
    retries: int,
    request_delay_seconds: float,
) -> list[dict[str, str]]:
    response = post_json(
        base_url,
        "/theme/all",
        {},
        timeout_seconds=timeout_seconds,
        retries=retries,
        request_delay_seconds=request_delay_seconds,
    )
    items = (response.get("data") or {}).get("items")
    if not isinstance(items, list) or not items:
        raise CollectionError("theme/all: missing items")

    index: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for source_order, item in enumerate(items):
        if not isinstance(item, dict):
            raise CollectionError("theme/all: invalid item")
        theme_id = str(item.get("code") or "").strip()
        theme_name = str(item.get("name") or "").strip()
        if not theme_id or not theme_name:
            raise CollectionError("theme/all: missing code or name")
        if theme_id in seen_ids:
            raise CollectionError(f"theme/all: duplicate code {theme_id}")
        seen_ids.add(theme_id)
        index.append(
            {
                "sourceOrder": source_order,
                "themeId": theme_id,
                "themeName": theme_name,
                "sourceUrl": f"https://infostock.co.kr/Theme/ThemeDB/{theme_id}",
            }
        )
    return index


def collect_one(
    base_url: str,
    theme: dict[str, str],
    *,
    timeout_seconds: float,
    retries: int,
    request_delay_seconds: float,
) -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc).isoformat()
    theme_id = theme["themeId"]
    response = post_json(
        base_url,
        "/theme/detail",
        {"code": theme_id, "idx": "0"},
        timeout_seconds=timeout_seconds,
        retries=retries,
        request_delay_seconds=request_delay_seconds,
    )
    detail = response.get("data")
    if not isinstance(detail, dict):
        raise CollectionError(f"theme {theme_id}: missing response data")
    normalized = normalize_theme_detail(theme_id, detail, captured_at)
    if normalized["themeName"] != theme["themeName"]:
        raise CollectionError(
            f"theme {theme_id}: index/detail name mismatch "
            f"({theme['themeName']} != {normalized['themeName']})"
        )
    return normalized


def validate_theme_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    history = payload.get("history") or []
    related_stocks = payload.get("relatedStocks") or []
    if payload.get("historyComplete") is not True:
        errors.append("history is not complete")
    if not payload.get("themeId") or not payload.get("themeName"):
        errors.append("theme identity missing")
    if not isinstance(history, list) or not isinstance(related_stocks, list):
        errors.append("theme collections invalid")
    return errors


def quality_summary(payload: dict[str, Any]) -> dict[str, int]:
    history = payload.get("history") or []
    related_stocks = payload.get("relatedStocks") or []
    fingerprints = [
        (item.get("date"), item.get("content"))
        for item in history
        if item.get("date") and item.get("content")
    ]
    return {
        "duplicateHistoryCount": len(fingerprints) - len(set(fingerprints)),
        "missingHistoryDateCount": sum(not item.get("date") for item in history),
        "missingHistoryContentCount": sum(not item.get("content") for item in history),
        "missingLeaderCodeCount": sum(
            not leader.get("stockCode")
            for item in history
            for leader in (item.get("leaders") or [])
        ),
        "missingRelatedStockCodeCount": sum(
            not stock.get("stockCode") for stock in related_stocks
        ),
    }


def completed_record(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "themeId": payload["themeId"],
        "themeName": payload["themeName"],
        "historyCount": len(payload["history"]),
        "relatedStockCount": len(payload["relatedStocks"]),
        "contentHash": payload["contentHash"],
        "quality": quality_summary(payload),
    }


def choose_themes(
    index: list[dict[str, str]], requested_ids: Iterable[str]
) -> list[dict[str, str]]:
    ids = [str(value) for value in requested_ids]
    if not ids:
        return index
    wanted = set(ids)
    selected = [theme for theme in index if theme["themeId"] in wanted]
    found = {theme["themeId"] for theme in selected}
    missing = sorted(wanted - found, key=int)
    if missing:
        raise CollectionError(f"unknown theme IDs: {', '.join(missing)}")
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("INFOSTOCK_API_BASE_URL") or DEFAULT_BASE_URL,
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("INFOSTOCK_IMPORT_DIR") or DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--theme-id",
        action="append",
        default=[],
        help="Collect only this theme ID; repeat for multiple IDs. Omit for full sync.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--request-delay-ms",
        type=float,
        default=2000.0,
        help="Minimum process-wide delay between API calls.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse valid theme JSON already present in the output directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.workers <= 8:
        raise CollectionError("workers must be between 1 and 8")
    output_dir = Path(args.output_dir).resolve()
    started_at = datetime.now(timezone.utc)

    index = fetch_theme_index(
        args.base_url,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        request_delay_seconds=args.request_delay_ms / 1000.0,
    )
    selected = choose_themes(index, args.theme_id)
    index_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "source": SOURCE,
        "sourceType": "theme_index",
        "sourceUrl": "https://infostock.co.kr/Theme/ThemeDB/ThemeAll",
        "apiEndpoint": "/theme/all",
        "capturedAt": started_at.isoformat(),
        "themeCount": len(index),
        "items": index,
        "contentHash": canonical_hash(index),
    }
    write_json_atomic(output_dir / "theme-index.json", index_payload)

    completed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    total = len(selected)
    pending: list[dict[str, str]] = []
    if args.resume:
        for theme in selected:
            existing_path = output_dir / f"theme-{theme['themeId']}.json"
            try:
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
                if (
                    existing.get("sourceType") != "theme_detail"
                    or existing.get("themeId") != theme["themeId"]
                    or existing.get("themeName") != theme["themeName"]
                    or validate_theme_payload(existing)
                ):
                    raise ValueError("existing payload failed validation")
                completed.append(completed_record(existing))
            except (OSError, ValueError, json.JSONDecodeError):
                pending.append(theme)
    else:
        pending = selected

    print(
        f"Collecting {len(pending)} of {total} themes with {args.workers} workers; "
        f"resumed={len(completed)}",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                collect_one,
                args.base_url,
                theme,
                timeout_seconds=args.timeout_seconds,
                retries=args.retries,
                request_delay_seconds=args.request_delay_ms / 1000.0,
            ): theme
            for theme in pending
        }
        for sequence, future in enumerate(as_completed(futures), start=1):
            theme = futures[future]
            try:
                payload = future.result()
                errors = validate_theme_payload(payload)
                if errors:
                    raise CollectionError("; ".join(errors))
                write_json_atomic(
                    output_dir / f"theme-{payload['themeId']}.json",
                    payload,
                )
                completed.append(completed_record(payload))
            except Exception as exc:  # keep the remaining full sync running
                failed.append(
                    {
                        "themeId": theme["themeId"],
                        "themeName": theme["themeName"],
                        "error": str(exc),
                    }
                )
            processed = len(completed) + len(failed)
            if sequence % 25 == 0 or sequence == len(pending):
                print(
                    f"Progress {processed}/{total}; "
                    f"completed={len(completed)} failed={len(failed)}",
                    flush=True,
                )

    completed.sort(key=lambda item: int(item["themeId"]))
    failed.sort(key=lambda item: int(item["themeId"]))
    finished_at = datetime.now(timezone.utc)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "dataset": "infostock-theme-full-sync",
        "apiBaseUrl": args.base_url,
        "startedAt": started_at.isoformat(),
        "finishedAt": finished_at.isoformat(),
        "requestedThemeCount": total,
        "completedThemeCount": len(completed),
        "failedThemeCount": len(failed),
        "historyCount": sum(item["historyCount"] for item in completed),
        "relatedStockCount": sum(item["relatedStockCount"] for item in completed),
        "quality": {
            key: sum(item["quality"][key] for item in completed)
            for key in (
                "duplicateHistoryCount",
                "missingHistoryDateCount",
                "missingHistoryContentCount",
                "missingLeaderCodeCount",
                "missingRelatedStockCodeCount",
            )
        },
        "index": {
            "themeCount": len(index),
            "contentHash": index_payload["contentHash"],
        },
        "themes": completed,
        "failures": failed,
    }
    write_json_atomic(output_dir / "manifest.json", manifest)

    print(
        json.dumps(
            {
                "outputDir": str(output_dir),
                "completed": len(completed),
                "failed": len(failed),
                "history": manifest["historyCount"],
                "relatedStocks": manifest["relatedStockCount"],
                "seconds": round((finished_at - started_at).total_seconds(), 2),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CollectionError as exc:
        print(f"collection failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
