"""İki senaryolu grafiklerin smoke testleri."""

import pytest

from binbin.reporting.charts import (
    chart_scenario_causes,
    chart_scenario_control,
    chart_scenario_false_fault,
    chart_scenario_hourly,
    chart_scenario_overview,
    chart_scenario_subregions,
    chart_scenario_transitions,
    chart_scenario_vehicles,
)
from tests.test_scenario_analysis import _report


@pytest.mark.parametrize(
    "chart_fn",
    [
        chart_scenario_overview,
        chart_scenario_transitions,
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
