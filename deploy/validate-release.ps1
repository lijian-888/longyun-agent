[CmdletBinding()]
param(
    [string]$ReleaseTag = "v1.11.0-rc1",
    [string]$PythonCommand = "python",
    [switch]$SkipDockerBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$frontendRoot = Join-Path $projectRoot "frontend"
$backendRoot = Join-Path $projectRoot "backend"
$testImage = "longyun-agent-api:$ReleaseTag-validation"
$startedAt = Get-Date
$steps = [System.Collections.Generic.List[object]]::new()

function Invoke-ValidationStep {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    $stepStartedAt = Get-Date
    Write-Host "[validate] $Name"
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) {
            throw "$Name failed with exit code $LASTEXITCODE"
        }
        $steps.Add([ordered]@{
            name = $Name
            status = "passed"
            duration_seconds = [math]::Round(((Get-Date) - $stepStartedAt).TotalSeconds, 2)
        })
    }
    catch {
        $steps.Add([ordered]@{
            name = $Name
            status = "failed"
            duration_seconds = [math]::Round(((Get-Date) - $stepStartedAt).TotalSeconds, 2)
            detail = $_.Exception.Message
        })
        throw
    }
}

Push-Location $projectRoot
try {
    Invoke-ValidationStep "Git patch integrity" {
        & git diff --check
    }

    Invoke-ValidationStep "Python syntax compilation" {
        $oldPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = $backendRoot
            & $PythonCommand -m compileall -q (Join-Path $backendRoot "app") (Join-Path $backendRoot "tests")
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
        }
    }

    Invoke-ValidationStep "Model-egress and workflow regression tests" {
        $oldPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = $backendRoot
            & $PythonCommand -m unittest backend.tests.test_model_data_policy backend.tests.test_agent_workflow
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
        }
    }

    Invoke-ValidationStep "Frontend production build" {
        Push-Location $frontendRoot
        try {
            & npm run build
        }
        finally {
            Pop-Location
        }
    }

    Invoke-ValidationStep "Compose configuration" {
        & docker compose `
            --env-file (Join-Path $projectRoot "deploy/.env.production.example") `
            -f (Join-Path $projectRoot "docker-compose.lan.yml") `
            config -q
    }

    if (-not $SkipDockerBuild) {
        Invoke-ValidationStep "Backend production image build" {
            & docker build -t $testImage $backendRoot
        }
        Invoke-ValidationStep "Backend full tests in production image" {
            $testsPath = (Resolve-Path (Join-Path $backendRoot "tests")).Path
            & docker run --rm `
                --mount "type=bind,source=$testsPath,target=/app/tests,readonly" `
                -e PYTHONPATH=/app `
                $testImage `
                python -m unittest discover -s /app/tests -p "test_*.py" -v
        }
    }
}
finally {
    Pop-Location
    $reportDirectory = Join-Path $projectRoot "release-packages/validation"
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    $safeTag = $ReleaseTag -replace '[^A-Za-z0-9._-]', '-'
    $reportPath = Join-Path $reportDirectory ("validation-$safeTag-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
    $commit = (& git -C $projectRoot rev-parse HEAD 2>$null)
    $dirty = [bool](& git -C $projectRoot status --porcelain 2>$null)
    [ordered]@{
        release = $ReleaseTag
        commit = "$commit"
        working_tree_dirty = $dirty
        started_at = $startedAt.ToString("o")
        completed_at = (Get-Date).ToString("o")
        steps = $steps
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Host "Validation report: $reportPath"
}

Write-Host "Release validation passed: $ReleaseTag"
