# Binbin - Tek seferde tüm pipeline'ı çalıştır
# Kullanım: proje kökünden  ->  .\run.ps1
# .venv'i elle aktive etmene gerek yok; script venv python'unu kendi bulur.

$ErrorActionPreference = "Stop"

# Proje kökü = bu script'in bulunduğu klasör
Set-Location -Path $PSScriptRoot

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
& $py -m binbin.cli analyze --false-fault --detay --derin --charts out\
if ($LASTEXITCODE -ne 0) { Write-Host "ANALYZE BASARISIZ!" -ForegroundColor Red; exit 1 }

Write-Host "`n========== TAMAMLANDI ==========" -ForegroundColor Green
Write-Host "Grafikler 'out\' klasorunde." -ForegroundColor Green
