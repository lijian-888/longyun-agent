[CmdletBinding()]
param(
    [switch]$KeepEnvironment,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $repoRoot "docker-compose.sandbox-acceptance.yml"
$projectName = "longyun-sandbox-acceptance"
$compose = @("compose", "-p", $projectName, "-f", $composeFile)

function Invoke-DockerCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker @compose @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed: $($Arguments -join ' ')"
    }
}

try {
    # This project name is reserved for disposable acceptance infrastructure.
    Invoke-DockerCompose down --volumes --remove-orphans
    if ($SkipBuild) {
        Invoke-DockerCompose up -d
    } else {
        Invoke-DockerCompose up -d --build
    }

    Invoke-DockerCompose exec -T runner python -c `
        "from app.tenancy import tenant_database_manager; from app.main import initialize_tenant_database; tenant_database_manager.ensure_control_schema(); [initialize_tenant_database(item.institution_id) for item in tenant_database_manager.active_bindings()]; print('tenant-databases-initialized')"

    Invoke-DockerCompose exec -T runner python /app/tests/run_sandbox_acceptance.py
    Invoke-DockerCompose exec -T runner python /app/tests/integration_multitenant_sandbox.py
    Invoke-DockerCompose exec -T runner python /app/tests/integration_minio_stream.py
    Invoke-DockerCompose exec -T `
        -e DEFAULT_INSTITUTION_ID=migration-test `
        -e DATABASE_URL=postgresql+psycopg://rice_app:AppOnlyA_2026@migration-db:5432/longyun_migration_test `
        -e MIGRATION_DATABASE_URL=postgresql+psycopg://rice:AcceptanceMigration_2026@migration-db:5432/longyun_migration_test `
        runner python /app/tests/integration_data_spine_migration.py

    Write-Host "SANDBOX_ACCEPTANCE_OK" -ForegroundColor Green
}
finally {
    if (-not $KeepEnvironment) {
        Invoke-DockerCompose down --volumes --remove-orphans
    } else {
        Write-Host "Acceptance environment retained under Compose project: $projectName"
    }
}
