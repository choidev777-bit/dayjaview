#!/usr/bin/env python3
"""인포스탁 테마 history 전수에 소재 온톨로지 라벨을 붙인다 (E-17).

외부 호출 없이 로컬 수집본(data/infostock/import)만 읽는다. 산출물은
research/ontology/ 아래 labels.jsonl(전수 라벨)과 coverage_report.json이며,
기타(미분류) 비율 게이트가 GO가 아니면 종료 코드 2로 실패한다.
같은 수집본·같은 어휘/변환 버전이면 같은 산출물이 재현된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.infostock.errors import FixtureValidationError
from packages.infostock.existing_collection import load_existing_collection
from packages.ontology import label_history_records, records_from_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="테마 history 원인문 전수를 통제어휘로 라벨링합니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "infostock" / "import",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology",
    )
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        bundle = load_existing_collection(arguments.input_dir)
    except (OSError, FixtureValidationError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2
    rows, report = label_history_records(records_from_bundle(bundle))
    report["datasetHash"] = bundle.dataset_hash
    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "labels.jsonl"
    report_path = output_dir / "coverage_report.json"
    with labels_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _print(
        {
            "status": "SUCCEEDED" if report["gate"] == "GO" else "GATE_FAILED",
            "gate": report["gate"],
            "totalRecords": report["totalRecords"],
            "unclassifiedRatio": report["unclassifiedRatio"],
            "labelsPath": str(labels_path),
            "reportPath": str(report_path),
        }
    )
    return 0 if report["gate"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
