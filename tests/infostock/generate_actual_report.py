"""Generate committed, path-free audit reports from an explicitly supplied corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.infostock import (
    ExistingCollectionPolicy,
    human_quality_report,
    load_existing_collection,
    machine_quality_report,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).parent / "reports"
    )
    args = parser.parse_args()
    bundle = load_existing_collection(
        args.collection_dir, ExistingCollectionPolicy()
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "actual-collection-audit.json").write_text(
        json.dumps(
            machine_quality_report(bundle),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (args.output_dir / "actual-collection-audit.md").write_text(
        human_quality_report(bundle), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
