"""Backend sağlamlaştırma testleri — DB'siz (bağlantı kurulmaz).

Kapsam: SQL güvenlik yardımcıları (scope clause bind-param'lı mı, partition adı
guard'ı enjeksiyonu reddediyor mu) ve config sağlamlığı (DATABASE_URL yoksa
anlaşılır hata, engine tekil/cached).
"""

import pytest

from binbin.data.ingest import _PARTITION_NAME_RE
from binbin.data.engine import _CITY_ALIAS, _database_url, _scope_clause, get_engine
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
