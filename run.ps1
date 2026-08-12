# Binbin - Tek seferde tüm pipeline'ı çalıştır
# Kullanım: proje kökünden  ->  .\run.ps1
# .venv'i elle aktive etmene gerek yok; script venv python'unu kendi bulur.
#
# Kapsam (-All diğer ikisiyle BİRLİKTE verilemez; -City ve -Country
#         birlikte verilebilir, VE olarak uygulanır):
#   .\run.ps1                                   -> config.DEFAULT_SCOPE (Türkiye + İstanbul)
#   .\run.ps1 -All                              -> tüm ülke/şehirler
#   .\run.ps1 -City "İstanbul Avrupa","Bursa"   -> yalnız bu şehirler
#   .\run.ps1 -Country "Bosnia and Herzegovina" -> yalnız bu ülke
# Birden fazla değer VİRGÜLLE verilir (PowerShell dizi söz dizimi); adlar Türkçe
# karakterleriyle ve TAM eşleşmeli yazılır — yanlış ad artık sessizce boş sonuç
# üretmez, CLI net hata verir.

param(
    [Nullable[double]]$WiDuration,
    [Nullable[double]]$WiDistance,
    [string[]]$Country,
    [string[]]$City,
    [switch]$All
)

# Eşik sorularından ÖNCE reddet: kullanıcı iki soru cevaplayıp sonra hata almasın.
if ($All -and ($Country -or $City)) {
    Write-Host "HATA: -All ile -Country/-City birlikte verilemez." -ForegroundColor Red
    exit 1
}

# Her bayrak ve degeri AYRI token olmali. Bayragi ve degeri tek bir string'de
# birlestirmek argparse'ta tek arguman olur ve "Istanbul Avrupa" gibi bosluklu
# bir sehir adi ikiye bolunur; bu yuzden daima iki elemanli dizi eklenir.
$scopeArgs = @()
if ($All) {
    $scopeArgs = @("--all")
    Write-Host "Kapsam: TUM ULKELER/SEHIRLER (--all)" -ForegroundColor Yellow
}
elseif ($Country -or $City) {
    foreach ($c in $Country) { $scopeArgs += @("--country", $c) }
    foreach ($c in $City) { $scopeArgs += @("--city", $c) }
    Write-Host "Kapsam: OZEL" -ForegroundColor Yellow
    Write-Host "  $($scopeArgs -join ' ')" -ForegroundColor Yellow
}
else {
    Write-Host "Kapsam: VARSAYILAN (config.DEFAULT_SCOPE) - TUM VERI DEGIL." -ForegroundColor Yellow
    Write-Host "  Tum veri icin -All, secmek icin -City/-Country kullan." -ForegroundColor Yellow
}

# Durum-degisim defteri (stg_status_raw) ulke/sehir kolonu TASIMAZ; ingest_status
# kapsami bilincli yok sayar. Kullanici 1. adimin filtrelendigini sanmasin.
if (-not $All) {
    Write-Host "NOT: arac durum-degisim CSV'si KAPSAM FILTRESI UYGULAMAZ -" -ForegroundColor Yellow
    Write-Host "     stg_status_raw'da ulke/sehir adi yok, defter DAIMA FILO GENELINDE yuklenir." -ForegroundColor Yellow
}

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
# Not: data_raw/'daki surus VE arac durum-degisim CSV'leri turlerine gore otomatik
# ayrilip sirayla yuklenir. Ayni dosya zaten yuklendiyse guard atlar (SKIPPED).
# Yeniden yuklemek icin: --force
& $py -m binbin.cli ingest @scopeArgs
if ($LASTEXITCODE -ne 0) { Write-Host "INGEST BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== ADIM 2/4: CLASSIFY (siniflandirma) ==========" -ForegroundColor Cyan
# --refresh: kural kitabi (fleet_status_reason) veya siniflandirma mantigi degismis
# olabilir; pipeline her kosuda kalici ride.failure_category'yi canli analizle ayni
# tutsun diye tum basarisiz suruslar yeniden siniflandirilir.
& $py -m binbin.cli classify --refresh @scopeArgs
if ($LASTEXITCODE -ne 0) { Write-Host "CLASSIFY BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== ADIM 3/4: ASSESS (sahte ariza degerlendirmesi) ==========" -ForegroundColor Cyan
# --refresh: ADIM 2 gibi. Artimli mod aday kumesinden DUSEN satirlari silmez;
# bayat degerlendirmeler kalir ve v_false_fault_by_subregion onlari sayar.
& $py -m binbin.cli assess --refresh @scopeArgs
if ($LASTEXITCODE -ne 0) { Write-Host "ASSESS BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== ADIM 4/4: ANALYZE (analiz + grafikler) ==========" -ForegroundColor Cyan
# Esik karsilastirmasi: Mevcut Kural (120sn/60m) ve kullanicinin girdigi Ozel Kural.
& $py -m binbin.cli analyze --false-fault --detay --derin --esik-taramasi --charts out\ --wi-duration $wiDurationText --wi-distance $wiDistanceText @scopeArgs
if ($LASTEXITCODE -ne 0) { Write-Host "ANALYZE BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== TAMAMLANDI ==========" -ForegroundColor Green
Write-Host "Grafikler 'out\' klasorunde." -ForegroundColor Green
