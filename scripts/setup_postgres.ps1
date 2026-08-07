<#
.SYNOPSIS
  Creates the SearchFit PostgreSQL role + database on a local Postgres install.
.EXAMPLE
  .\scripts\setup_postgres.ps1 -PostgresPassword "your-password"
#>
param(
  [Parameter(Mandatory = $true)]
  [string]$PostgresPassword,

  [string]$HostName = "127.0.0.1",
  [int]$Port = 5432,
  [string]$AdminUser = "postgres",
  [string]$DbName = "searchfit",
  [string]$AppUser = "searchfit",
  [string]$AppPassword = "searchfit"
)

$ErrorActionPreference = "Stop"
$psql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" |
  Sort-Object FullName -Descending |
  Select-Object -First 1 -ExpandProperty FullName

if (-not $psql) { throw "psql.exe not found under C:\Program Files\PostgreSQL" }

$env:PGPASSWORD = $PostgresPassword

function Invoke-Psql([string]$sql, [string]$database = "postgres") {
  & $psql -U $AdminUser -h $HostName -p $Port -d $database -v ON_ERROR_STOP=1 -c $sql
}

Write-Host "Creating role/database '$DbName'..."

Invoke-Psql @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$AppUser') THEN
    CREATE ROLE $AppUser LOGIN PASSWORD '$AppPassword';
  ELSE
    ALTER ROLE $AppUser WITH LOGIN PASSWORD '$AppPassword';
  END IF;
END
`$`$;
"@

$exists = & $psql -U $AdminUser -h $HostName -p $Port -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DbName'"
if ($exists -ne "1") {
  Invoke-Psql "CREATE DATABASE $DbName OWNER $AppUser;"
} else {
  Write-Host "Database already exists."
}

Invoke-Psql "GRANT ALL PRIVILEGES ON DATABASE $DbName TO $AppUser;"
# Try pgvector if available; ignore failure
try {
  Invoke-Psql "CREATE EXTENSION IF NOT EXISTS vector;" $DbName
  Write-Host "pgvector extension enabled."
} catch {
  Write-Host "pgvector not available — embeddings will store as JSON/JSONB. Continuing."
}

Write-Host ""
Write-Host "Done. Use this DATABASE_URL in .env:"
Write-Host "DATABASE_URL=postgresql+asyncpg://${AppUser}:${AppPassword}@${HostName}:${Port}/${DbName}"
