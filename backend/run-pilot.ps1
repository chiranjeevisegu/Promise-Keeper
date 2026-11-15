$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '../frontend/dist/index.html'))) {
    throw 'Build the frontend with npm.cmd run build before starting the pilot.'
}

$previousPassword = $env:PROMISE_KEEPER_PASSWORD
$previousRequired = $env:PROMISE_KEEPER_AUTH_REQUIRED
$previousOrigin = $env:PROMISE_KEEPER_ORIGIN
$previousFrontend = $env:PROMISE_KEEPER_SERVE_FRONTEND
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $env:PROMISE_KEEPER_PASSWORD) {
        $secret = Read-Host 'Choose a workspace password of at least 12 characters' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        try { $env:PROMISE_KEEPER_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    }
    $env:PROMISE_KEEPER_AUTH_REQUIRED = '1'
    $env:PROMISE_KEEPER_ORIGIN = 'http://127.0.0.1:8000'
    $env:PROMISE_KEEPER_SERVE_FRONTEND = '1'
    Write-Host 'Local pilot: http://127.0.0.1:8000'
    & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
    exit $LASTEXITCODE
} finally {
    $env:PROMISE_KEEPER_PASSWORD = $previousPassword
    $env:PROMISE_KEEPER_AUTH_REQUIRED = $previousRequired
    $env:PROMISE_KEEPER_ORIGIN = $previousOrigin
    $env:PROMISE_KEEPER_SERVE_FRONTEND = $previousFrontend
    Pop-Location
}
