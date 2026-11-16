param([int]$Port = 5003, [switch]$SkipInstall, [switch]$SkipBuild,
    [switch]$Native, [switch]$InstallGpu, [switch]$BuildNative,
    [string]$Compiler = '', [string]$OpenSSLRoot = '')
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
if ($InstallGpu) {
    & $teamPython -m pip install -r native_hashcash/requirements-gpu.txt
    if ($LASTEXITCODE -ne 0) { throw 'GPU 선택 의존성 설치 실패 · 기본 웹 연산은 계속 사용할 수 있습니다' }
}
if ($BuildNative) {
    if (-not $Compiler) {
        $teamGccCommand = Get-Command gcc.exe -ErrorAction SilentlyContinue
        if ($teamGccCommand) { $Compiler = $teamGccCommand.Source }
        else {
            $teamKnownGcc = Join-Path $env:LOCALAPPDATA 'Programs/msys64/ucrt64/bin/gcc.exe'
            if (Test-Path -LiteralPath $teamKnownGcc) { $Compiler = $teamKnownGcc }
        }
    }
    if (-not $OpenSSLRoot -and $Compiler) { $OpenSSLRoot = Split-Path -Parent (Split-Path -Parent $Compiler) }
    $teamNativeArgs = @('-m', 'native_hashcash.hashcash_lab.native_scheduler_experiment', '--build', '--self-test')
    if ($Compiler) { $teamNativeArgs += @('--compiler', $Compiler) }
    if ($OpenSSLRoot) { $teamNativeArgs += @('--openssl-root', $OpenSSLRoot) }
    & $teamPython @teamNativeArgs
    if ($LASTEXITCODE -ne 0) { throw 'C 선택 엔진 빌드 실패 · GCC와 OpenSSL 개발 환경을 확인하세요' }
}
if ($Native -or $InstallGpu -or $BuildNative) { $env:TEAM_NATIVE_HASHCASH = '1' }
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
Write-Host "기초 실습: http://127.0.0.1:$Port/"
Write-Host "넌스 찾기: http://127.0.0.1:$Port/hashcash"
if ($env:TEAM_NATIVE_HASHCASH -eq '1') { Write-Host 'Python/C 고급 모드: 이 PC에서만 활성화' }
Write-Host '종료: Ctrl+C'
& $teamPython -B app.py
