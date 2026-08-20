[CmdletBinding()]
param(
    [string]$ReleaseTag = "v1.5.1",
    [string]$OutputDirectory = "release-packages"
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bundleName = "longyun-agent-$ReleaseTag-$timestamp"
$outputPath = Join-Path $projectRoot $OutputDirectory
$archivePath = Join-Path $outputPath "$bundleName.tar.gz"
$hashPath = "$archivePath.sha256"
$tempRoot = [System.IO.Path]::GetTempPath()
$stageRoot = Join-Path $tempRoot ("longyun-agent-package-" + [guid]::NewGuid().ToString("N"))
$stagePath = Join-Path $stageRoot $bundleName

function Remove-StageDirectory {
    if (Test-Path -LiteralPath $stageRoot) {
        $resolvedStage = (Resolve-Path -LiteralPath $stageRoot).Path
        if (-not $resolvedStage.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a staging path outside the temp directory: $resolvedStage"
        }
        try {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
        catch {
            # A deeply nested third-party dependency can exceed the legacy Windows
            # path limit. The extended-length path keeps temporary staging cleanup
            # reliable without touching any project or user data.
            $extendedPath = "\\\\?\\$resolvedStage"
            if ([System.IO.Directory]::Exists($extendedPath)) {
                [System.IO.Directory]::Delete($extendedPath, $true)
            }
        }
    }
}

try {
    New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
    New-Item -ItemType Directory -Path $stagePath -Force | Out-Null

    # Bare directory names make robocopy exclude matching dependency, cache and
    # local-runtime folders at any nesting level. This is more reliable than
    # absolute /XD paths across the Windows robocopy builds used by developers.
    $excludedDirectories = @(
        ".git",
        "node_modules",
        "dist",
        "__pycache__",
        "tmp",
        "raw",
        "research",
        "backups",
        "certs",
        $OutputDirectory
    )

    $robocopyArgs = @(
        $projectRoot,
        $stagePath,
        "/E",
        "/COPY:DAT",
        "/DCOPY:DAT",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/XD"
    ) + $excludedDirectories
    & robocopy.exe @robocopyArgs | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed while preparing the release package (exit code $LASTEXITCODE)."
    }

    # Never move local credentials or TLS material into a release archive.
    $explicitSecretFiles = @(
        ".env",
        "deploy\\.env.production",
        "deploy\\.env.lan",
        "backend\\.env",
        "backend\\.env.production",
        "frontend\\.env",
        "frontend\\.env.production",
        # This ignored file is generated for local Docker development and can
        # contain developer-only account passwords. Production uses the
        # placeholder-based realm under deploy/keycloak instead.
        "keycloak\\rice-research-realm.json"
    )
    foreach ($relativePath in $explicitSecretFiles) {
        $candidate = Join-Path $stagePath $relativePath
        if (Test-Path -LiteralPath $candidate) {
            Remove-Item -LiteralPath $candidate -Force
        }
    }

    $allowedEnvironmentExamples = @(
        ".env.example",
        ".env.lan.example",
        ".env.production.example",
        ".env.sample",
        ".env.template"
    )
    Get-ChildItem -LiteralPath $stagePath -Force -File -Recurse |
        Where-Object {
            ($_.Name -eq ".env") -or
            ($_.Name -like ".env.*" -and $allowedEnvironmentExamples -notcontains $_.Name) -or
            ($_.Extension.ToLowerInvariant() -in @(".key", ".pem", ".pfx", ".p12"))
        } |
        Remove-Item -Force

    $realmFile = Join-Path $stagePath "deploy\\keycloak\\rice-research-realm.json"
    if (-not (Test-Path -LiteralPath $realmFile)) {
        throw "The Keycloak realm import is missing from the package. Demo accounts would not be created."
    }

    @(
        "Product: Longyun Agent Breeding Intelligence",
        "Release: $ReleaseTag",
        "Build time: $(Get-Date -Format s)",
        "Deployment guide: deploy/LAN-DEPLOYMENT.md",
        "This package intentionally excludes .env files, TLS certificates, local databases, raw uploads, and research attachments."
    ) | Set-Content -LiteralPath (Join-Path $stagePath "RELEASE-MANIFEST.txt") -Encoding utf8

    if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }
    if (Test-Path -LiteralPath $hashPath) { Remove-Item -LiteralPath $hashPath -Force }
    & tar.exe -czf $archivePath -C $stageRoot $bundleName
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed while creating the release archive (exit code $LASTEXITCODE)."
    }

    $entries = & tar.exe -tzf $archivePath
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed while verifying the release archive (exit code $LASTEXITCODE)."
    }
    $forbiddenEntries = $entries | Where-Object {
        $_ -match '(^|/)\.env$' -or
        $_ -match '(^|/)deploy/\.env\.(production|lan)$' -or
        $_ -match '(^|/)deploy/certs/' -or
        $_ -match '(^|/)data/(raw|research)/' -or
        $_ -match '\.(key|pem|pfx|p12)$'
    }
    if ($forbiddenEntries) {
        throw "The release archive contains forbidden local material: $($forbiddenEntries -join ', ')"
    }

    $sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
    "$sha256  $(Split-Path -Leaf $archivePath)" | Set-Content -LiteralPath $hashPath -Encoding ascii

    Write-Host "Release package created and verified:"
    Write-Host "  $archivePath"
    Write-Host "  SHA256: $sha256"
}
finally {
    Remove-StageDirectory
}
