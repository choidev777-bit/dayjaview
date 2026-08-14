param(
    [string]$TradeDate = "2026-08-14",
    [ValidateSet("real", "demo")]
    [string]$Mode = "real"
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $workspace "logs"
$logPath = Join-Path $logDirectory "market-gap-recovery-$TradeDate.log"
$errorLogPath = Join-Path $logDirectory "market-gap-recovery-$TradeDate.error.log"
$pidPath = Join-Path $logDirectory "market-gap-recovery-$TradeDate.pid"
$scriptPath = Join-Path $PSScriptRoot "collect_market_gap_recovery.py"
$mainDatabase = Join-Path $workspace "data\market-replay\$TradeDate\market-replay.sqlite3"
$outputDirectory = Join-Path $workspace "data\market-replay-gap-recovery\$TradeDate"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($existingPid -match '^\d+$' -and (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
        throw "Market gap recovery is already running with PID $existingPid"
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$arguments = @(
    "-u",
    $scriptPath,
    "--main-database", $mainDatabase,
    "--output-dir", $outputDirectory,
    "--mode", $Mode,
    "--gap-start", "09:00",
    "--gap-end", "10:09",
    "--request-delay-seconds", "0.05"
)

$process = Start-Process -FilePath "python" `
    -ArgumentList $arguments `
    -WorkingDirectory $workspace `
    -RedirectStandardOutput $logPath `
    -RedirectStandardError $errorLogPath `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $process.Id
Write-Output "Market gap recovery started. PID=$($process.Id) Log=$logPath ErrorLog=$errorLogPath"
