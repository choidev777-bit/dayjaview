param(
    [Parameter(Mandatory = $false)]
    [string]$TradeDate = "2026-08-14",

    [ValidateSet("real", "demo")]
    [string]$Mode = "real"
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $workspace "logs"
$logPath = Join-Path $logDirectory "market-capture-$TradeDate.log"
$errorLogPath = Join-Path $logDirectory "market-capture-$TradeDate.error.log"
$pidPath = Join-Path $logDirectory "market-capture-$TradeDate.pid"
$scriptPath = Join-Path $PSScriptRoot "collect_market_replay.py"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($existingPid -match '^\d+$' -and (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
        throw "Market capture is already running with PID $existingPid"
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$arguments = @(
    "-u",
    $scriptPath,
    "capture",
    "--mode", $Mode,
    "--trade-date", $TradeDate,
    "--start-at", "09:00:00",
    "--end-at", "15:40:00",
    "--poll-seconds", "30",
    "--max-subscriptions", "180",
    "--condition-id", "7",
    "--condition-id", "12",
    "--condition-id", "19",
    "--condition-id", "25",
    "--condition-id", "35",
    "--condition-id", "54",
    "--condition-id", "56",
    "--condition-id", "71",
    "--backfill-minute-bars"
)

$process = Start-Process -FilePath "python" `
    -ArgumentList $arguments `
    -WorkingDirectory $workspace `
    -RedirectStandardOutput $logPath `
    -RedirectStandardError $errorLogPath `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $process.Id
Write-Output "Market capture started. PID=$($process.Id) Log=$logPath ErrorLog=$errorLogPath"
