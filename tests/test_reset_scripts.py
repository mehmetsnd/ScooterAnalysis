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
