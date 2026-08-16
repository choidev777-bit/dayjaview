#!/usr/bin/env python3
"""KRX 일별매매 봉투에서 종목명 이력을 뽑는다 (회사 온톨로지 단계 2).

인포스탁은 과거 기록의 종목명을 현재 이름으로 소급 정규화한 코드가 있어
그 원천만으로는 사명 이력을 만들 수 없다. E-16이 이미 받아 둔 KRX 봉투는
거래일마다 그날의 종목명을 담고 있으므로 이름의 시작·끝 거래일이 나온다.

외부 API를 호출하지 않는다. 같은 봉투면 같은 색인이다. 산출물은
`build_company_master.py --krx-names`가 읽는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.historical_data.models import HistoricalDataError
from packages.ontology.krx_names import (
    KRX_NAME_INDEX_VERSION,
    name_index_payload,
    scan_krx_name_windows,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KRX 일별매매 봉투에서 종목명 이력 색인을 만듭니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "data" / "krx-daily",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "krx_name_windows.json",
    )
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    paths = sorted(Path(arguments.input_dir).glob("*/*/*.json"))
    if not paths:
        _print(
            {
                "status": "FAILED",
                "messageKo": f"{arguments.input_dir}에 KRX 봉투가 없습니다.",
            }
        )
        return 2
    try:
        index = scan_krx_name_windows(paths)
    except (OSError, ValueError, HistoricalDataError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2

    payload = name_index_payload(index)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    renamed = sum(
        1
        for code, windows in index.by_code().items()
        if len(windows) > 1
    )
    _print(
        {
            "status": "SUCCEEDED",
            "indexVersion": KRX_NAME_INDEX_VERSION,
            "envelopes": len(paths),
            "stockCodes": len(index.by_code()),
            "nameWindows": len(index.windows),
            "codesWithRename": renamed,
            "marketLastDates": payload["marketLastDates"],
            "output": str(arguments.output),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
