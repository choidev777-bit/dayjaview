#!/usr/bin/env python3
"""Finalize a stopped DAYJAVIEW capture and write one validation report.

The command never calls market, order, account, or credential APIs.  It only
operates on a run that is already marked COMPLETED, rebuilds its manifest from
the current on-disk files, and performs the full read-only verification suite.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from market_replay_common import ReplayStore, iso_utc, latest_run_id
from replay_market import (
    VerificationError,
    audit_capture,
    audit_gap_recovery,
    audit_supplemental_capture,
    prove_combined_service_replay,
    prove_replay_files,
    verify_database,
)


def require_completed_run(database: Path, run_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT trade_date,status,started_at,finished_at,error "
            "FROM collection_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise VerificationError(f"unknown run: {run_id}")
    if row[1] != "COMPLETED" or not row[3]:
        raise VerificationError(
            f"run is not complete: status={row[1]!r}, finishedAt={row[3]!r}"
        )
    return {
        "tradeDate": row[0],
        "status": row[1],
        "startedAt": row[2],
        "finishedAt": row[3],
        "error": row[4],
    }


def finalize(
    output_dir: Path,
    *,
    run_id: str | None = None,
    log_paths: list[Path] | None = None,
    supplemental_output_dir: Path | None = None,
    supplemental_run_id: str | None = None,
    recovery_output_dir: Path | None = None,
    recovery_run_id: str | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    database = output_dir / "market-replay.sqlite3"
    if not database.is_file():
        raise VerificationError(f"database does not exist: {database}")
    resolved_run = run_id or latest_run_id(database)
    run = require_completed_run(database, resolved_run)

    # This also upgrades an older/incomplete manifest format without changing
    # any captured event or minute bar.
    with ReplayStore(output_dir) as store:
        manifest = store.write_manifest(resolved_run)

    integrity = verify_database(database, resolved_run)
    audit = audit_capture(
        database,
        resolved_run,
        log_paths,
        integrity_result=integrity,
    )
    proof = prove_replay_files(database, resolved_run)
    report = {
        "dataset": "dayjaview-one-time-market-replay-validation",
        "generatedAt": iso_utc(),
        "runId": resolved_run,
        "tradeDate": run["tradeDate"],
        "passed": bool(integrity["passed"] and audit["passed"] and proof["passed"]),
        "run": run,
        "manifest": {
            "status": manifest.get("status"),
            "events": manifest.get("events"),
            "minuteBars": manifest.get("minuteBars"),
            "references": manifest.get("references"),
            "files": manifest.get("files"),
        },
        "verify": integrity,
        "audit": audit,
        "prove": proof,
    }
    if supplemental_output_dir is not None:
        supplemental_output_dir = supplemental_output_dir.resolve()
        supplemental_database = supplemental_output_dir / "market-replay.sqlite3"
        if not supplemental_database.is_file():
            raise VerificationError(
                f"supplemental database does not exist: {supplemental_database}"
            )
        resolved_supplemental_run = (
            supplemental_run_id or latest_run_id(supplemental_database)
        )
        supplemental_run = require_completed_run(
            supplemental_database, resolved_supplemental_run
        )
        with ReplayStore(supplemental_output_dir) as store:
            supplemental_manifest = store.write_manifest(resolved_supplemental_run)
        supplemental_integrity = verify_database(
            supplemental_database, resolved_supplemental_run
        )
        supplemental_audit = audit_supplemental_capture(
            supplemental_database,
            resolved_supplemental_run,
            integrity_result=supplemental_integrity,
        )
        supplemental_proof = prove_replay_files(
            supplemental_database, resolved_supplemental_run
        )
        combined_proof = prove_combined_service_replay(
            database,
            main_run_id=resolved_run,
            supplemental_database=supplemental_database,
            supplemental_run_id=resolved_supplemental_run,
        )
        report["supplemental"] = {
            "runId": resolved_supplemental_run,
            "run": supplemental_run,
            "manifest": {
                "status": supplemental_manifest.get("status"),
                "events": supplemental_manifest.get("events"),
                "files": supplemental_manifest.get("files"),
            },
            "verify": supplemental_integrity,
            "audit": supplemental_audit,
            "prove": supplemental_proof,
        }
        report["combinedReplay"] = combined_proof
        report["operationalPassed"] = bool(
            integrity["passed"]
            and audit["passed"]
            and proof["passed"]
            and supplemental_integrity["passed"]
            and supplemental_audit["operationalPassed"]
            and supplemental_proof["passed"]
            and combined_proof["passed"]
        )
        report["passed"] = bool(
            report["operationalPassed"] and supplemental_audit["passed"]
        )
    if recovery_output_dir is not None:
        recovery_output_dir = recovery_output_dir.resolve()
        recovery_database = recovery_output_dir / "market-replay.sqlite3"
        if not recovery_database.is_file():
            raise VerificationError(
                f"gap recovery database does not exist: {recovery_database}"
            )
        resolved_recovery_run = recovery_run_id or latest_run_id(recovery_database)
        recovery_run = require_completed_run(recovery_database, resolved_recovery_run)
        with ReplayStore(recovery_output_dir) as store:
            recovery_manifest = store.write_manifest(resolved_recovery_run)
        recovery_integrity = verify_database(recovery_database, resolved_recovery_run)
        recovery_audit = audit_gap_recovery(
            recovery_database,
            resolved_recovery_run,
            integrity_result=recovery_integrity,
        )
        recovery_proof = prove_replay_files(
            recovery_database, resolved_recovery_run
        )
        report["recovery"] = {
            "runId": resolved_recovery_run,
            "run": recovery_run,
            "manifest": {
                "status": recovery_manifest.get("status"),
                "events": recovery_manifest.get("events"),
                "files": recovery_manifest.get("files"),
            },
            "verify": recovery_integrity,
            "audit": recovery_audit,
            "prove": recovery_proof,
        }
        supplemental_database_for_merge = (
            supplemental_output_dir / "market-replay.sqlite3"
            if supplemental_output_dir is not None
            else None
        )
        supplemental_run_for_merge = (
            report["supplemental"]["runId"] if "supplemental" in report else None
        )
        combined_proof = prove_combined_service_replay(
            database,
            main_run_id=resolved_run,
            supplemental_database=supplemental_database_for_merge,
            supplemental_run_id=supplemental_run_for_merge,
            recovery_database=recovery_database,
            recovery_run_id=resolved_recovery_run,
        )
        report["combinedReplay"] = combined_proof
        previous_operational = report.get(
            "operationalPassed",
            bool(integrity["passed"] and audit["passed"] and proof["passed"]),
        )
        report["operationalPassed"] = bool(
            previous_operational
            and recovery_integrity["passed"]
            and recovery_audit["operationalPassed"]
            and recovery_proof["passed"]
            and combined_proof["passed"]
        )
        exact_supplemental_passed = (
            report["supplemental"]["audit"]["passed"]
            if "supplemental" in report
            else True
        )
        report["passed"] = bool(
            report["operationalPassed"] and exact_supplemental_passed
        )
    temporary = output_dir / "validation-report.json.tmp"
    destination = output_dir / "validation-report.json"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--log", action="append", type=Path, default=None)
    parser.add_argument("--supplemental-output-dir", type=Path)
    parser.add_argument("--supplemental-run-id")
    parser.add_argument("--recovery-output-dir", type=Path)
    parser.add_argument("--recovery-run-id")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    report = finalize(
        args.output_dir,
        run_id=args.run_id,
        log_paths=args.log,
        supplemental_output_dir=args.supplemental_output_dir,
        supplemental_run_id=args.supplemental_run_id,
        recovery_output_dir=args.recovery_output_dir,
        recovery_run_id=args.recovery_run_id,
    )
    summary = {
        "report": str((args.output_dir / "validation-report.json").resolve()),
        "runId": report["runId"],
        "tradeDate": report["tradeDate"],
        "passed": report["passed"],
        "eventCount": report["verify"]["eventCount"],
        "serviceReplay": report["prove"]["serviceReplay"],
        "minuteBars": report["verify"]["minuteBars"],
        "failedAuditChecks": [
            check["name"] for check in report["audit"]["checks"] if not check["passed"]
        ],
        "warnings": report["audit"]["warnings"],
    }
    if "supplemental" in report:
        summary["operationalPassed"] = report["operationalPassed"]
        summary["supplemental"] = {
            "runId": report["supplemental"]["runId"],
            "eventCount": report["supplemental"]["verify"]["eventCount"],
            "operationalPassed": report["supplemental"]["audit"]["operationalPassed"],
            "exactFullSessionCoverage": report["supplemental"]["audit"][
                "exactFullSessionCoverage"
            ],
        }
        summary["combinedReplay"] = report["combinedReplay"]
    if "recovery" in report:
        summary["operationalPassed"] = report["operationalPassed"]
        summary["recovery"] = {
            "runId": report["recovery"]["runId"],
            "eventCount": report["recovery"]["verify"]["eventCount"],
            "recoveredStateCount": report["recovery"]["audit"][
                "recoveredStateCount"
            ],
            "recoveredStateStockCount": report["recovery"]["audit"][
                "recoveredStateStockCount"
            ],
            "exactLiveRecovery": report["recovery"]["audit"]["exactLiveRecovery"],
        }
        summary["combinedReplay"] = report["combinedReplay"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"market replay finalization failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
