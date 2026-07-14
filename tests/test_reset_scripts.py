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
        "false_fault_assessment", "feedback", "ride", "data_load", "stg_rental_raw"
    ):
        assert table in sql
    assert "RESTART IDENTITY" in upper


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
