[CmdletBinding()]
param(
    [string]$ReleaseTag = "v1.5.1",
    [string]$OutputDirectory = "release-packages",
    [string]$ApiSourceImage = "rice-data-governance-platform-demo-api:latest",
    [string]$WebSourceImage = "rice-platform-web:preflight",
    [string]$MineruSourceImage = "rice-data-governance-platform-demo-mineru:latest"
)

$ErrorActionPreference = "Stop"

function Assert-DockerImage([string]$Image) {
    & docker image inspect $Image *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Required local image '$Image' was not found. Build and test it locally before creating an offline bundle."
    }
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outputPath = Join-Path $projectRoot $OutputDirectory
$bundleName = "longyun-agent-images-$ReleaseTag-$timestamp.tar"
$archivePath = Join-Path $outputPath $bundleName
$hashPath = "$archivePath.sha256"
$manifestPath = Join-Path $outputPath ("longyun-agent-images-$ReleaseTag-$timestamp.manifest.txt")

$applicationImages = @{
    $ApiSourceImage = "longyun-agent-api:$ReleaseTag"
    $WebSourceImage = "longyun-agent-web:$ReleaseTag"
    $MineruSourceImage = "longyun-agent-mineru:$ReleaseTag"
}
$externalImages = @(
    "pgvector/pgvector:pg16",
    "quay.io/keycloak/keycloak:26.2",
    "nginx:1.27-alpine"
)

New-Item -ItemType Directory -Path $outputPath -Force | Out-Null

foreach ($sourceImage in $applicationImages.Keys) {
    Assert-DockerImage $sourceImage
}
foreach ($externalImage in $externalImages) {
    Assert-DockerImage $externalImage
}

foreach ($sourceImage in $applicationImages.Keys) {
    $targetImage = $applicationImages[$sourceImage]
    & docker image tag $sourceImage $targetImage
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to tag '$sourceImage' as '$targetImage'."
    }
}

$imagesToExport = @($applicationImages.Values) + $externalImages
& docker image save --output $archivePath @imagesToExport
if ($LASTEXITCODE -ne 0) {
    throw "docker image save failed."
}

$sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
"$sha256  $bundleName" | Set-Content -LiteralPath $hashPath -Encoding ascii

@(
    "Product: Longyun Agent Breeding Intelligence",
    "Release: $ReleaseTag",
    "Build time: $(Get-Date -Format s)",
    "Purpose: Offline Docker image bundle for intranet deployment.",
    "Server import: docker image load --input $bundleName",
    "Server start: bash deploy/compose.sh --env-file deploy/.env.production -f docker-compose.lan.yml up -d --no-build --pull never",
    "Images:"
) + $imagesToExport | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Host "Offline image bundle created and verified:"
Write-Host "  $archivePath"
Write-Host "  SHA256: $sha256"
Write-Host "  Manifest: $manifestPath"
