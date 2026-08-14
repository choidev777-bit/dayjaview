param(
    [int]$InitialCooldownSeconds = 600,
    [int]$CooldownSeconds = 600,
    [int]$MaxAttempts = 12
)

# 로컬 일회성 복구 도구다. production에서는 worker를 재우지 않고
# scheduler가 저장된 next_retry_at 이후 새 작업을 실행한다.

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $PSScriptRoot
$statusDirectory = Join-Path $repoRoot 'data\infostock'
$statusPath = Join-Path $statusDirectory 'sync-status.json'
$logDirectory = Join-Path $repoRoot 'logs'
$logPath = Join-Path $logDirectory 'infostock-sync.log'

New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location -LiteralPath $repoRoot

function Write-SyncStatus {
    param(
        [string]$State,
        [int]$Attempt,
        [string]$Message
    )

    $status = [ordered]@{
        state = $State
        attempt = $Attempt
        message = $Message
        updatedAt = [DateTimeOffset]::UtcNow.ToString('o')
        processId = $PID
    }
    $status | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}

Write-SyncStatus -State 'WAITING' -Attempt 0 -Message "Initial cooldown ${InitialCooldownSeconds}s"
Start-Sleep -Seconds $InitialCooldownSeconds

for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    Write-SyncStatus -State 'RUNNING' -Attempt $attempt -Message 'Resuming incomplete theme sync'
    "[$([DateTimeOffset]::Now.ToString('o'))] attempt=$attempt" | Add-Content -LiteralPath $logPath -Encoding UTF8

    & python scripts\collect_infostock.py --resume --workers 1 --request-delay-ms 2000 --retries 2 *>> $logPath
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0) {
        Write-SyncStatus -State 'COMPLETE' -Attempt $attempt -Message 'All themes collected and validated'
        exit 0
    }

    if ($attempt -lt $MaxAttempts) {
        Write-SyncStatus -State 'WAITING' -Attempt $attempt -Message "Retry after ${CooldownSeconds}s"
        Start-Sleep -Seconds $CooldownSeconds
    }
}

Write-SyncStatus -State 'FAILED' -Attempt $MaxAttempts -Message 'Retry budget exhausted; inspect logs\infostock-sync.log'
exit 1
