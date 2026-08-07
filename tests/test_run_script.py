"""run.ps1 özel kural giriş sözleşmesi testleri."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "run.ps1"


def test_run_script_prompts_for_duration_and_distance_with_safe_defaults():
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert "Read-Host" in script
    assert '-Unit "saniye"' in script
    assert '-Unit "metre"' in script
    assert "-DefaultValue 75" in script
    assert "-DefaultValue 60" in script
    assert "-Minimum 60" in script
    assert "-Maximum 200" in script
    assert "-Minimum 20" in script
    assert "-Maximum 150" in script


def test_run_script_passes_resolved_user_values_to_analyze():
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert "--wi-duration $wiDurationText" in script
    assert "--wi-distance $wiDistanceText" in script
    assert "--wi-duration 100 --wi-distance 45" not in script
    assert "[Nullable[double]]$WiDuration" in script
    assert "[Nullable[double]]$WiDistance" in script


# --- kapsam (scope) parametreleri ------------------------------------------
def test_run_script_scope_parameters_are_string_arrays():
    """CLI bayrakları argparse `action="append"` — PowerShell karşılığı dizidir."""
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert "[string[]]$Country" in script
    assert "[string[]]$City" in script
    assert "[switch]$All" in script


def test_run_script_builds_flat_token_array_for_scope():
    """Her bayrak ve değeri AYRI argv token'ı olmalı.

    `"--city $c"` tek string olsaydı argparse bunu tek argüman görür ve
    'İstanbul Avrupa' değeri boşluktan bölünürdü.
    """
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert '$scopeArgs += @("--country", $c)' in script
    assert '$scopeArgs += @("--city", $c)' in script
    assert '"--country $' not in script
    assert '"--city $' not in script


def test_run_script_rejects_all_combined_with_country_or_city():
    """CLI de reddediyor; script erken reddetsin ki kullanıcı önce eşik
    sorularını cevaplayıp sonra hata almasın."""
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert '$All -and ($Country -or $City)' in script
    assert "-ForegroundColor Red" in script


def test_run_script_passes_scope_to_all_four_steps():
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert script.count("@scopeArgs") == 4


def test_run_script_warns_status_ingest_ignores_scope():
    """`ingest_status` kapsamı bilinçli yok sayar (stg_status_raw'da şehir yok);
    kullanıcı 1. adımın filtrelendiğini sanmamalı."""
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert "FILO GENELINDE" in script
    assert "stg_status_raw" in script


def test_run_script_announces_default_scope_is_not_all_data():
    """Bayraksız çağrı DEFAULT_SCOPE'tur (Türkiye + İstanbul), 'tüm veri' değil."""
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert "DEFAULT_SCOPE" in script


def test_run_script_console_text_stays_ascii():
    """run.ps1 BOM'suz; Windows PowerShell 5.1 dosyayı ANSI okur ve Türkçe
    literal bozulur. Parametre DEĞERLERİ çalışma anında geldiği için etkilenmez.
    """
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    for line in script.splitlines():
        if "Write-Host" in line:
            assert line.isascii(), f"ASCII olmayan konsol metni: {line!r}"
