"""İki senaryolu analiz motoru testleri — DB'siz."""

from datetime import datetime, timedelta

from binbin.core.scenario_analysis import (
    FailureScenario,
    ScenarioStatus,
    analyze_scenarios,
    build_scenarios,
)
from binbin.cli.main import (
    _print_scenario_causes,
    _print_scenario_comparisons,
    _print_scenario_control,
    _print_scenario_criteria,
    _print_scenario_definitions,
    _print_scenario_false_fault,
    _print_scenario_hourly,
    _print_scenario_overview,
    _print_scenario_subregions,
    _print_scenario_vehicles,
)


_T0 = datetime(2026, 6, 1, 12, 0)


def _row(ride_id, outcome, duration, distance):
    return {
        "ride_id": ride_id,
        "source_ref": f"r{ride_id}",
        "user_ref": f"u{ride_id}",
        "start_time": _T0 + timedelta(minutes=ride_id),
        "end_time": _T0 + timedelta(minutes=ride_id + 1),
        "duration_sec": duration,
        "distance_m": distance,
        "outcome": outcome,
        "vehicle_id": ride_id,
        "city_id": 1,
        "external_code": f"34-{ride_id}",
        "city": "İstanbul",
        "sub_region_code": None,
        "sub_region_name": None,
        "local_hour": 12,
        "triggered_regulation_id": None,
        "end_reason_id": None,
        "end_message": None,
        "unlock_ack": None,
        "start_battery_pct": None,
        "connection_lost": None,
        "motor_error_code": None,
        "bms_error_code": None,
        "user_cancelled": None,
        "payment_status": None,
        "data_quality_flags": ["DISTANCE_NULL"] if distance is None else [],
        "rating": None,
        "comment_text": None,
        "next_ride_id": None,
        "next_start_time": None,
        "next_duration_sec": None,
        "next_distance_m": None,
        "next_outcome": None,
    }


def _report():
    rows = [
        _row(1, "BASARISIZ_HARD", 200, 100),  # yalnız özel eşikte başarılı
        _row(2, "BASARILI", 110, 55),          # yalnız mevcut kuralda başarısız
        _row(3, "BASARILI", 80, 40),           # üçünde de başarısız
        _row(4, "BASARISIZ_HARD", 80, 40),     # üçünde de başarısız
        _row(5, "BASARISIZ_HARD", 50, None),   # yalnız özel eşikte değerlendirilemez
    ]
    return analyze_scenarios(rows, custom=(100, 45))


def test_scenario_definitions_have_meaningful_keys_and_labels():
    scenarios = build_scenarios((100, 45))
    assert [s.key for s in scenarios] == ["current_rule", "custom_rule"]
    assert [s.label for s in scenarios] == ["Mevcut Kural", "Özel Kural"]


def test_custom_only_missing_measurement_is_unevaluated():
    scenario = FailureScenario("x", "X", 100, 45, include_source_failure=False)
    assert scenario.status("BASARISIZ_HARD", 50, None) is ScenarioStatus.UNEVALUATED
    assert scenario.status("BASARISIZ_HARD", 100, 40) is ScenarioStatus.SUCCESS


def test_failure_rule_needs_both_thresholds_and_keeps_source_hard():
    """Regresyon kilidi: başarısızlık için İKİ eşiğe de takılmalı (süre<X VE mesafe<Y);
    birini sağlayıp diğerini sağlamazsa BAŞARILI. Mevcut Kural'da BASARISIZ_HARD ayrıca
    başarısızlık tetikler. Kural HİÇBİR ZAMAN OR değildir."""
    current, custom = build_scenarios((90, 50))
    F, S = ScenarioStatus.FAILED, ScenarioStatus.SUCCESS

    # Mevcut Kural (120/60): yalnız ikisi de altındaysa başarısız.
    assert current.status("BASARILI", 100, 100) is S   # biri takıldı -> başarılı
    assert current.status("BASARILI", 90, 150) is S    # mesafe geçti -> başarılı
    assert current.status("BASARILI", 500, 55) is S    # süre geçti -> başarılı
    assert current.status("BASARILI", 60, 40) is F     # ikisi de altında -> başarısız
    assert current.status("BASARISIZ_HARD", 90, 150) is F  # HARD daima başarısız

    # Özel Kural (90/50): kaynaktan bağımsız, yine ikisi de gerekli.
    assert custom.status("BASARILI", 80, 40) is F
    assert custom.status("BASARISIZ_HARD", 80, 40) is F
    assert custom.status("BASARILI", 80, 100) is S     # biri takıldı -> başarılı
    assert custom.status("BASARISIZ_HARD", 80, 100) is S


