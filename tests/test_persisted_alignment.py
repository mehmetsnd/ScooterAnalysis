"""Kalıcı yazma yolunun (classify_all/assess_all) canlı motorla hizalı kaldığını
kilitler. Ayrışırlarsa DB, `analyze`'ın başarısız saydığı sürüşleri görmez.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from binbin.core.scenario_analysis import CURRENT_DISTANCE_M, CURRENT_DURATION_SEC
from binbin.data.assess import assess_all
from binbin.data.classify import classify_all

ROOT = Path(__file__).resolve().parents[1]
_T0 = datetime(2026, 6, 1, 12, 0)


class _FakeConn:
    rowcount = 0

    def __init__(self, batches, calls):
        self._batches, self.calls = batches, calls

    def execute(self, statement, params=None):
        self.calls.append((" ".join(str(statement).split()), params))
        self._last = self._batches.pop(0) if self._batches else []
        return self

    def mappings(self):
        return self

    def all(self):
        return self._last

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    """`batches`: her execute() çağrısının sırayla döndüreceği satır listesi."""

    def __init__(self, *batches):
        self.calls = []
        self._batches = list(batches)

    def begin(self):
        return _FakeConn(self._batches, self.calls)


def _assess_row(**over):
    row = {
        "ride_id": 1, "start_time": _T0, "end_time": _T0 + timedelta(seconds=40),
        "vehicle_id": 7, "outcome": "BASARISIZ_HARD", "duration_sec": 40,
        "distance_m": 5, "end_reason_id": None, "end_message": "motor calismiyor",
        "rating": None, "comment_text": None, "field_fault": False,
        "next_ride_id": 2, "next_start_time": _T0 + timedelta(minutes=30),
        "next_outcome": "BASARILI", "next_duration_sec": 900,
        "next_distance_m": 1500,
    }
    row.update(over)
    return row


def _call_containing(engine, needle):
    return next(c for c in engine.calls if needle in c[0])


def _insert_payload(engine):
    return _call_containing(engine, "INSERT INTO false_fault_assessment")[1][0]


# --- assess_all -------------------------------------------------------------
def test_assess_all_rescores_next_ride_under_current_rule():
    """Ham outcome BASARILI ama eşik altındaki bir sonraki sürüş, canlı motorda
    başarısızdır; kalıcı next_ride_ok bunu yansıtmalı."""
    engine = _FakeEngine([], [_assess_row(next_duration_sec=30, next_distance_m=10)])
    assess_all(engine, None, refresh=True)
    assert _insert_payload(engine)["next_ride_ok"] is False


def test_assess_all_keeps_healthy_next_ride_as_proof():
    engine = _FakeEngine([], [_assess_row()])
    assess_all(engine, None, refresh=True)
    payload = _insert_payload(engine)
    assert payload["next_ride_ok"] is True
    assert payload["healthy_proof"] is True


def test_assess_all_refresh_deletes_rows_that_left_the_candidate_set():
    """UPSERT tek başına yetmez: kural daralınca eski satırlar tabloda kalır."""
    engine = _FakeEngine([], [])
    assess_all(engine, None, refresh=True)
    sql, _ = _call_containing(engine, "DELETE FROM false_fault_assessment")
    assert "OUT_OF_CONTENT" in sql
    assert f"< {CURRENT_DURATION_SEC}" in sql


def test_assess_all_incremental_run_does_not_delete():
    engine = _FakeEngine([], [])
    assess_all(engine, None, refresh=False)
    assert not any("DELETE" in c[0] for c in engine.calls)


def test_assess_all_selects_the_same_set_as_analysis_timeline():
    engine = _FakeEngine([], [])
    assess_all(engine, None, refresh=True)
    sql, params = _call_containing(engine, "WITH scoped AS")
    assert f"< {CURRENT_DURATION_SEC}" in sql and ":fsig_max_dur" in sql
    assert "OUT_OF_CONTENT" in sql
    assert "r.outcome IN ('BASARILI', 'BASARISIZ_HARD')" in sql
    assert "ORDER BY start_time, ride_id" in sql
    # LEAD(duration_sec) ancak kolon scoped CTE'sinde seçilmişse çalışır.
    assert "LEAD(duration_sec)" in sql
    assert "duration_sec" in sql.split("FROM ride r")[0]
    assert params["fsig_max_dur"] == CURRENT_DURATION_SEC


# --- classify_all -----------------------------------------------------------
def test_classify_all_uses_current_rule_and_thresholds_guard():
    engine = _FakeEngine([])
    classify_all(engine, None)
    sql, params = engine.calls[0]
    assert f"< {CURRENT_DURATION_SEC}" in sql and ":fsig_max_dur" in sql
    assert params["fsig_max_dist"] == CURRENT_DISTANCE_M


def test_classify_all_refresh_aborts_when_migration_is_missing():
    """db/08 uygulanmadan reset kendi transaction'ında COMMIT olur, sonraki UPDATE
    CheckViolation verir ve ride.failure_category tablo genelinde SİLİNMİŞ kalır.
    Bu yüzden reset'ten ÖNCE kısıt denetlenir."""
    engine = _FakeEngine([{"migrated": False}])
    with pytest.raises(RuntimeError, match="db/08"):
        classify_all(engine, None, refresh=True)
    assert not any("UPDATE ride SET" in c[0] for c in engine.calls)


