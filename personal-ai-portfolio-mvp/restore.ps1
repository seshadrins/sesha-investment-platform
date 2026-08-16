param(
    [Parameter(Mandatory=$true)]
    [string]$BackupFile
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}

$envLines = Get-Content ".env"
$db = (($envLines | Where-Object { $_ -match '^POSTGRES_DB=' }) -split '=',2)[1]
$user = (($envLines | Where-Object { $_ -match '^POSTGRES_USER=' }) -split '=',2)[1]

Get-Content $BackupFile | docker compose exec -T db psql -U $user -d $db
Write-Host "Restore completed from: $BackupFile"