def test_out_of_content_counts_thread_into_report():
    rows = [_row(1, "BASARILI", 90, 40)]
    ooc = {"total": 4144, "by_distance": 4140, "by_duration": 4}
    report = analyze_scenarios(rows, ooc_counts=ooc)
    assert report["data_quality"]["out_of_content"] == ooc
    # Param verilmezse sıfır kova (geriye dönük uyum).
    assert analyze_scenarios(rows)["data_quality"]["out_of_content"] == {
        "total": 0, "by_distance": 0, "by_duration": 0
    }


def test_two_scenario_totals_and_single_transition():
    report = _report()
    scenarios = report["scenarios"]
    assert scenarios["current_rule"]["overview"] == {
        "total": 5, "evaluated": 5, "failed": 5, "success": 0,
        "unevaluated": 0, "failure_rate_pct": 100.0,
    }
    assert scenarios["custom_rule"]["overview"]["failed"] == 2
    assert scenarios["custom_rule"]["overview"]["success"] == 2
    assert scenarios["custom_rule"]["overview"]["unevaluated"] == 1

    comparisons = {(c["from_key"], c["to_key"]): c for c in report["comparisons"]}
    assert set(comparisons) == {("current_rule", "custom_rule")}
    comparison = comparisons[("current_rule", "custom_rule")]
    assert comparison["failed_to_success"] == 2
    assert comparison["unevaluated"] == 1
    assert comparison["failed_to_unevaluated"] == 1


def test_cli_uses_readable_scenario_names(capsys):
    report = _report()
    for scenario in report["scenarios"].values():
        scenario["subregion"]["sub_regions"] = [{
            "city": "İstanbul",
            "sub_region_code": 607,
            "sub_region_name": "Test",
            "total_rides": 1234,
            "failed": 100,
            "failure_rate_pct": 8.1,
            "false_alarm_per_1000": 2.5,
        }]
    _print_scenario_definitions(report)
    _print_scenario_overview(report)
    _print_scenario_comparisons(report)
    _print_scenario_causes(report)
    _print_scenario_criteria(report)
    _print_scenario_control(report)
    _print_scenario_false_fault(report)
    _print_scenario_vehicles(report)
    _print_scenario_subregions(report)
    _print_scenario_hourly(report)
    output = capsys.readouterr().out
    assert "Mevcut Kural" in output
    assert "Özel Kural" in output
    assert "Kaynak + Özel Eşik" not in output
    assert "Yalnız Özel Eşik" not in output
    assert "source_plus_custom_rule" not in output
    assert "custom_rule_only" not in output
    assert "mongo_distance_meters" in output
    assert "Bölge 607 · n=1.234" in output
    assert "Şehir / saat" in output
    assert "n" in output
    assert "5" in output
    assert "What-If 1" not in output
    assert "What-If 2" not in output


def test_cli_rejects_mismatched_subregion_and_hourly_denominators():
    report = _report()
    scenarios = list(report["scenarios"].values())
    for total, scenario in zip((100, 101), scenarios):
        scenario["subregion"]["sub_regions"] = [{
            "city": "İstanbul", "sub_region_code": 607, "sub_region_name": "Test",
            "total_rides": total, "failed": 1, "failure_rate_pct": 1.0,
            "false_alarm_per_1000": 0.0,
        }]
    try:
        _print_scenario_subregions(report)
    except ValueError as exc:
        assert "toplam sürüş sayıları uyuşmuyor" in str(exc)
    else:
        raise AssertionError("Uyumsuz alt bölge toplamı reddedilmeliydi.")

    scenarios[0]["hourly"]["buckets"][0]["total"] = 100
    scenarios[1]["hourly"]["buckets"][0]["total"] = 101
    try:
        _print_scenario_hourly(report)
    except ValueError as exc:
        assert "toplam sürüş sayıları uyuşmuyor" in str(exc)
    else:
        raise AssertionError("Uyumsuz saatlik toplam reddedilmeliydi.")
