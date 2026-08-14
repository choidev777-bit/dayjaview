param(
    [string]$TradeDate = "2026-08-14",
    [ValidateSet("real", "demo")]
    [string]$Mode = "real"
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $workspace "logs"
$logPath = Join-Path $logDirectory "market-snapshot-supplement-$TradeDate.log"
$errorLogPath = Join-Path $logDirectory "market-snapshot-supplement-$TradeDate.error.log"
$pidPath = Join-Path $logDirectory "market-snapshot-supplement-$TradeDate.pid"
$scriptPath = Join-Path $PSScriptRoot "collect_market_snapshot_supplement.py"
$mainDatabase = Join-Path $workspace "data\market-replay\$TradeDate\market-replay.sqlite3"
$outputDirectory = Join-Path $workspace "data\market-replay-supplemental\$TradeDate"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($existingPid -match '^\d+$' -and (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
        throw "Market snapshot supplement is already running with PID $existingPid"
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$arguments = @(
    "-u",
    $scriptPath,
    "--main-database", $mainDatabase,
    "--output-dir", $outputDirectory,
    "--mode", $Mode,
    "--start-at", "09:00:00",
    "--end-at", "15:40:00",
    "--poll-seconds", "30",
    "--batch-size", "100",
    "--candidate-ttl-seconds", "1800"
)

$process = Start-Process -FilePath "python" `
    -ArgumentList $arguments `
    -WorkingDirectory $workspace `
    -RedirectStandardOutput $logPath `
    -RedirectStandardError $errorLogPath `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $process.Id
Write-Output "Snapshot supplement started. PID=$($process.Id) Log=$logPath ErrorLog=$errorLogPath"
