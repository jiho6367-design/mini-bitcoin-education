param([int]$Port = 5003, [switch]$SkipInstall, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$teamAppRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $teamAppRoot
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "포트 $Port 사용 중 · -Port 옵션으로 다른 포트를 선택하세요" }
$teamPython = Join-Path $teamAppRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $teamPython)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 가상환경 생성 실패' }
}
if (-not $SkipInstall) {
    & $teamPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python 의존성 설치 실패' }
}
Push-Location -LiteralPath (Join-Path $teamAppRoot 'frontend')
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw '화면 의존성 설치 실패' }
    }
    if (-not $SkipBuild) {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw '화면 빌드 실패' }
    }
} finally { Pop-Location }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw '통합 화면 빌드가 없습니다' }
if (-not $env:TEAM_TEACHER_SECRET) {
    $teamPrivateDir = Join-Path $teamAppRoot 'instance'
    New-Item -ItemType Directory -Path $teamPrivateDir -Force | Out-Null
    $teamSecretFile = Join-Path $teamPrivateDir 'teacher-key.txt'
    if (-not (Test-Path -LiteralPath $teamSecretFile)) {
        $teamRandom = New-Object byte[] 24
        $teamGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $teamGenerator.GetBytes($teamRandom) } finally { $teamGenerator.Dispose() }
        $teamNewKey = [Convert]::ToBase64String($teamRandom).TrimEnd('=').Replace('+','-').Replace('/','_')
        [IO.File]::WriteAllText($teamSecretFile, $teamNewKey)
    }
    $env:TEAM_TEACHER_SECRET = [IO.File]::ReadAllText($teamSecretFile).Trim()
    Write-Host "교사용 비밀코드 파일: $teamSecretFile"
}
$env:PORT = [string]$Port
Write-Host "통합 실습: http://127.0.0.1:$Port/"
Write-Host "기초 실습: http://127.0.0.1:$Port/basic"
Write-Host '종료: Ctrl+C'
& $teamPython -B app.py
