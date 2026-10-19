param(
    [int]$Port = 43921
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    $python = 'python'
}

$env:ASTROGATE_PORT = $Port
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
