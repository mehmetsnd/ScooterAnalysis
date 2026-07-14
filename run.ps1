# Binbin - Tek seferde tüm pipeline'ı çalıştır
# Kullanım: proje kökünden  ->  .\run.ps1
# .venv'i elle aktive etmene gerek yok; script venv python'unu kendi bulur.

param(
    [Nullable[double]]$WiDuration,
    [Nullable[double]]$WiDistance
)

$ErrorActionPreference = "Stop"

# Proje kökü = bu script'in bulunduğu klasör
Set-Location -Path $PSScriptRoot

function Resolve-CustomThreshold {
    param(
        [Nullable[double]]$ProvidedValue,
        [string]$Label,
        [string]$Unit,
        [double]$DefaultValue,
        [double]$Minimum,
        [double]$Maximum
    )

    $culture = [Globalization.CultureInfo]::InvariantCulture

    if ($null -ne $ProvidedValue) {
        $value = [double]$ProvidedValue
        if ($value -lt $Minimum -or $value -gt $Maximum) {
            throw "$Label $Minimum-$Maximum $Unit araliginda olmali."
        }
        return $value.ToString("0.###", $culture)
    }

    while ($true) {
        $rawValue = Read-Host "$Label ($Unit) [$Minimum-$Maximum, varsayilan: $DefaultValue]"
        if ([string]::IsNullOrWhiteSpace($rawValue)) {
            return $DefaultValue.ToString("0.###", $culture)
        }

        $normalized = $rawValue.Trim().Replace(",", ".")
        $value = 0.0
        $parsed = [double]::TryParse(
            $normalized,
            [Globalization.NumberStyles]::Float,
            $culture,
            [ref]$value
        )
        if (-not $parsed) {
            Write-Host "Gecersiz deger. Ornek: 100 veya 100,5" -ForegroundColor Yellow
            continue
        }
        if ($value -lt $Minimum -or $value -gt $Maximum) {
            Write-Host "$Label $Minimum-$Maximum $Unit araliginda olmali." -ForegroundColor Yellow
            continue
        }
        return $value.ToString("0.###", $culture)
    }
}

Write-Host "`n========== OZEL KURAL AYARLARI ==========" -ForegroundColor Cyan
$wiDurationText = Resolve-CustomThreshold `
    -ProvidedValue $WiDuration `
    -Label "Sure esigi" `
    -Unit "saniye" `
    -DefaultValue 75 `
    -Minimum 60 `
    -Maximum 200
$wiDistanceText = Resolve-CustomThreshold `
    -ProvidedValue $WiDistance `
    -Label "Mesafe esigi" `
    -Unit "metre" `
    -DefaultValue 60 `
    -Minimum 20 `
    -Maximum 150
Write-Host "Ozel Kural: sure < $wiDurationText saniye VE mesafe < $wiDistanceText metre" -ForegroundColor Green

# venv python'u (yoksa PATH'teki python'a düş)
if (Test-Path ".\.venv\Scripts\python.exe") {
    $py = ".\.venv\Scripts\python.exe"
} else {
    $py = "python"
    Write-Host "UYARI: .venv bulunamadi, PATH'teki python kullanilacak." -ForegroundColor Yellow
}

# src-layout: paketler src/ altinda
$env:PYTHONPATH = "src"

Write-Host "`n========== ADIM 1/4: INGEST (CSV -> PostgreSQL) ==========" -ForegroundColor Cyan
# Not: Ayni dosya zaten yuklendiyse guard atlar (SKIPPED). Yeniden yuklemek icin: --force
& $py -m binbin.cli ingest
if ($LASTEXITCODE -ne 0) { Write-Host "INGEST BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== ADIM 2/4: CLASSIFY (siniflandirma) ==========" -ForegroundColor Cyan
& $py -m binbin.cli classify
if ($LASTEXITCODE -ne 0) { Write-Host "CLASSIFY BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== ADIM 3/4: ASSESS (sahte ariza degerlendirmesi) ==========" -ForegroundColor Cyan
& $py -m binbin.cli assess
if ($LASTEXITCODE -ne 0) { Write-Host "ASSESS BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== ADIM 4/4: ANALYZE (analiz + grafikler) ==========" -ForegroundColor Cyan
# Esik karsilastirmasi: Mevcut Kural (120sn/60m) ve kullanicinin girdigi Ozel Kural.
& $py -m binbin.cli analyze --false-fault --detay --derin --charts out\ --wi-duration $wiDurationText --wi-distance $wiDistanceText
if ($LASTEXITCODE -ne 0) { Write-Host "ANALYZE BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== TAMAMLANDI ==========" -ForegroundColor Green
Write-Host "Grafikler 'out\' klasorunde." -ForegroundColor Green
