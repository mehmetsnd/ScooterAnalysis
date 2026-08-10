"""İki senaryolu grafiklerin smoke testleri."""

import pytest
from PIL import Image

from binbin.reporting.charts import (
    MIN_HOURLY_BUCKET_RIDES,
    chart_scenario_causes,
    chart_scenario_control,
    chart_scenario_false_fault,
    chart_scenario_hourly,
    chart_scenario_overview,
    chart_scenario_subregions,
    chart_scenario_vehicles,
)
from tests.test_scenario_analysis import _report


@pytest.mark.parametrize(
    "chart_fn",
    [
        chart_scenario_overview,
        chart_scenario_causes,
        chart_scenario_control,
        chart_scenario_false_fault,
        chart_scenario_vehicles,
        chart_scenario_subregions,
        chart_scenario_hourly,
    ],
)
def test_scenario_chart_writes_png(chart_fn, tmp_path):
    path = chart_fn(_report(), tmp_path)
    assert path.exists()
    assert path.suffix == ".png"
    assert path.stat().st_size > 0


def _report_with_cities(city_count: int) -> dict:
    """`_report()`'u alıp saatlik kovaları `city_count` şehir için sentetikler.

    Her şehre MIN_HOURLY_BUCKET_RIDES üstünde hacimli, _MIN_HOURLY_CITY_POINTS'ten
    fazla saat kovası verilir; böylece hepsi panel olarak çizilir.
    """
    report = _report()
    buckets = [
        {
            "city": f"Sehir{c}",
            "hour": h,
            "total": MIN_HOURLY_BUCKET_RIDES * 10,
            "failed": MIN_HOURLY_BUCKET_RIDES,
            "failure_rate_pct": 10.0,
        }
        for c in range(city_count)
        for h in range(12)
    ]
    for key in report["scenario_order"]:
        report["scenarios"][key]["hourly"]["buckets"] = list(buckets)
    return report


def _png_width(path) -> int:
    with Image.open(path) as im:
        return im.width


def test_hourly_chart_width_scales_with_panel_count(tmp_path):
    """Regresyon kilidi: sütun sayısı panel sayısını AŞMAZ.

    `ncols` sabit 4 iken yalnız 2 şehir çizilebildiğinde figürün sağ yarısı boş
    kalıyor ve PNG "kırpılmış" gibi görünüyordu (teslim öncesi QA'da bulundu).
    Genişlik panel sayısıyla ölçeklendiği için az panelli figür DAHA DAR olmalı.
    """
    narrow = chart_scenario_hourly(_report_with_cities(2), tmp_path / "az")
    wide = chart_scenario_hourly(_report_with_cities(8), tmp_path / "cok")
    assert _png_width(narrow) < _png_width(wide)


def test_hourly_chart_full_row_keeps_reference_width(tmp_path):
    """4 ve 8 panel aynı genişlikte olmalı: ikisi de tam 4 sütunluk ızgara kurar
    (8 panel ikinci satıra taşar). Düzeltme eski görünümü bozmadı."""
    four = chart_scenario_hourly(_report_with_cities(4), tmp_path / "dort")
    eight = chart_scenario_hourly(_report_with_cities(8), tmp_path / "sekiz")
    assert _png_width(four) == _png_width(eight)


def test_hourly_legend_covers_scenario_missing_from_first_panel(tmp_path, monkeypatch):
    """Regresyon kilidi: gösterge ÇİZİLEN her senaryoyu içermeli. Yalnız ilk panelden
    toplanırken, orada hacim şartına takılan senaryo başka panelde çizildiği hâlde
    göstergeden düşüyordu — hangi rengin hangi senaryo olduğu okunamıyordu.
    """
    from binbin.reporting import charts

    report = _report_with_cities(2)
    keys = report["scenario_order"]
    assert len(keys) > 1, "test iki senaryolu rapor gerektirir"
    # İkinci senaryo YALNIZ ilk panelin şehrinde hacim şartının altında kalsın.
    top_city = "Sehir0"
    report["scenarios"][keys[1]]["hourly"]["buckets"] = [
        b if b["city"] != top_city else {**b, "total": MIN_HOURLY_BUCKET_RIDES - 1}
        for b in report["scenarios"][keys[1]]["hourly"]["buckets"]
    ]

    captured: list[list[str]] = []
    real_legend = charts.plt.Figure.legend

    def spy(self, handles, labels, *a, **kw):
        captured.append(list(labels))
        return real_legend(self, handles, labels, *a, **kw)

    monkeypatch.setattr(charts.plt.Figure, "legend", spy)
    charts.chart_scenario_hourly(report, tmp_path)

    labels = captured[0]
    expected = [report["scenarios"][k]["label"] for k in keys]
    assert labels == expected


@pytest.mark.parametrize(
    "chart_name", ["chart_threshold_scan", "chart_threshold_tradeoff"]
)
def test_threshold_chart_writes_png(chart_name, tmp_path):
    """Eşik grafikleri senaryo raporu değil `scan` dict'i alır; kapsam dışı kalmasınlar."""
    from binbin.core.threshold_scan import scan_thresholds
    from binbin.reporting import charts

    path = getattr(charts, chart_name)(scan_thresholds([]), tmp_path)
    assert path.exists() and path.stat().st_size > 0
