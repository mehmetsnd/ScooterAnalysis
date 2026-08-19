"""Veri-only reset betiklerinin güvenlik sözleşmesi."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_operational_reset_does_not_drop_schema_objects():
    sql = (ROOT / "db" / "reset" / "02_reset_operational_data.sql").read_text(encoding="utf-8")
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
    sql = (ROOT / "db" / "01_setup.sql").read_text(encoding="utf-8")
    for table in ("fleet_status_code", "fleet_status_reason", "fleet_status_event"):
        assert f"CREATE TABLE {table}" in sql
    assert "is_fault_signal" in sql
    assert "verified" in sql
    assert "ck_fault_signal_needs_category" in sql
    assert "fleet_status_event_default" in sql
    assert "PARTITION BY RANGE (created_on)" in sql


def test_vehicle_status_seed_marks_low_battery_as_non_signal():
    """Kural kitabı seed'i ölçümü geçemeyen kodla (8 'Batarya az') başlamamalı."""
    sql = (ROOT / "db" / "01_setup.sql").read_text(encoding="utf-8")
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


def test_install_is_aligned_with_the_current_rule():
    """Kalıcı tabloların kısıt/görünümleri Mevcut Kural'ı tanımalı; aksi hâlde DB,
    `analyze`'ın başarısız saydığı 13.210 sürüşü göremez."""
    sql = (ROOT / "db" / "01_setup.sql").read_text(encoding="utf-8")
    assert "duration_sec < 120" in sql
    assert "WHERE r.outcome = 'BASARISIZ_HARD' AND ci.is_test = false" not in sql


def test_install_is_a_single_file_with_no_leftover_migrations():
    """Kurulum TEK dosyadır (db/01_setup.sql); ayrı migration dosyası açılmaz.
    Reset betikleri kurulumun parçası değildir, db/reset/ altında yaşar."""
    install = sorted(p.name for p in (ROOT / "db").glob("*.sql"))
    assert install == ["01_setup.sql"]
    resets = sorted(p.name for p in (ROOT / "db" / "reset").glob("*.sql"))
    assert resets == ["01_pre_data_reset_check.sql", "02_reset_operational_data.sql", "03_post_data_reset_check.sql"]


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


def test_neighbor_signal_is_shared_by_read_and_write_paths():
    """Komşu-sinyal mantığı tek yerde; kopyalanırsa analyze ile classify ayrışır."""
    for name in ("queries.py", "classify.py"):
        source = (ROOT / "src" / "binbin" / "data" / name).read_text(encoding="utf-8")
        assert "neighbor_signal_sql(" in source, name
        assert "neighbor_base_sql(" in source, name
    assess = (ROOT / "src" / "binbin" / "data" / "assess.py").read_text(encoding="utf-8")
    assert "next_user_ref" in assess


def test_new_categories_exist_in_install_ddl():
    """Python enum'u ile DB tipi ayrışırsa classify --refresh CheckViolation ile patlar."""
    sql = (ROOT / "db" / "01_setup.sql").read_text(encoding="utf-8")
    for value in ("ARAC_TARAFI", "KULLANICI_TARAFI", "NEIGHBOR_RIDE"):
        assert f"'{value}'" in sql, value


def test_every_category_has_a_report_label():
    """Etiketi olmayan kategori raporda ham enum adıyla görünür (sessiz çirkinlik)."""
    from binbin.core.scenario_analysis import KANIT_YOK
    from binbin.domain.enums import FailureCategory
    from binbin.reporting.format import CAUSE_LABELS

    for category in FailureCategory:
        assert category.value in CAUSE_LABELS, category.value
    assert KANIT_YOK in CAUSE_LABELS


def test_every_ingest_target_has_period_and_analyze_entries():
    """Yeni kaynak eklenince bu iki allowlist unutulursa ingest çağrı anında KeyError verir."""
    from binbin.data.ingest import _ANALYZE_AFTER_LOAD, _LOAD_PERIOD_SOURCE

    targets = {"ride", "fleet_status_event", "maintenance_event", "ride_geo"}
    assert targets <= set(_LOAD_PERIOD_SOURCE)
    assert targets <= set(_ANALYZE_AFTER_LOAD)
