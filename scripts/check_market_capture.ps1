param(
    [string]$TradeDate = "2026-08-14",
    [switch]$Detailed
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $workspace "logs\market-capture-$TradeDate.pid"
$databasePath = Join-Path $workspace "data\market-replay\$TradeDate\market-replay.sqlite3"
$ndjsonPath = Join-Path $workspace "data\market-replay\$TradeDate\events.ndjson"
$supplementPidPath = Join-Path $workspace "logs\market-snapshot-supplement-$TradeDate.pid"
$supplementDatabasePath = Join-Path $workspace "data\market-replay-supplemental\$TradeDate\market-replay.sqlite3"
$supplementNdjsonPath = Join-Path $workspace "data\market-replay-supplemental\$TradeDate\events.ndjson"
$recoveryPidPath = Join-Path $workspace "logs\market-gap-recovery-$TradeDate.pid"
$recoveryDatabasePath = Join-Path $workspace "data\market-replay-gap-recovery\$TradeDate\market-replay.sqlite3"

$processState = "NOT_STARTED"
$processId = $null
if (Test-Path -LiteralPath $pidPath) {
    $pidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($pidText -match '^\d+$') {
        $processId = [int]$pidText
        $running = Get-Process -Id $processId -ErrorAction SilentlyContinue
        $processState = if ($running) { "RUNNING" } else { "EXITED" }
    }
}

Write-Output "process=$processState pid=$processId database=$databasePath"

if (Test-Path -LiteralPath $databasePath) {
    $detailFlag = if ($Detailed) { "1" } else { "0" }
    @'
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

database = sys.argv[1]
ndjson = sys.argv[2]
connection = sqlite3.connect(database, timeout=5)
run = connection.execute(
    "SELECT run_id,status,started_at,finished_at,error FROM collection_runs ORDER BY started_at DESC LIMIT 1"
).fetchone()
detailed = sys.argv[3] == "1"
counts = connection.execute(
    "SELECT event_type,COUNT(*) FROM events WHERE run_id=? GROUP BY event_type ORDER BY event_type",
    (run[0],),
).fetchall() if run and detailed else []
bars = connection.execute(
    "SELECT COUNT(DISTINCT stock_code),COUNT(*) FROM minute_bars WHERE run_id=?",
    (run[0],),
).fetchone() if run and detailed else (None, None)
latest = connection.execute(
    "SELECT sequence,event_type,received_at,run_id FROM events ORDER BY sequence DESC LIMIT 1",
).fetchone() if run else None
if latest and latest[3] != run[0]:
    latest = connection.execute(
        "SELECT sequence,event_type,received_at,run_id FROM events "
        "INDEXED BY events_run_received_idx WHERE run_id=? "
        "ORDER BY received_at DESC,sequence DESC LIMIT 1",
        (run[0],),
    ).fetchone()
lag_seconds = None
if latest:
    received_at = datetime.fromisoformat(latest[2].replace("Z", "+00:00"))
    lag_seconds = round((datetime.now(timezone.utc) - received_at).total_seconds(), 3)
recent_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
recent_event_count = connection.execute(
    "SELECT COUNT(*) FROM events WHERE run_id=? AND received_at>=?",
    (run[0], recent_cutoff),
).fetchone()[0] if run else 0
source_error_count = connection.execute(
    "SELECT COUNT(*) FROM events WHERE run_id=? AND event_type='source.error'",
    (run[0],),
).fetchone()[0] if run else 0
backfill_counts = dict(connection.execute(
    "SELECT event_type,COUNT(*) FROM events INDEXED BY events_type_idx WHERE run_id=? "
    "AND event_type IN ('backfill.minute.completed','backfill.minute.failed',"
    "'backfill.repair.completed','backfill.repair.failed') GROUP BY event_type",
    (run[0],),
).fetchall()) if run else {}

def size_mb(path):
    candidate = Path(path)
    return round(candidate.stat().st_size / 1024 / 1024, 1) if candidate.exists() else 0

print(json.dumps({
    "run": {
        "runId": run[0], "status": run[1], "startedAt": run[2],
        "finishedAt": run[3], "error": run[4]
    } if run else None,
    "eventsByType": dict(counts),
    "sourceErrorCount": source_error_count,
    "latestEvent": {
        "sequence": latest[0], "eventType": latest[1],
        "receivedAt": latest[2], "lagSeconds": lag_seconds,
    } if latest else None,
    "throughput": {
        "last60Seconds": recent_event_count,
        "eventsPerSecond": round(recent_event_count / 60, 2),
    },
    "minuteBars": {
        "stockCount": bars[0], "rowCount": bars[1],
        "completedStocks": (
            backfill_counts.get("backfill.minute.completed", 0)
            + backfill_counts.get("backfill.repair.completed", 0)
        ),
        "failedAttempts": (
            backfill_counts.get("backfill.minute.failed", 0)
            + backfill_counts.get("backfill.repair.failed", 0)
        ),
        "detailedCountsIncluded": detailed,
    },
    "storageMb": {
        "database": size_mb(database),
        "wal": size_mb(database + "-wal"),
        "ndjson": size_mb(ndjson),
        "diskFreeGb": round(shutil.disk_usage(database).free / 1024 / 1024 / 1024, 1),
    },
}, ensure_ascii=False, indent=2))
'@ | python - $databasePath $ndjsonPath $detailFlag
}

$supplementProcessState = "NOT_STARTED"
$supplementProcessId = $null
if (Test-Path -LiteralPath $supplementPidPath) {
    $supplementPidText = (Get-Content -LiteralPath $supplementPidPath -Raw).Trim()
    if ($supplementPidText -match '^\d+$') {
        $supplementProcessId = [int]$supplementPidText
        $supplementRunning = Get-Process -Id $supplementProcessId -ErrorAction SilentlyContinue
        $supplementProcessState = if ($supplementRunning) { "RUNNING" } else { "EXITED" }
    }
}

Write-Output "supplementProcess=$supplementProcessState pid=$supplementProcessId database=$supplementDatabasePath"

if (Test-Path -LiteralPath $supplementDatabasePath) {
    @'
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

database, ndjson = sys.argv[1:3]
connection = sqlite3.connect(database, timeout=5)
run = connection.execute(
    "SELECT run_id,status,started_at,finished_at,error FROM collection_runs "
    "ORDER BY started_at DESC LIMIT 1"
).fetchone()
latest = connection.execute(
    "SELECT sequence,event_type,received_at FROM events ORDER BY sequence DESC LIMIT 1"
).fetchone() if run else None
coverage = connection.execute(
    "SELECT received_at,payload_json FROM events WHERE run_id=? "
    "AND event_type='supplemental.coverage' ORDER BY sequence DESC LIMIT 1",
    (run[0],),
).fetchone() if run else None
source_errors = connection.execute(
    "SELECT COUNT(*) FROM events WHERE run_id=? AND event_type='source.error'",
    (run[0],),
).fetchone()[0] if run else 0
recent_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
recent_snapshots = connection.execute(
    "SELECT COUNT(*) FROM events WHERE run_id=? AND event_type='market.snapshot' "
    "AND received_at>=?",
    (run[0], recent_cutoff),
).fetchone()[0] if run else 0
coverage_payload = json.loads(coverage[1]) if coverage else None
lag_seconds = None
if latest:
    lag_seconds = round(
        (datetime.now(timezone.utc) - datetime.fromisoformat(latest[2].replace("Z", "+00:00"))).total_seconds(),
        3,
    )

def size_mb(path):
    candidate = Path(path)
    return round(candidate.stat().st_size / 1024 / 1024, 1) if candidate.exists() else 0

print(json.dumps({
    "run": {
        "runId": run[0], "status": run[1], "startedAt": run[2],
        "finishedAt": run[3], "error": run[4],
    } if run else None,
    "latestEvent": {
        "sequence": latest[0], "eventType": latest[1],
        "receivedAt": latest[2], "lagSeconds": lag_seconds,
    } if latest else None,
    "latestCoverage": coverage_payload,
    "sourceErrorCount": source_errors,
    "snapshotsLast60Seconds": recent_snapshots,
    "storageMb": {
        "database": size_mb(database),
        "wal": size_mb(database + "-wal"),
        "ndjson": size_mb(ndjson),
        "diskFreeGb": round(shutil.disk_usage(database).free / 1024 / 1024 / 1024, 1),
    },
}, ensure_ascii=False, indent=2))
connection.close()
'@ | python - $supplementDatabasePath $supplementNdjsonPath
}

$recoveryProcessState = "NOT_STARTED"
$recoveryProcessId = $null
if (Test-Path -LiteralPath $recoveryPidPath) {
    $recoveryPidText = (Get-Content -LiteralPath $recoveryPidPath -Raw).Trim()
    if ($recoveryPidText -match '^\d+$') {
        $recoveryProcessId = [int]$recoveryPidText
        $recoveryRunning = Get-Process -Id $recoveryProcessId -ErrorAction SilentlyContinue
        $recoveryProcessState = if ($recoveryRunning) { "RUNNING" } else { "EXITED" }
    }
}

Write-Output "recoveryProcess=$recoveryProcessState pid=$recoveryProcessId database=$recoveryDatabasePath"

if (Test-Path -LiteralPath $recoveryDatabasePath) {
    @'
import json
import sqlite3
import sys
from datetime import datetime, timezone

database = sys.argv[1]
connection = sqlite3.connect(database, timeout=5)
run = connection.execute(
    "SELECT run_id,status,started_at,finished_at,error,settings_json "
    "FROM collection_runs ORDER BY started_at DESC LIMIT 1"
).fetchone()
latest = connection.execute(
    "SELECT sequence,event_type,received_at FROM events ORDER BY sequence DESC LIMIT 1"
).fetchone() if run else None
completed = connection.execute(
    "SELECT COUNT(DISTINCT stock_code) FROM events WHERE run_id=? "
    "AND event_type='gap_recovery.stock.completed'",
    (run[0],),
).fetchone()[0] if run else 0
states = connection.execute(
    "SELECT COUNT(*),COUNT(DISTINCT stock_code) FROM events WHERE run_id=? "
    "AND event_type='market.minute_state.recovered'",
    (run[0],),
).fetchone() if run else (0, 0)
errors = connection.execute(
    "SELECT COUNT(*) FROM events WHERE run_id=? AND event_type='source.error'",
    (run[0],),
).fetchone()[0] if run else 0
settings = json.loads(run[5]) if run else {}
lag = None
if latest:
    lag = round(
        (datetime.now(timezone.utc) - datetime.fromisoformat(latest[2].replace("Z", "+00:00"))).total_seconds(),
        3,
    )
print(json.dumps({
    "run": {
        "runId": run[0], "status": run[1], "startedAt": run[2],
        "finishedAt": run[3], "error": run[4],
    } if run else None,
    "targetCount": settings.get("targetCount"),
    "completedStockCount": completed,
    "recoveredStateCount": states[0],
    "recoveredStateStockCount": states[1],
    "sourceErrorCount": errors,
    "latestEvent": {
        "sequence": latest[0], "eventType": latest[1],
        "receivedAt": latest[2], "lagSeconds": lag,
    } if latest else None,
}, ensure_ascii=False, indent=2))
connection.close()
'@ | python - $recoveryDatabasePath
}