def test_classify_all_reset_set_is_a_superset_of_the_select_set():
    """reset OUT_OF_CONTENT'i kapsar, select dışlar; asimetri kasıtlı."""
    engine = _FakeEngine([{"migrated": True}], [], [])
    classify_all(engine, None, refresh=True)
    reset_sql = _call_containing(engine, "UPDATE ride SET")[0]
    select_sql = _call_containing(engine, "LIMIT :batch")[0]
    assert f"< {CURRENT_DURATION_SEC}" in reset_sql
    assert f"< {CURRENT_DURATION_SEC}" in select_sql
    assert "OUT_OF_CONTENT" not in reset_sql
    assert "OUT_OF_CONTENT" in select_sql


def test_classify_all_passes_telemetry_to_the_core():
    """Telemetri bugün NULL; geçilmezse dolduğu gün classify ile analyze ayrışır."""
    engine = _FakeEngine([{
        "ride_id": 1, "start_time": _T0, "end_message": None, "comment_text": None,
        "outcome": "BASARISIZ_HARD", "field_category": None, "field_reason": None,
        "triggered_regulation_id": None, "unlock_ack": None, "start_battery_pct": None,
        "connection_lost": None, "motor_error_code": None, "bms_error_code": None,
        "user_cancelled": True, "payment_status": None,
    }])
    classify_all(engine, None)
    assert engine.calls[-1][1][0]["category"] == "KULLANICI"


# --- 120/60: Python sabiti ile SQL kısıtı senkron kalmalı --------------------
@pytest.mark.parametrize(
    "sql_file",
    ["db/01_reset_ve_kurulum.sql", "db/08_align_persisted_with_current_rule.sql"],
)
def test_db_constraint_matches_python_thresholds(sql_file):
    """Ayrışırsa DB, canlı motorun başarısız saydığı satıra kategori yazmayı
    reddeder ve classify --refresh yarıda patlar."""
    sql = (ROOT / sql_file).read_text(encoding="utf-8")
    body = re.search(
        r"CONSTRAINT ck_success_has_no_failure CHECK \((.*?)\);", sql, re.S
    ).group(1)
    assert f"duration_sec < {int(CURRENT_DURATION_SEC)}" in body
    assert f"distance_m < {int(CURRENT_DISTANCE_M)}" in body
    assert "duration_sec IS NOT NULL" in body and "distance_m IS NOT NULL" in body


def test_write_path_never_filters_on_outcome_alone():
    """Regresyon: tek başına outcome filtresi dönerse eşik altındaki sürüşler
    kalıcı tablolardan yine düşer."""
    for name in ("classify.py", "assess.py"):
        src = (ROOT / "src" / "binbin" / "data" / name).read_text(encoding="utf-8")
        assert "current_rule_sql(" in src
