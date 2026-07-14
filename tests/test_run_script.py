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
