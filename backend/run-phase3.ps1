param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $env:PROMISE_KEEPER_OLLAMA_MODEL) {
        $env:PROMISE_KEEPER_OLLAMA_MODEL = 'qwen2.5:1.5b'
    }
    if (-not $env:PROMISE_KEEPER_OLLAMA_URL) {
        $env:PROMISE_KEEPER_OLLAMA_URL = 'http://127.0.0.1:11434'
        $runtime = Join-Path $PSScriptRoot 'data/tools/ollama/ollama.exe'
        try {
            Invoke-RestMethod -Uri "$env:PROMISE_KEEPER_OLLAMA_URL/api/version" -TimeoutSec 2 | Out-Null
        } catch {
            if (-not (Test-Path -LiteralPath $runtime)) {
                throw 'Start your local Ollama server before running Phase 3.'
            }
            $env:OLLAMA_HOST = '127.0.0.1:11434'
            $env:OLLAMA_NO_CLOUD = '1'
            $env:OLLAMA_MODELS = Join-Path $PSScriptRoot 'data/tools/ollama/models'
            $server = Start-Process -FilePath $runtime -ArgumentList 'serve' -WindowStyle Hidden -PassThru
            $deadline = [DateTime]::UtcNow.AddSeconds(15)
            do {
                try {
                    Invoke-RestMethod -Uri "$env:PROMISE_KEEPER_OLLAMA_URL/api/version" -TimeoutSec 2 | Out-Null
                    break
                } catch {
                    if ($server.HasExited -or [DateTime]::UtcNow -ge $deadline) {
                        throw 'Ollama did not become ready on port 11434.'
                    }
                    Start-Sleep -Milliseconds 500
                }
            } while ($true)
        }
    }
    if ($CheckOnly) {
        & '.\.venv\Scripts\python.exe' check_commitment_setup.py
        exit $LASTEXITCODE
    }
    & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
