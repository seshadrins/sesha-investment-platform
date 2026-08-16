$ErrorActionPreference = "Stop"

if (-not (Test-Path "backups")) {
    New-Item -ItemType Directory -Path "backups" | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$file = "backups/portfolio_$timestamp.sql"

$envLines = Get-Content ".env"
$db = (($envLines | Where-Object { $_ -match '^POSTGRES_DB=' }) -split '=',2)[1]
$user = (($envLines | Where-Object { $_ -match '^POSTGRES_USER=' }) -split '=',2)[1]

docker compose exec -T db pg_dump -U $user -d $db | Out-File -Encoding utf8 $file
Write-Host "Backup created: $file"
