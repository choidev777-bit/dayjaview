# 시연 실행(PowerShell). 터널과 화면 서버를 한 번에 띄운다.
#
#   powershell -ExecutionPolicy Bypass -File C:\dayjaview\tools\kg_live\start.ps1
#
# 끝낼 때는 이 창에서 Ctrl+C. 터널도 같이 정리된다.
#
# PowerShell에서 `bash`는 WSL이 잡혀 윈도우 경로를 못 읽는다. 그래서 Git Bash를
# 경로로 직접 찾아 start.sh를 돌린다. ssh 터널·서버 기동은 전부 그쪽이 한다.

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repositoryRoot

$candidates = @(
    'C:\Program Files\Git\bin\bash.exe',
    'C:\Program Files\Git\usr\bin\bash.exe',
    'C:\Program Files (x86)\Git\bin\bash.exe',
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)
$gitBash = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $gitBash) {
    Write-Host 'Git Bash를 찾지 못했습니다. Git for Windows가 설치돼 있어야 합니다.' -ForegroundColor Yellow
    exit 1
}

Write-Host '시작합니다. 30~40초 뒤 http://127.0.0.1:8899 를 여세요.'
& $gitBash 'tools/kg_live/start.sh'
