"""Backend sağlamlaştırma testleri — DB'siz (bağlantı kurulmaz).

Kapsam: SQL güvenlik yardımcıları (scope clause bind-param'lı mı, partition adı
guard'ı enjeksiyonu reddediyor mu), config sağlamlığı (DATABASE_URL yoksa anlaşılır
hata, engine tekil/cached) ve mimari sözleşme (Repository Protocol uyumu, DIP).
"""

import pytest

from binbin.core.scenario_analysis import CURRENT_DISTANCE_M, CURRENT_DURATION_SEC
from binbin.data.ingest import (
    _FLEET_STATUS_EVENT_PARTITION_NAME_RE,
    _PARTITION_NAME_RE,
    close_data_load_success,
    ensure_month_partitions,
)
from binbin.data.engine import (
    _database_url,
    _scope_clause,
    current_rule_params,
    current_rule_sql,
    field_signal_join_sql,
    get_engine,
)
from binbin.data.repository import AnalysisScope


# --- _scope_clause: değerler daima bind-param, alias sabit literal ----------
def test_scope_clause_none_bos():
    assert _scope_clause(None) == ("", {})


def test_scope_clause_filtresiz_scope_bos_doner():
    assert _scope_clause(AnalysisScope(None)) == ("", {})


def test_scope_clause_ride_tarafinda_kurulur():
    """Predikat `city` üzerinde DEĞİL `ride.city_id` üzerinde olmalı: planlayıcının
    satır tahmini buna bağlı (bkz. AnalysisScope docstring'i)."""
    clause, params = _scope_clause(AnalysisScope(city_ids=[7, 8]))
    assert "r.city_id = ANY(:sc_city_ids)" in clause  # alias sabit literal
    assert "ci." not in clause
    assert params == {"sc_city_ids": [7, 8]}
    # Ham değer clause metnine gömülmemeli (yalnız :param placeholder)
    assert "[7, 8]" not in clause


@pytest.mark.parametrize("alias", ["r", "ride", "w"])
def test_scope_clause_izin_verilen_aliaslar(alias):
    clause, _ = _scope_clause(AnalysisScope(city_ids=[5]), alias=alias)
    assert f"{alias}.city_id = ANY(:sc_city_ids)" in clause


def test_scope_clause_bilinmeyen_alias_reddedilir():
    """Alias allowlist'i SQL güvenlik sözleşmesinin parçası — interpolasyona
    yalnız sabit literal akar."""
    with pytest.raises(ValueError):
        _scope_clause(AnalysisScope(city_ids=[5]), alias="r; DROP TABLE ride--")


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


# Regresyon kilidi: kırpma olmadan, tekrar kiralanan aracın YENİ sürüşündeki arıza olayı
# ÖNCEKİ sürüşe de atanıyordu (6.423 atamanın %20,1'i) — var olmayan kanıtı kategoriye çevirir.
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


# Regresyon kilidi: guard'sız assess_all 51,9 sn (LATERAL 1,03M satırın tamamında çalışıyordu);
# "outcome" guard'ı 9,1 sn'ye indirdi, sonuç DB'de birebir aynı (52.755 satır, byte-eşit).
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


# --- current_rule_sql: Mevcut Kural'ın SQL karşılığı -------------------------
def test_current_rule_sql_inlines_thresholds_as_literals():
    """Bind-param olsaydı `idx_ride_unclassified` kısmi indeksi ölürdü: planlayıcı
    implikasyonu kanıtlayamıyor (ölçüldü: 17.653 → 53.273 maliyet, seq scan)."""
    sql = current_rule_sql()
    assert ":cur_max_dur" not in sql and ":cur_max_dist" not in sql
    assert f"< {CURRENT_DURATION_SEC}" in sql and f"< {CURRENT_DISTANCE_M}" in sql


def test_current_rule_sql_treats_unmeasured_ride_as_not_failed():
    """Canlı motor ölçüm eksikse SUCCESS der; guard olmadan NULL sessizce ayrışır."""
    sql = current_rule_sql()
    assert "duration_sec IS NOT NULL" in sql
    assert "distance_m IS NOT NULL" in sql


def test_current_rule_sql_includes_source_failure():
    assert "outcome = 'BASARISIZ_HARD'" in current_rule_sql()


@pytest.mark.parametrize("alias", ["r", "ride", "seq"])
def test_current_rule_sql_accepts_allowlisted_alias(alias):
    assert f"{alias}.outcome" in current_rule_sql(alias)


