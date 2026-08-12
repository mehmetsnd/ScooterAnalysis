"""Veri-only reset betiklerinin güvenlik sözleşmesi."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_operational_reset_does_not_drop_schema_objects():
    sql = (ROOT / "db" / "04_reset_operational_data.sql").read_text(encoding="utf-8")
    executable = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    upper = executable.upper()
    assert "DROP " not in upper
    assert " CASCADE" not in upper
    for table in (
        "false_fault_assessment", "feedback", "ride", "fleet_status_event",
        "data_load", "stg_rental_raw", "stg_status_raw",
    ):
        assert table in sql
    assert "RESTART IDENTITY" in upper


def test_vehicle_status_schema_defines_rule_book_and_partitioning():
    """db/06: kural kitabı DB'de yaşar (verified ile doğrulanabilir), ride ile
    aynı partition/default-boş sözleşmesi korunur."""
    sql = (ROOT / "db" / "06_vehicle_status.sql").read_text(encoding="utf-8")
    for table in ("fleet_status_code", "fleet_status_reason", "fleet_status_event"):
        assert f"CREATE TABLE {table}" in sql
    assert "is_fault_signal" in sql
    assert "verified" in sql
    assert "ck_fault_signal_needs_category" in sql
    assert "fleet_status_event_default" in sql
    assert "PARTITION BY RANGE (created_on)" in sql


def test_signal_rulebook_revision_drops_low_battery_and_respects_verified():
    """db/07: ölçüm sonucu ayırt etmeyen kod (8 'Batarya az') sinyalden çıkar.

    KRİTİK: revizyon `verified=true` satırlara ASLA dokunmamalı — saha ekibinin
    doğruladığı eşleme mühendis önerisini yener (governance sözleşmesi).
    """
    sql = (ROOT / "db" / "07_signal_rulebook_revision.sql").read_text(encoding="utf-8")
    assert "WHERE reason_id = 8 AND NOT verified" in sql
    assert "is_fault_signal = false" in sql
    # Her UPDATE verified guard'ı taşımalı; guard'sız UPDATE governance'ı bozar.
    assert sql.count("NOT verified") >= sql.count("UPDATE fleet_status_reason")


def test_vehicle_status_seed_marks_low_battery_as_non_signal():
    """Temiz kurulum da (db/06) revize edilmiş kural kitabıyla başlamalı."""
    sql = (ROOT / "db" / "06_vehicle_status.sql").read_text(encoding="utf-8")
    low_battery_line = next(
        line for line in sql.splitlines() if "'LowBattery'" in line
    )
    assert "false" in low_battery_line
    assert "'TEKNIK'" not in low_battery_line


def test_write_path_excludes_out_of_content_like_analysis_timeline():
    """Üç tüketici de AYNI kümeyi görmeli. Filtre LEAD'den önce koştuğu için
    eksikliği yalnız satır sayısını değil, "sonraki sürüş"ü de kaydırır."""
    guard = "NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags))"
    for name in ("classify.py", "assess.py", "queries.py"):
        source = (ROOT / "src" / "binbin" / "data" / name).read_text(encoding="utf-8")
        assert guard in source, name


def test_alignment_migration_is_idempotent_and_lock_bounded():
    sql = (ROOT / "db" / "08_align_persisted_with_current_rule.sql").read_text(
        encoding="utf-8"
    )
    assert "SET LOCAL lock_timeout" in sql and "SET LOCAL statement_timeout" in sql
    assert sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1
    assert "pg_constraint" in sql and "DROP CONSTRAINT IF EXISTS" in sql
    assert "-- DOĞRULAMA" in sql
    # Miras kısıt: işlem daima parent `ride` üzerinde, partition'a dokunulmaz.
    assert "ride_2026_" not in sql
    assert "DROP TABLE" not in sql.upper()


def test_alignment_migration_also_fixed_the_clean_install():
    """CLAUDE.md: 07+ migration, ama 01/02/06'daki ilk kurulum aynı anda düzeltilir."""
    d01 = (ROOT / "db" / "01_reset_ve_kurulum.sql").read_text(encoding="utf-8")
    d02 = (ROOT / "db" / "02_false_fault.sql").read_text(encoding="utf-8")
    assert "duration_sec < 120" in d01
    assert "WHERE r.outcome = 'BASARISIZ_HARD' AND ci.is_test = false" not in d02


def test_mongo_distance_is_the_only_ingest_distance_source():
    source = (ROOT / "src" / "binbin" / "data" / "ingest.py").read_text(encoding="utf-8")
    assert "NULLIF(s.mongo_distance_meters, '')::numeric AS dist_raw" in source
    assert "NULLIF(s.distance_meters, '')::numeric AS dist_raw" not in source


def test_out_of_content_flag_replaces_implausible_flags():
    """Out-of-content = mesafe>20km VEYA süre>=6sa; tek OUT_OF_CONTENT flag'i.
    Eski >50km-NULL ve DISTANCE_IMPLAUSIBLE/DURATION_IMPLAUSIBLE mantığı kaldırıldı."""
    source = (ROOT / "src" / "binbin" / "data" / "ingest.py").read_text(encoding="utf-8")
    assert "'OUT_OF_CONTENT'" in source
    assert "src.dist_raw > 20000" in source
    # Saçma mesafe artık NULL'lanmaz, işaretlenip dışlanır.
    assert "> 50000 THEN NULL" not in source
    assert "DISTANCE_IMPLAUSIBLE" not in source
    assert "DURATION_IMPLAUSIBLE" not in source

    queries = (ROOT / "src" / "binbin" / "data" / "queries.py").read_text(encoding="utf-8")
    assert "OUT_OF_CONTENT' = ANY(r.data_quality_flags)" in queries
    assert "def out_of_content_counts" in queries
