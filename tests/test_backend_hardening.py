"""Backend sağlamlaştırma testleri — DB'siz (bağlantı kurulmaz).

Kapsam: SQL güvenlik yardımcıları (scope clause bind-param'lı mı, partition adı
guard'ı enjeksiyonu reddediyor mu) ve config sağlamlığı (DATABASE_URL yoksa
anlaşılır hata, engine tekil/cached).
"""

import pytest

from binbin.data.ingest import (
    _FLEET_STATUS_EVENT_PARTITION_NAME_RE,
    _PARTITION_NAME_RE,
    ensure_month_partitions,
)
from binbin.data.engine import (
    _CITY_ALIAS,
    _database_url,
    _scope_clause,
    field_signal_join_sql,
    get_engine,
)
from binbin.data.repository import AnalysisScope


# --- _scope_clause: değerler daima bind-param, alias sabit literal ----------
def test_scope_clause_none_bos():
    assert _scope_clause(None) == ("", {})


def test_scope_clause_sadece_ulke():
    clause, params = _scope_clause(AnalysisScope(country_ids=[1, 2], city_ids=None))
    assert "ANY(:sc_country_ids)" in clause  # değer bind-param
    assert f"{_CITY_ALIAS}.country_id" in clause  # alias sabit literal 'ci'
    assert params == {"sc_country_ids": [1, 2]}
    assert "sc_city_ids" not in params


def test_scope_clause_sadece_sehir():
    clause, params = _scope_clause(AnalysisScope(country_ids=None, city_ids=[5]))
    assert "ANY(:sc_city_ids)" in clause
    assert params == {"sc_city_ids": [5]}


def test_scope_clause_ulke_ve_sehir():
    clause, params = _scope_clause(AnalysisScope(country_ids=[9], city_ids=[7, 8]))
    assert params == {"sc_country_ids": [9], "sc_city_ids": [7, 8]}
    # Ham değer clause metnine gömülmemeli (yalnız :param placeholder)
    assert "9" not in clause and "[7, 8]" not in clause


# --- Partition adı guard'ı: yalnız ride_YYYY_MM kabul; enjeksiyon reddedilir --
@pytest.mark.parametrize("name", ["ride_2026_06", "ride_2026_12", "ride_2030_01"])
def test_partition_name_gecerli(name):
    assert _PARTITION_NAME_RE.match(name)


@pytest.mark.parametrize(
    "name",
    [
        "ride_2026_6",          # tek haneli ay
        "ride_2026_06; DROP TABLE ride",  # enjeksiyon
        "ride_2026_06 --",
        "rides_2026_06",
        " ride_2026_06",
        "ride_2026_06\n",
    ],
)
def test_partition_name_reddedilir(name):
    assert not _PARTITION_NAME_RE.match(name)


# --- fleet_status_event partition adı guard'ı: aynı sözleşme, farklı önek -----
@pytest.mark.parametrize(
    "name", ["fleet_status_event_2026_06", "fleet_status_event_2030_12"]
)
def test_fleet_status_event_partition_name_gecerli(name):
    assert _FLEET_STATUS_EVENT_PARTITION_NAME_RE.match(name)


@pytest.mark.parametrize(
    "name",
    [
        "fleet_status_event_2026_6",
        "fleet_status_event_2026_06; DROP TABLE fleet_status_event",
        "ride_2026_06",  # yanlış önek, çapraz kabul edilmemeli
    ],
)
def test_fleet_status_event_partition_name_reddedilir(name):
    assert not _FLEET_STATUS_EVENT_PARTITION_NAME_RE.match(name)


# --- ensure_month_partitions: parent_table allowlist'i (bilinmeyen tablo reddedilir) --
def test_ensure_month_partitions_bilinmeyen_parent_reddedilir():
    with pytest.raises(ValueError, match="partition'lı tablo"):
        ensure_month_partitions(
            engine=None,
            parent_table="users",
            partition_name_re=_PARTITION_NAME_RE,
            bounds_sql="SELECT 1",
            bounds_params={},
        )


# --- Sinyal-join penceresi: sonraki sürüşte KESİLİR -------------------------
# Regresyon kilidi: kırpma olmadan, sürüş bittikten sonra tekrar kiralanan aracın
# YENİ sürüşü sırasında düşen arıza olayı ÖNCEKİ sürüşe de atanıyordu (ölçüm:
# 6.423 atamanın %20,1'i). Bu, var olmayan bir kanıtı kategoriye çevirmektir.
def test_field_signal_join_sonraki_suruste_kesilir():
    sql = field_signal_join_sql()
    assert "LEAST(" in sql
    assert "r2.start_time > r.start_time" in sql
    assert "min(r2.start_time)" in sql
    # Üst sınır YARI AÇIK olmalı: olay tam sonraki sürüşün başlangıcındaysa ona aittir.
    assert "e.created_on <" in sql
    assert "BETWEEN" not in sql


def test_field_signal_join_kural_kitabi_etiketini_tasir():
    """TEKNİK ARIZA KIRILIMI etiketleri DB'den akar; core 58 kodu hardcode etmez."""
    assert "fsr.description    AS field_signal_desc" in field_signal_join_sql()


# --- Aday guard'ı: LATERAL'i başarısız-olabilecek sürüşlerle sınırlar -------
# Regresyon kilidi: assess_all guard'sız 51,9 sn sürüyordu (LATERAL 1,03M satırın
# TAMAMINDA çalışıyordu, oysa field_fault yalnız BASARISIZ_HARD satırlarında
# okunuyordu). "outcome" guard'ı bunu 9,1 sn'ye indirdi — sonuçlar DB'de birebir
# aynı doğrulandı (52.755 satır, byte-eşit).
def test_field_signal_join_guardsiz_varsayilan():
    sql = field_signal_join_sql()
    assert "BASARISIZ_HARD" not in sql
    assert ":fsig_max_dur" not in sql


def test_field_signal_join_outcome_guard():
    sql = field_signal_join_sql(candidate_guard="outcome")
    assert "r.outcome = 'BASARISIZ_HARD' AND " in sql
    assert ":fsig_max_dur" not in sql  # eşik bind-param'ı gerekmez


def test_field_signal_join_thresholds_guard():
    sql = field_signal_join_sql(candidate_guard="thresholds")
    assert "r.outcome = 'BASARISIZ_HARD'" in sql
    assert ":fsig_max_dur" in sql and ":fsig_max_dist" in sql


def test_field_signal_join_bilinmeyen_guard_reddedilir():
    with pytest.raises(ValueError, match="Bilinmeyen candidate_guard"):
        field_signal_join_sql(candidate_guard="typo")


# --- Config: DATABASE_URL yoksa anlaşılır RuntimeError ----------------------
def test_database_url_eksikse_hata(monkeypatch):
    monkeypatch.setattr("binbin.data.engine.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _database_url()


# --- Engine tekil (cached): iki çağrı aynı nesne ----------------------------
def test_get_engine_tekil(monkeypatch):
    monkeypatch.setattr("binbin.data.engine.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    get_engine.cache_clear()
    try:
        e1 = get_engine()  # lazy — bağlantı kurmaz
        e2 = get_engine()
        assert e1 is e2
    finally:
        get_engine.cache_clear()  # başka testleri kirletme