def test_current_rule_sql_rejects_unknown_alias():
    with pytest.raises(ValueError, match="Bilinmeyen alias"):
        current_rule_sql("r; DROP TABLE ride")


def test_current_rule_params_match_core_constants():
    """Guard ile WHERE ayrışırsa guard tam eşleşme olmaktan çıkar."""
    p = current_rule_params()
    assert p["fsig_max_dur"] == CURRENT_DURATION_SEC
    assert p["fsig_max_dist"] == CURRENT_DISTANCE_M


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


# --- close_data_load_success: tablo adı allowlist'ten, değerler bind-param ---
class _FakeConn:
    """execute() çağrılarını kaydeder; SQL metni ve parametreleri denetlenebilsin."""

    def __init__(self, calls):
        self.calls = calls

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return self

    def one(self):
        return type("Row", (), {"lo": "2026-06-01", "hi": "2026-06-30"})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.calls = []

    def begin(self):
        return _FakeConn(self.calls)


class _FakeReport:
    rows_read = 10
    rows_inserted = 8
    rows_skipped = 2
    warnings: list[str] = []


@pytest.mark.parametrize(
    "table,time_col", [("ride", "start_time"), ("fleet_status_event", "created_on")]
)
def test_close_data_load_success_dogru_zaman_kolonu(table, time_col):
    engine = _FakeEngine()
    close_data_load_success(engine, 7, _FakeReport(), table=table)
    period_sql, period_params = engine.calls[0]
    assert f"FROM {table} " in period_sql
    assert f"min({time_col})" in period_sql
    assert period_params == {"id": 7}  # değer bind-param


def test_close_data_load_success_bilinmeyen_tabloyu_reddeder():
    """Identifier bind EDİLEMEZ; allowlist dışı ad SQL'e interpolate edilmemeli."""
    with pytest.raises(KeyError):
        close_data_load_success(_FakeEngine(), 1, _FakeReport(), table="ride; DROP TABLE ride")


def test_close_data_load_success_eksik_rows_flagged_sifir_yazar():
    """Durum defteri raporunda rows_flagged alanı yok — 0 yazılmalı, patlamamalı."""
    engine = _FakeEngine()
    close_data_load_success(engine, 3, _FakeReport(), table="fleet_status_event")
    _, update_params = engine.calls[1]
    assert update_params["flag"] == 0


# --- Mimari sözleşme: Protocol ile implementasyon ayrışmasın ---------------
# Protocol'ler hiçbir yerde type-hint olarak kullanılmıyordu; bir metot yeniden
# adlandırılsa arayüz sessizce yanlış şeyi belgelemeye devam ederdi.
from binbin.data.postgres_repo import PostgresRideRepository  # noqa: E402
from binbin.data.repository import (  # noqa: E402
    RideCommandRepository,
    RideQueryRepository,
    RideRepository,
)


@pytest.mark.parametrize(
    "protocol", [RideQueryRepository, RideCommandRepository, RideRepository]
)
def test_postgres_repo_protokolu_karsilar(protocol):
    assert issubclass(PostgresRideRepository, protocol), (
        f"{protocol.__name__} sözleşmesindeki metotlardan biri "
        "PostgresRideRepository'de yok veya adı değişmiş."
    )


def test_protokol_eksik_metotu_yakalar():
    """Kontrolün gerçekten bir şey ölçtüğünü kanıtlar (tautolojik test değil)."""

    class Eksik:
        def resolve_scope(self, scope):
            ...

    assert not issubclass(Eksik, RideQueryRepository)


def test_komutlar_somut_repoyu_dogrudan_kurmaz():
    """DIP kilidi: veri kaynağı YALNIZ `_repository()` composition root'unda seçilir.

    Komutlar `PostgresRideRepository`'yi kendi içinde kurarsa kaynağı değiştirmek
    dört yeri birden düzeltmeyi gerektirir ve Protocol yalnız süs olur.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("src/binbin/cli/main.py").read_text(encoding="utf-8"))
    users = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(
            isinstance(n, ast.Name) and n.id == "PostgresRideRepository"
            or isinstance(n, ast.alias) and n.name == "PostgresRideRepository"
            for n in ast.walk(fn)
        )
    }
    assert users == {"_repository"}, f"Somut repoyu kuran fonksiyonlar: {sorted(users)}"
