# Start local CockroachDB and apply the stub schema. Usage: .\scripts\dev_up.ps1 [-Reset] [-Tune]
param([switch]$Reset, [switch]$Tune)
Set-Location (Join-Path $PSScriptRoot "..")
docker compose up -d --wait
if ($Tune) { uv run python scripts/tune_crdb.py --unsafe-nosync }
if ($Reset) { uv run python scripts/init_stub_schema.py --reset } else { uv run python scripts/init_stub_schema.py }
