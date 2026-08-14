#!/usr/bin/env python3
"""Fail-closed S1 entrypoint for the Daily full-backfill browser contract.

The authenticated browser adapter and scheduler remain S6-owned. This command
therefore reports the checkpoint/blockers and never opens the live URL itself.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

def main() -> int:
    infostock = importlib.import_module("packages.infostock")
    try:
        infostock.InfostockAccessPolicy.require_daily_browser_collection()
    except infostock.DataRightsBlockedError as exc:
        print(
            json.dumps(
                {
                    "component": "DAILY_FEATURED_THEME",
                    "status": "BLOCKED",
                    "blockers": ["B-INFOSTOCK-AUTH", "B-DATA-RIGHTS"],
                    "nextPage": 1,
                    "coverageComplete": False,
                    "liveRequestAttempted": False,
                    "messageKo": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    raise RuntimeError("S6 browser adapter가 연결되지 않았습니다.")


if __name__ == "__main__":
    raise SystemExit(main())
