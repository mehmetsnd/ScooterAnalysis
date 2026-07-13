"""Grafik fonksiyonları smoke-test — DB'siz, temsili dict ile.

Her fonksiyon `tmp_path`'e PNG yazar; dönen Path var olmalı ve dosya boyu > 0
olmalı (çökme/kırılma kalkanı). Agg backend; ekran gerektirmez.
"""

import pytest

from binbin.reporting.charts import (
    chart_cause_distribution,
    chart_control_group,
    chart_criteria_whatif,
    chart_hourly_failure_rate,
    chart_subregion_false_fault,
    chart_vehicle_hotspots,
)


# --- Temsili (representative) mock veriler -----------------------------------

_CAUSE_DATA = {
    "total_failed": 12_000,
    "categories": [
        {"category": "TEKNIK", "count": 1000, "pct": 8.3},
        {"category": "REGULASYON", "count": 15, "pct": 0.1},
        {"category": "OPERASYONEL", "count": 5, "pct": 0.0},
    ],
    "signalless": {"count": 10_980, "pct": 91.5},
}

_CONTROL_DATA = {
    "groups": [
        {"group": "ariza_metinli", "total": 500, "healthy": 90, "healthy_rate_pct": 18.0},
        {"group": "herhangi_bildirimli", "total": 800, "healthy": 200, "healthy_rate_pct": 25.0},
        {"group": "bildirimsiz", "total": 400, "healthy": 160, "healthy_rate_pct": 40.0},
    ]
}

_VEHICLE_DATA = {
    "min_failures": 10,
    "vehicles": [
        {"vehicle_id": 1, "external_code": "34AB001", "failures": 55},
        {"vehicle_id": 2, "external_code": "34AB002", "failures": 42},
        {"vehicle_id": 3, "external_code": "34AB003", "failures": 30},
        {"vehicle_id": 4, "external_code": "34AB004", "failures": 18},
        {"vehicle_id": 5, "external_code": "34AB005", "failures": 12},
    ],
}

_SUBREGION_DATA = {
    "min_rides": 2000,
    "sub_regions": [
        {"city": "İstanbul Avrupa", "sub_region_code": 599, "false_alarm_per_1000": 6.06},
        {"city": "İstanbul Anadolu", "sub_region_code": 216, "false_alarm_per_1000": 3.12},
        {"city": "Ankara", "sub_region_code": 312, "false_alarm_per_1000": 1.45},
    ],
}

_WHATIF_DATA = {
    "real": {
        "duration_threshold": 120,
        "distance_threshold": 60,
        "failed_meeting_criterion": {"count": 15980, "pct_of_failed": 75.0},
        "failed_total": 21313,
    },
    "whatif": {
        "duration_threshold": 100,
        "distance_threshold": 45,
        "failed_meeting_criterion": {"count": 12100, "pct_of_failed": 56.8},
        "failed_total": 21313,
    },
    "delta": {"confirm_pct_points": -18.2, "confirm_count_delta": -3880, "rel_pct": -24.3},
}

_HOURLY_DATA = {
    "buckets": [
        {"city": "İstanbul", "hour": h, "total": 500, "failed": int(500 * (3 + h % 5) / 100),
         "failure_rate_pct": round((3 + h % 5), 1)}
        for h in range(24)
    ] + [
        {"city": "Üsküp", "hour": h, "total": 200, "failed": int(200 * (5 + h % 4) / 100),
         "failure_rate_pct": round((5 + h % 4), 1)}
        for h in range(24)
    ],
}


# --- Testler -----------------------------------------------------------------

def _assert_png(path):
    """Dönen dosyanın var olduğunu ve boş olmadığını doğrula."""
    assert path.exists(), f"PNG oluşturulmadı: {path}"
    assert path.stat().st_size > 0, f"PNG dosyası boş: {path}"
    assert path.suffix == ".png"


def test_chart_cause_distribution(tmp_path):
    path = chart_cause_distribution(_CAUSE_DATA, tmp_path)
    _assert_png(path)


def test_chart_control_group(tmp_path):
    path = chart_control_group(_CONTROL_DATA, tmp_path)
    _assert_png(path)


def test_chart_vehicle_hotspots(tmp_path):
    path = chart_vehicle_hotspots(_VEHICLE_DATA, tmp_path)
    _assert_png(path)


def test_chart_subregion_false_fault(tmp_path):
    path = chart_subregion_false_fault(_SUBREGION_DATA, tmp_path)
    _assert_png(path)


def test_chart_hourly_failure_rate(tmp_path):
    path = chart_hourly_failure_rate(_HOURLY_DATA, tmp_path)
    _assert_png(path)


def test_chart_criteria_whatif(tmp_path):
    path = chart_criteria_whatif(_WHATIF_DATA, tmp_path)
    _assert_png(path)
