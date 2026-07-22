"""Durum-değişim ingest yolu testleri — DB'siz (bağlantı kurulmaz)."""

import pytest

from binbin.data.ingest import copy_csv_to_staging, detect_csv_kind
from binbin.data.ingest_status import StatusIngestReport


def _touch(dir_, name, content):
    p = dir_ / name
    p.write_text(content, encoding="utf-8")
    return p


# --- detect_csv_kind: başlık satırından tür ayrımı ---------------------------
def test_detect_csv_kind_rides(tmp_path):
    p = _touch(tmp_path, "rides.csv", "rental_id,user_id,vehicle_id\n1,2,3\n")
    assert detect_csv_kind(p) == "rides"


def test_detect_csv_kind_status(tmp_path):
    p = _touch(
        tmp_path, "status.csv",
        "id,vehicle_id,status_id,status_reason_id,previous_status_id\n1,2,3,4,5\n",
    )
    assert detect_csv_kind(p) == "status"


def test_detect_csv_kind_bilinmeyen_baslik_hata(tmp_path):
    p = _touch(tmp_path, "weird.csv", "foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="Bilinmeyen CSV türü"):
        detect_csv_kind(p)


# --- copy_csv_to_staging: staging tablosu allowlist'i (SQL güvenlik sözleşmesi) --
def test_copy_csv_to_staging_bilinmeyen_tablo_reddedilir():
    with pytest.raises(ValueError, match="Bilinmeyen staging tablosu"):
        copy_csv_to_staging(engine=None, csv_path="x.csv", table="ride; DROP TABLE ride")


def test_copy_csv_to_staging_stg_status_raw_allowlistte():
    # engine=None ile bile ValueError öncesi patlamaması (allowlist kontrolü ilk adım)
    # gerçek DB çağrısına ulaşmadan önce ayrı bir hata (AttributeError) beklenir —
    # yani 'stg_status_raw' reddedilmedi, akış DB'ye ulaşmaya çalıştı.
    with pytest.raises(AttributeError):
        copy_csv_to_staging(engine=None, csv_path="x.csv", table="stg_status_raw")


# --- StatusIngestReport: alan sözleşmesi -------------------------------------
def test_status_ingest_report_varsayilanlar():
    report = StatusIngestReport(data_load_id=1, file_name="x.csv")
    assert report.status == "RUNNING"
    assert report.rows_read == 0
    assert report.rows_inserted == 0
    assert report.rows_skipped == 0
    assert report.vehicles_created == 0
    assert report.warnings == []
