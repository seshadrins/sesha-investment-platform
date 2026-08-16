$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Review the local database password when convenient."
}

docker compose up --build -d
docker compose ps

Write-Host ""
Write-Host "Portfolio UI: http://localhost:8501"
Write-Host "API docs:     http://localhost:8000/docs"
