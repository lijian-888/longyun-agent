param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\samples\genotype_qc_demo")
)

$ErrorActionPreference = "Stop"
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$samples = @(
    @{ Sample = "SEQ2025_001"; Material = "ME-A01" },
    @{ Sample = "SEQ2025_002"; Material = "ME-A02" },
    @{ Sample = "SEQ2025_003"; Material = "ME-A03" },
    @{ Sample = "SEQ2025_004"; Material = "ME-A04" },
    @{ Sample = "SEQ2025_005"; Material = "ME-A05" },
    @{ Sample = "SEQ2025_006"; Material = "ME-A06" },
    @{ Sample = "SEQ2025_007"; Material = "ME-A07" },
    @{ Sample = "SEQ2025_008"; Material = "ME-A08" },
    @{ Sample = "SEQ2025_009"; Material = "CK-01" },
    @{ Sample = "SEQ2025_010"; Material = "CK-02" }
)

$vcfPath = Join-Path $OutputDirectory "rice_genotype_qc_full_demo.vcf"
$mappingPath = Join-Path $OutputDirectory "rice_genotype_qc_full_demo_sample_mapping.csv"
$phenotypePath = Join-Path $OutputDirectory "rice_genotype_qc_full_demo_phenotype.csv"

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("##fileformat=VCFv4.2")
$lines.Add("##source=rice-data-governance-platform-demo")
1..12 | ForEach-Object { $lines.Add("##contig=<ID=chr$_>") }
$lines.Add("##FORMAT=<ID=GT,Number=1,Type=String,Description=`"Genotype`">")
$headerSamples = ($samples | ForEach-Object { $_.Sample }) -join "`t"
$lines.Add("#CHROM`tPOS`tID`tREF`tALT`tQUAL`tFILTER`tINFO`tFORMAT`t$headerSamples")

$genotypePatterns = @(
    @("0/0", "0/0", "0/1", "0/0", "0/1", "0/0", "1/1", "0/0", "0/0", "0/1"),
    @("0/0", "0/1", "0/0", "0/0", "0/1", "1/1", "0/0", "0/1", "0/0", "0/0"),
    @("0/1", "0/0", "0/0", "1/1", "0/0", "0/1", "0/0", "0/0", "0/1", "0/0")
)

for ($chromosomeIndex = 1; $chromosomeIndex -le 12; $chromosomeIndex++) {
    for ($markerWithinChromosome = 1; $markerWithinChromosome -le 5; $markerWithinChromosome++) {
        $marker = (($chromosomeIndex - 1) * 5) + $markerWithinChromosome
        $chromosome = "chr$chromosomeIndex"
        $position = 100000 + ($markerWithinChromosome * 1379)
        $ref = if ($marker % 2 -eq 0) { "A" } else { "C" }
        $alt = if ($marker % 3 -eq 0) { "G" } else { "T" }
        $pattern = $genotypePatterns[($marker - 1) % $genotypePatterns.Count]
        $shift = $marker % $pattern.Count
        $genotypes = for ($sampleIndex = 0; $sampleIndex -lt $pattern.Count; $sampleIndex++) {
            $pattern[($sampleIndex + $shift) % $pattern.Count]
        }
        $lines.Add("$chromosome`t$position`trice_demo_snp_$marker`t$ref`t$alt`t.`tPASS`t.`tGT`t$($genotypes -join "`t")")
    }
}

$vcfContent = [string]::Join([System.Environment]::NewLine, [string[]]$lines)
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText([string]$vcfPath, [string]$vcfContent, $utf8WithoutBom)

$mappingRows = $samples | ForEach-Object {
    [PSCustomObject]@{
        FID = $_.Sample
        IID = $_.Sample
        material_code = $_.Material
        note = "Demo sample mapping"
    }
}
$mappingRows | Export-Csv -Path $mappingPath -NoTypeInformation -Encoding UTF8

$traitValues = @(538.4, 557.1, 548.9, 570.6, 535.2, 552.8, 545.6, 568.3, 521.5, 529.4)
$phenotypeRows = for ($sampleIndex = 0; $sampleIndex -lt $samples.Count; $sampleIndex++) {
    [PSCustomObject]@{
        FID = $samples[$sampleIndex].Sample
        IID = $samples[$sampleIndex].Sample
        material_code = $samples[$sampleIndex].Material
        analysis_environment = "2025-Nanchang-standard_n"
        trait_value = $traitValues[$sampleIndex]
    }
}
$phenotypeRows | Export-Csv -Path $phenotypePath -NoTypeInformation -Encoding UTF8

Write-Host "Generated: $vcfPath"
Write-Host "Generated: $mappingPath"
Write-Host "Generated: $phenotypePath"
