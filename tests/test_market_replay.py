import importlib.util
import asyncio
import contextlib
import json
import socket
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from market_replay_common import (  # noqa: E402
    ReplayStore,
    iter_events,
    payload_hash,
    source_clock_to_utc,
)
from collect_market_replay import (  # noqa: E402
    CandidateManager,
    iter_stock_rows,
    parse_condition_list,
    stock_code_from_row,
)
from replay_market import audit_capture, prove_replay_files, verify_database  # noqa: E402
from replay_market import (  # noqa: E402
    VerificationError,
    audit_gap_recovery,
    audit_supplemental_capture,
    iter_combined_selected_events,
    iter_selected_events,
    prove_combined_service_replay,
    prove_websocket_replay,
    serve_event_factory,
)
from repair_market_backfill import select_repair_codes  # noqa: E402
from finalize_market_replay import finalize  # noqa: E402
from collect_market_snapshot_supplement import MainCaptureFollower  # noqa: E402
from collect_market_gap_recovery import (  # noqa: E402
    reconstruct_intended_codes_by_minute,
)

import websockets  # noqa: E402


class MarketReplayStoreTests(unittest.TestCase):
    def test_gap_recovery_reconstructs_active_theme_minus_0b_by_minute(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="candidate.rest",
                    source="fixture",
                    payload={"apiId": "ka10032"},
                    received_at="2026-08-14T00:00:10+00:00",
                    stock_code="000001",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="subscription.changed",
                    source="fixture",
                    payload={"kind": "stock_trade", "targets": ["000001"]},
                    received_at="2026-08-14T00:00:20+00:00",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="candidate.condition",
                    source="fixture",
                    payload={"values": {"843": "D"}},
                    received_at="2026-08-14T00:00:30+00:00",
                    stock_code="000004",
                )
                store.finish_run(run_id, status="COMPLETED")
            result = reconstruct_intended_codes_by_minute(
                output / "market-replay.sqlite3",
                run_id=run_id,
                trade_date=date(2026, 8, 14),
                master_codes={"000001", "000002", "000003", "000004"},
                stock_to_themes={"000001": {"t1"}, "000004": {"t2"}},
                theme_members={"t1": {"000001", "000002", "000003"}, "t2": {"000004"}},
                start_hhmm="0900",
                end_hhmm="0901",
                candidate_ttl_seconds=30,
            )
            self.assertEqual(result["0900"], {"000002", "000003"})
            self.assertEqual(result["0901"], set())

    def test_gap_recovery_audit_and_combined_replay_use_historical_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main"
            recovery = root / "recovery"
            with ReplayStore(main, batch_size=1) as store:
                main_run = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=main_run,
                    event_type="market.trade",
                    source="fixture-main",
                    payload={"position": 1},
                    received_at="2026-08-14T00:00:00+00:00",
                )
                store.append_event(
                    run_id=main_run,
                    event_type="market.trade",
                    source="fixture-main",
                    payload={"position": 3},
                    received_at="2026-08-14T00:02:00+00:00",
                )
                store.finish_run(main_run, status="COMPLETED")
            replay_at = "2026-08-14T00:01:59.999999+00:00"
            captured_at = "2026-08-14T07:00:00+00:00"
            raw = {
                "tm": "090100",
                "cur_prc": "1000",
                "pre_rt": "1.00",
                "pri_sel_bid_unit": "1005",
                "pri_buy_bid_unit": "1000",
                "cntr_trde_qty": "10",
                "acc_trde_qty": "100",
                "acc_trde_prica": "1",
                "cntr_str": "101.00",
            }
            with ReplayStore(recovery, batch_size=1) as store:
                recovery_run = store.start_run(
                    trade_date=date(2026, 8, 14),
                    mode="fixture",
                    settings={
                        "purpose": "ka10084 one-minute recovery for the pre-sidecar snapshot gap",
                        "parentRunId": main_run,
                        "sourceApi": "ka10084",
                        "gapStart": "2026-08-14T09:00:00+09:00",
                        "gapEnd": "2026-08-14T10:09:59.999999+09:00",
                        "resolutionSeconds": 60,
                        "exactFullSessionCoverage": False,
                        "orderApisEnabled": False,
                        "targetCount": 1,
                        "limitations": ["fixture limitation"],
                    },
                )
                store.append_event(
                    run_id=recovery_run,
                    event_type="source.status",
                    source="fixture",
                    payload={"status": "GAP_RECOVERY_STARTED"},
                    received_at=captured_at,
                )
                store.append_event(
                    run_id=recovery_run,
                    event_type="kiwoom.ka10084.raw",
                    source="fixture",
                    payload={"apiId": "ka10084"},
                    received_at=captured_at,
                    stock_code="005930",
                )
                store.append_event(
                    run_id=recovery_run,
                    event_type="market.minute_state.recovered",
                    source="kiwoom_rest_historical_recovery",
                    payload={
                        "position": 2,
                        "apiId": "ka10084",
                        "source": "HISTORICAL_MINUTE_RECOVERY",
                        "replayAt": replay_at,
                        "capturedAt": captured_at,
                        "resolutionSeconds": 60,
                        "exactLiveSnapshot": False,
                        "selection": {
                            "reason": "ACTIVE_THEME_NON_0B",
                            "asOfMinute": "0901",
                        },
                        "raw": raw,
                    },
                    occurred_at=replay_at,
                    received_at=captured_at,
                    stock_code="005930",
                )
                store.append_event(
                    run_id=recovery_run,
                    event_type="gap_recovery.stock.completed",
                    source="fixture",
                    payload={"stateCount": 1},
                    received_at=captured_at,
                    stock_code="005930",
                )
                store.append_event(
                    run_id=recovery_run,
                    event_type="source.status",
                    source="fixture",
                    payload={"status": "GAP_RECOVERY_FINISHED"},
                    received_at=captured_at,
                )
                store.finish_run(recovery_run, status="COMPLETED")

            audit = audit_gap_recovery(
                recovery / "market-replay.sqlite3", recovery_run
            )
            self.assertTrue(audit["passed"])
            self.assertFalse(audit["exactLiveRecovery"])
            events = list(
                iter_combined_selected_events(
                    main / "market-replay.sqlite3",
                    main_run_id=main_run,
                    recovery_database=recovery / "market-replay.sqlite3",
                    recovery_run_id=recovery_run,
                    event_types={
                        "market.trade",
                        "market.minute_state.recovered",
                    },
                    from_time=None,
                    to_time=None,
                )
            )
            self.assertEqual([event.payload["position"] for event in events], [1, 2, 3])
            self.assertEqual(events[1].received_at, replay_at)
            proof = prove_combined_service_replay(
                main / "market-replay.sqlite3",
                main_run_id=main_run,
                recovery_database=recovery / "market-replay.sqlite3",
                recovery_run_id=recovery_run,
            )
            self.assertTrue(proof["passed"])
            self.assertEqual(proof["eventCount"], 5)
            self.assertEqual(
                proof["eventsByType"]["market.minute_state.recovered"], 1
            )
            report = finalize(
                main,
                run_id=main_run,
                recovery_output_dir=recovery,
                recovery_run_id=recovery_run,
            )
            self.assertIn("recovery", report)
            self.assertTrue(report["recovery"]["audit"]["passed"])
            self.assertTrue(report["combinedReplay"]["passed"])

    def test_supplement_audit_separates_operational_success_from_known_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            now = "2026-08-14T01:10:18+00:00"
            raw = {
                key: "1"
                for key in (
                    "stk_cd",
                    "dt",
                    "cntr_tm",
                    "cur_prc",
                    "flu_rt",
                    "pred_pre",
                    "pred_pre_sig",
                    "trde_qty",
                    "trde_prica",
                    "open_pric",
                    "high_pric",
                    "low_pric",
                    "cntr_str",
                    "mac",
                )
            }
            raw["stk_cd"] = "005930"
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14),
                    mode="fixture",
                    settings={
                        "purpose": "ka10095 non-0B active-theme snapshot supplement",
                        "parentRunId": "main-run",
                        "orderApisEnabled": False,
                        "knownGapBeforeStart": True,
                        "pollSeconds": 30,
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="source.status",
                    source="fixture",
                    payload={
                        "status": "SNAPSHOT_SUPPLEMENT_STARTED",
                        "knownGap": {
                            "from": "2026-08-14T09:00:00+09:00",
                            "to": "2026-08-14T10:09:17+09:00",
                            "reason": "fixture",
                        },
                    },
                    received_at=now,
                )
                store.append_event(
                    run_id=run_id,
                    event_type="kiwoom.ka10095.raw",
                    source="fixture",
                    payload={"apiId": "ka10095"},
                    received_at=now,
                )
                store.append_event(
                    run_id=run_id,
                    event_type="market.snapshot",
                    source="fixture",
                    payload={
                        "apiId": "ka10095",
                        "source": "REST_SNAPSHOT",
                        "raw": raw,
                    },
                    received_at=now,
                    stock_code="005930",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="supplemental.coverage",
                    source="fixture",
                    payload={
                        "cycle": 0,
                        "requestedStockCount": 1,
                        "returnedStockCount": 1,
                        "batchCount": 1,
                        "failedBatchCount": 0,
                    },
                    received_at=now,
                )
                store.append_event(
                    run_id=run_id,
                    event_type="source.status",
                    source="fixture",
                    payload={"status": "SNAPSHOT_SUPPLEMENT_FINISHED"},
                    received_at=now,
                )
                store.finish_run(run_id, status="COMPLETED")
            result = audit_supplemental_capture(
                output / "market-replay.sqlite3", run_id
            )
            self.assertTrue(result["operationalPassed"])
            self.assertFalse(result["exactFullSessionCoverage"])
            self.assertFalse(result["passed"])

    def test_combined_replay_merges_by_receive_time_with_one_sequence_space(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main"
            supplemental = root / "supplemental"
            with ReplayStore(main, batch_size=1) as store:
                main_run = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=main_run,
                    event_type="market.trade",
                    source="fixture-main",
                    payload={"position": 1},
                    received_at="2026-08-14T00:00:00+00:00",
                )
                store.append_event(
                    run_id=main_run,
                    event_type="market.trade",
                    source="fixture-main",
                    payload={"position": 3},
                    received_at="2026-08-14T00:00:02+00:00",
                )
                store.finish_run(main_run, status="COMPLETED")
            with ReplayStore(supplemental, batch_size=1) as store:
                supplemental_run = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                original = store.append_event(
                    run_id=supplemental_run,
                    event_type="market.snapshot",
                    source="fixture-supplemental",
                    payload={"position": 2},
                    received_at="2026-08-14T00:00:01+00:00",
                )
                store.finish_run(supplemental_run, status="COMPLETED")

            events = list(
                iter_combined_selected_events(
                    main / "market-replay.sqlite3",
                    main_run_id=main_run,
                    supplemental_database=supplemental / "market-replay.sqlite3",
                    supplemental_run_id=supplemental_run,
                    event_types={"market.trade", "market.snapshot"},
                    from_time=None,
                    to_time=None,
                )
            )
            self.assertEqual([event.payload["position"] for event in events], [1, 2, 3])
            self.assertEqual([event.sequence for event in events], [1, 2, 3])
            self.assertEqual(len({event.run_id for event in events}), 1)
            self.assertTrue(events[0].run_id.startswith("combined:"))
            self.assertEqual(events[1].payload_sha256, original.payload_sha256)
            proof = prove_combined_service_replay(
                main / "market-replay.sqlite3",
                main_run_id=main_run,
                supplemental_database=supplemental / "market-replay.sqlite3",
                supplemental_run_id=supplemental_run,
            )
            self.assertTrue(proof["passed"])
            self.assertEqual(proof["eventCount"], 3)
            self.assertEqual(proof["eventsByType"], {"market.snapshot": 1, "market.trade": 2})

    def test_snapshot_supplement_selects_active_theme_members_outside_0b(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            now = datetime.now(timezone.utc)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="reference.stock_master",
                    source="fixture",
                    payload={
                        "response": {
                            "list": [
                                {"code": "000001"},
                                {"code": "000002"},
                            ]
                        }
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="reference.infostock_theme",
                    source="fixture",
                    payload={
                        "content": {
                            "sourceType": "theme_detail",
                            "themeId": "theme-1",
                            "relatedStocks": [
                                {"stockCode": "000001"},
                                {"stockCode": "000002"},
                                {"stockCode": "000003"},
                            ],
                        }
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="candidate.rest",
                    source="fixture",
                    payload={"apiId": "ka10032", "rank": 1, "raw": {}},
                    received_at=now.isoformat(),
                    stock_code="000001",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="candidate.condition",
                    source="fixture",
                    payload={
                        "item": "000003",
                        "type": "02",
                        "values": {"841": "7", "843": "D"},
                    },
                    received_at=now.isoformat(),
                    stock_code="000003",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="subscription.changed",
                    source="fixture",
                    payload={
                        "kind": "stock_trade",
                        "targets": ["000001"],
                    },
                    received_at=now.isoformat(),
                )

            follower = MainCaptureFollower(output / "market-replay.sqlite3")
            try:
                selection = follower.selection(now)
            finally:
                follower.close()
            self.assertEqual(selection["activeCandidates"], {"000001"})
            self.assertEqual(selection["supplementalStocks"], ["000002"])

    def test_verify_detects_exact_sensitive_keys_not_benign_authorization_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="reference.infostock_theme",
                    source="fixture",
                    payload={"collectionAuthorization": "APPROVED"},
                )
                store.append_event(
                    run_id=run_id,
                    event_type="source.error",
                    source="fixture",
                    payload={"token": "fixture-secret-value"},
                )
                store.finish_run(run_id, status="COMPLETED")

            result = verify_database(output / "market-replay.sqlite3", run_id)
            self.assertEqual(result["sensitivePayloads"]["count"], 1)
            self.assertEqual(result["sensitivePayloads"]["sequences"], [2])

    def test_finalizer_refuses_running_run_and_writes_report_for_completed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="market.trade",
                    source="fixture",
                    payload={"item": "005930"},
                )
            with self.assertRaises(VerificationError):
                finalize(output, run_id=run_id)

            with ReplayStore(output, batch_size=1) as store:
                store.finish_run(run_id, status="COMPLETED")
            report = finalize(output, run_id=run_id)
            self.assertFalse(report["passed"])
            self.assertTrue(report["prove"]["passed"])
            self.assertTrue((output / "validation-report.json").is_file())
            self.assertIn(
                "sequencePayloadHash", report["manifest"]["events"]
            )

    def test_replay_time_filter_uses_indexable_inclusive_local_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                for sequence, received_at in enumerate(
                    (
                        "2026-08-13T23:59:59.999000+00:00",
                        "2026-08-14T00:00:00.100000+00:00",
                        "2026-08-14T00:00:01.999000+00:00",
                        "2026-08-14T00:00:02+00:00",
                    )
                ):
                    store.append_event(
                        run_id=run_id,
                        event_type="market.trade",
                        source="fixture",
                        payload={"value": sequence},
                        received_at=received_at,
                    )
                store.finish_run(run_id, status="COMPLETED")

            selected = list(
                iter_selected_events(
                    output / "market-replay.sqlite3",
                    run_id=run_id,
                    event_types={"market.trade"},
                    from_time="09:00:00",
                    to_time="09:00:01",
                )
            )
            self.assertEqual([event.payload["value"] for event in selected], [1, 2])

    def test_prove_matches_database_ndjson_manifest_and_service_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="market.trade",
                    source="fixture",
                    payload={"item": "005930", "values": {"20": "090001"}},
                    stock_code="005930",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="kiwoom.websocket.raw",
                    source="fixture",
                    payload={"trnm": "REAL"},
                )
                store.finish_run(run_id, status="COMPLETED")

            proof = prove_replay_files(output / "market-replay.sqlite3", run_id)
            self.assertTrue(proof["passed"])
            self.assertEqual(proof["allEvents"]["count"], 2)
            self.assertEqual(proof["serviceReplay"]["count"], 1)

    def test_backfill_repair_selects_only_incomplete_master_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="reference.stock_master",
                    source="fixture",
                    payload={
                        "response": {
                            "list": [
                                {"code": "005930"},
                                {"code": "A000660"},
                                {"code": "0000D0"},
                            ]
                        }
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="backfill.minute.completed",
                    source="fixture",
                    payload={"barCount": 381},
                    stock_code="005930",
                )
                self.assertEqual(
                    select_repair_codes(store.connection, run_id), ["000660"]
                )
                self.assertEqual(
                    select_repair_codes(store.connection, run_id, scope="all"),
                    ["000660", "005930"],
                )

    def test_store_replays_the_exact_payload_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14),
                    mode="fixture",
                    settings={"orderApisEnabled": False},
                )
                payload = {"values": {"20": "090001", "10": "+71000"}}
                record = store.append_event(
                    run_id=run_id,
                    event_type="market.trade",
                    source="fixture",
                    payload=payload,
                    received_at="2026-08-14T00:00:01+00:00",
                    occurred_at="2026-08-14T00:00:01+00:00",
                    stock_code="A005930",
                )
                store.finish_run(run_id, status="COMPLETED")

            replayed = list(iter_events(output / "market-replay.sqlite3"))
            self.assertEqual(len(replayed), 1)
            self.assertEqual(replayed[0].payload, payload)
            self.assertEqual(replayed[0].payload_sha256, payload_hash(payload))
            self.assertEqual(replayed[0].stock_code, "005930")
            self.assertEqual(replayed[0].sequence, record.sequence)
            envelope = json.loads((output / "events.ndjson").read_text(encoding="utf-8"))
            self.assertEqual(envelope["payload"], payload)

    def test_verify_detects_payload_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="market.index",
                    source="fixture",
                    payload={"value": 1},
                )
                store.finish_run(run_id, status="COMPLETED")
            database = output / "market-replay.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE events SET payload_json=? WHERE run_id=?",
                ('{"value":2}', run_id),
            )
            connection.commit()
            connection.close()

            result = verify_database(database, run_id)
            self.assertFalse(result["passed"])
            hashes = next(item for item in result["checks"] if item["name"] == "payload_hashes")
            self.assertFalse(hashes["passed"])

    def test_completeness_audit_rejects_integrity_only_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=1) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                store.append_event(
                    run_id=run_id,
                    event_type="market.trade",
                    source="fixture",
                    payload={"item": "005930"},
                    stock_code="005930",
                )
                store.finish_run(run_id, status="COMPLETED")

            result = audit_capture(output / "market-replay.sqlite3", run_id)
            self.assertFalse(result["passed"])
            required = next(
                item for item in result["checks"] if item["name"] == "required_event_types"
            )
            self.assertIn("market.index", required["details"]["missing"])

    def test_completeness_audit_accepts_full_session_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with ReplayStore(output, batch_size=50) as store:
                run_id = store.start_run(
                    trade_date=date(2026, 8, 14), mode="fixture", settings={}
                )
                theme_hash_input = {
                    "themeId": "1",
                    "themeName": "fixture-theme",
                    "description": "fixture",
                    "history": [],
                    "relatedStocks": [
                        {"stockCode": "005930"},
                        *[
                            {"stockCode": f"{value:06d}"}
                            for value in range(180)
                        ],
                    ],
                }
                theme_content = {
                    **theme_hash_input,
                    "sourceType": "theme_detail",
                    "contentHash": payload_hash(theme_hash_input),
                }
                store.append_event(
                    run_id=run_id,
                    event_type="reference.infostock_theme",
                    source="fixture",
                    payload={"file": "theme-1.json", "content": theme_content},
                )
                index_items = [{"themeId": "1", "themeName": "fixture-theme"}]
                store.append_event(
                    run_id=run_id,
                    event_type="reference.infostock_theme",
                    source="fixture",
                    payload={
                        "file": "theme-index.json",
                        "content": {
                            "sourceType": "theme_index",
                            "items": index_items,
                            "contentHash": payload_hash(index_items),
                        },
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="reference.stock_master",
                    source="fixture",
                    payload={
                        "market": "KOSPI",
                        "response": {"list": [{"code": "005930"}]},
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="reference.stock_master",
                    source="fixture",
                    payload={
                        "market": "KOSDAQ",
                        "response": {"list": [{"code": "000660"}]},
                    },
                )
                store.append_event(
                    run_id=run_id,
                    event_type="candidate.condition_list",
                    source="fixture",
                    payload={"data": [["1", "조건"]]},
                )
                store.append_event(
                    run_id=run_id,
                    event_type="candidate.condition",
                    source="fixture",
                    payload={
                        "item": "005930",
                        "type": "02",
                        "values": {"841": "7", "843": "I", "9001": "005930"},
                    },
                    stock_code="005930",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="candidate.condition",
                    source="fixture",
                    payload={
                        "conditionId": "12",
                        "action": "INITIAL",
                        "rank": 1,
                        "raw": {"stk_cd": "000660"},
                    },
                    stock_code="000660",
                )
                store.append_event(
                    run_id=run_id,
                    event_type="kiwoom.websocket.raw",
                    source="fixture",
                    payload={"trnm": "REAL"},
                )
                store.append_event(
                    run_id=run_id,
                    event_type="source.status",
                    source="fixture",
                    payload={
                        "status": "CONDITIONS_SELECTED",
                        "selected": [
                            {"seq": value}
                            for value in ("7", "12", "19", "25", "35", "54", "56", "71")
                        ],
                    },
                )
                for status_name in (
                    "INFOSTOCK_FROZEN",
                    "STOCK_MASTER_READY",
                    "PREOPEN_READY",
                    "WEBSOCKET_CONNECTED",
                    "REALTIME_CAPTURE_ENDED",
                    "MINUTE_BACKFILL_STARTED",
                    "MINUTE_BACKFILL_FINISHED",
                ):
                    store.append_event(
                        run_id=run_id,
                        event_type="source.status",
                        source="fixture",
                        payload={"status": status_name},
                    )
                store.append_event(
                    run_id=run_id,
                    event_type="subscription.changed",
                    source="fixture",
                    payload={
                        "kind": "stock_trade",
                        "targets": [f"{value:06d}" for value in range(180)],
                        "targetCount": 180,
                        "maxSubscriptions": 180,
                        "request": {
                            "data": [
                                {"item": ["001", "101"], "type": ["0J", "0U"]},
                                {
                                    "item": [f"{value:06d}" for value in range(180)],
                                    "type": ["0B"],
                                },
                            ]
                        },
                    },
                )

                opened = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)
                api_ids = ("ka10019", "ka10023", "ka10027", "ka10032")
                for tick in range(0, 781):
                    timestamp = (opened + timedelta(seconds=30 * tick)).isoformat()
                    store.append_event(
                        run_id=run_id,
                        event_type="kiwoom.rest.raw",
                        source="fixture",
                        payload={"apiId": api_ids[tick % len(api_ids)]},
                        received_at=timestamp,
                    )
                for item in ("001", "101"):
                    for event_type in ("market.index", "market.breadth"):
                        for minute in range(0, 391):
                            store.append_event(
                                run_id=run_id,
                                event_type=event_type,
                                source="fixture",
                                payload={"item": item},
                                received_at=(opened + timedelta(minutes=minute)).isoformat(),
                            )
                for minute in range(0, 391):
                    trade_received = opened + timedelta(minutes=minute)
                    store.append_event(
                        run_id=run_id,
                        event_type="market.trade",
                        source="fixture",
                        payload={
                            "item": "005930",
                            "type": "0B",
                            "values": {
                                "10": "70000",
                                "11": "800",
                                "12": "1.2",
                                "27": "70100",
                                "28": "70000",
                                "13": "1000",
                                "14": "70000000",
                                "15": "10",
                                "16": "69000",
                                "17": "70500",
                                "18": "68800",
                                "20": trade_received.astimezone(
                                    timezone(timedelta(hours=9))
                                ).strftime("%H%M%S"),
                                "228": "110.0",
                                "311": "4000000",
                                "1313": "700000",
                            },
                        },
                        occurred_at=trade_received.isoformat(),
                        received_at=trade_received.isoformat(),
                        stock_code="005930",
                    )
                for rank, api_id in enumerate(
                    ("ka10019", "ka10023", "ka10027", "ka10032"), start=1
                ):
                    store.append_event(
                        run_id=run_id,
                        event_type="candidate.rest",
                        source="fixture",
                        payload={
                            "apiId": api_id,
                            "rank": rank,
                            "raw": {"stk_cd": "005930"},
                        },
                        received_at=opened.isoformat(),
                        stock_code="005930",
                    )
                for code in ("005930", "000660"):
                    store.append_event(
                        run_id=run_id,
                        event_type="backfill.minute.completed",
                        source="fixture",
                        payload={"barCount": 2},
                        stock_code=code,
                    )
                    store.append_minute_bars(
                        run_id=run_id,
                        stock_code=code,
                        source_received_at=opened.isoformat(),
                        rows=[
                            {
                                "cntr_tm": "20260814090000",
                                "open_pric": "1",
                                "high_pric": "1",
                                "low_pric": "1",
                                "cur_prc": "1",
                                "trde_qty": "1",
                            },
                            {
                                "cntr_tm": "20260814153000",
                                "open_pric": "2",
                                "high_pric": "2",
                                "low_pric": "2",
                                "cur_prc": "2",
                                "trde_qty": "1",
                            },
                        ],
                    )
                store.finish_run(run_id, status="COMPLETED")

            log_path = output / "collector.log"
            log_path.write_text("capture finished without sensitive values\n", encoding="utf-8")
            result = audit_capture(
                output / "market-replay.sqlite3", run_id, [log_path]
            )
            self.assertTrue(result["passed"], result)

            log_path.write_text(
                "authorization: Bearer_not_a_real_fixture_secret_123456\n",
                encoding="utf-8",
            )
            leaked = audit_capture(
                output / "market-replay.sqlite3", run_id, [log_path]
            )
            self.assertFalse(leaked["passed"])
            log_check = next(
                check
                for check in leaked["checks"]
                if check["name"] == "no_credentials_in_logs"
            )
            self.assertEqual(log_check["details"]["matchingLineCount"], 1)

    def test_source_clock_preserves_korean_market_time(self):
        result = source_clock_to_utc(
            date(2026, 8, 14), "090001123", "fallback"
        )
        self.assertEqual(result, "2026-08-14T00:00:01.123000+00:00")

    def test_websocket_replay_delivers_ordered_envelopes_and_completion(self):
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                with ReplayStore(output, batch_size=1) as store:
                    run_id = store.start_run(
                        trade_date=date(2026, 8, 14), mode="fixture", settings={}
                    )
                    for second in (1, 2):
                        store.append_event(
                            run_id=run_id,
                            event_type="market.trade",
                            source="fixture",
                            payload={"second": second},
                            received_at=f"2026-08-14T00:00:0{second}+00:00",
                        )
                    store.finish_run(run_id, status="COMPLETED")
                database = output / "market-replay.sqlite3"

                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                server = asyncio.create_task(
                    serve_event_factory(
                        lambda: iter_events(database, run_id=run_id),
                        speed=0,
                        host="127.0.0.1",
                        port=port,
                        loop_forever=False,
                    )
                )
                await asyncio.sleep(0.05)
                try:
                    async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
                        messages = [json.loads(await client.recv()) for _ in range(3)]
                    self.assertEqual(
                        [messages[0]["sequence"], messages[1]["sequence"]], [1, 2]
                    )
                    self.assertEqual(messages[2]["eventType"], "replay.completed")
                finally:
                    server.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await server

                proof = await prove_websocket_replay(
                    lambda: iter_events(database, run_id=run_id),
                    speed=0,
                    max_events=10,
                )
                self.assertTrue(proof["passed"], proof)
                self.assertEqual(proof["eventCount"], 2)
                self.assertEqual(
                    proof["expectedEnvelopeSha256"],
                    proof["observedEnvelopeSha256"],
                )

        asyncio.run(scenario())


class CandidateTests(unittest.TestCase):
    def test_candidates_expand_to_related_members_without_duplicates(self):
        stock_to_themes = {
            "000001": {"theme-1"},
            "000002": {"theme-1"},
            "000003": {"theme-1"},
        }
        theme_members = {"theme-1": ["000001", "000002", "000003"]}
        manager = CandidateManager(
            stock_to_themes, theme_members, max_subscriptions=3
        )
        manager.observe("A000001", "rest:ka10032")
        manager.observe("000002", "condition:1")

        self.assertEqual(
            set(manager.select_targets()), {"000001", "000002", "000003"}
        )
        reasons = manager.explain_targets(("000001", "000003"))
        self.assertEqual(reasons["000001"]["kind"], "direct_candidate")
        self.assertEqual(reasons["000003"]["kind"], "theme_expansion")
        self.assertEqual(reasons["000003"]["themeIds"], ["theme-1"])

    def test_candidate_selection_preserves_order_and_coalesces_bursts(self):
        manager = CandidateManager(
            {}, {}, max_subscriptions=3, minimum_update_interval_seconds=60
        )
        for code in ("000001", "000002", "000003"):
            manager.observe(code, "rest:ka10032")
        initial = manager.select_targets()
        manager.applied(initial)
        manager.observe("000002", "condition:1")
        manager.observe("000001", "condition:1")

        self.assertEqual(manager.select_targets(), initial)
        self.assertFalse(manager.dirty)

    def test_stock_rows_find_nested_api_payloads(self):
        payload = {
            "return_code": 0,
            "trde_prica_upper": [
                {"stk_cd": "005930", "stk_nm": "삼성전자"},
                {"stk_cd": "000660", "stk_nm": "SK하이닉스"},
            ],
        }
        rows = list(iter_stock_rows(payload))
        self.assertEqual(
            [stock_code_from_row(row) for row in rows], ["005930", "000660"]
        )

    def test_condition_list_accepts_official_list_and_map_shapes(self):
        result = parse_condition_list(
            {"data": [["0", "조건1"], {"seq": "1", "name": "조건2"}]}
        )
        self.assertEqual(result, [{"seq": "0", "name": "조건1"}, {"seq": "1", "name": "조건2"}])


if __name__ == "__main__":
    unittest.main()
