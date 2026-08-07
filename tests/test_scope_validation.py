"""Kapsam adı doğrulaması — DB'siz.

SESSİZ BOŞ SONUÇ, bu projede en tehlikeli hata sınıfıdır: yazım hatası yapılan bir
şehir adı `resolve_scope`'ta `[]`'e, `_scope_clause`'ta `ANY('{}')`'e dönüşüyor ve
tüm pipeline 0 satırla, exit kodu 0 ile "başarıyla" tamamlanıyordu. `classify
--refresh` bile sessizce no-op oluyordu. Uydurulmuş bir bulgu, gürültülü bir
çökmeden pahalıdır — bu yüzden çözülemeyen ad artık HATA verir.
"""

from pathlib import Path

import pytest

from binbin.config import Scope
from binbin.data.queries import (
    _SCOPE_TABLES,
    _resolve_scope_names,
    _unknown_scope_message,
    resolve_scope,
)
from binbin.data.repository import AnalysisScope, UnknownScopeName

ROOT = Path(__file__).resolve().parents[1]


# --- saf yardımcılar -------------------------------------------------------
def test_resolve_names_hepsi_bulundu():
    ids, missing, test_only = _resolve_scope_names(
        ["İstanbul Avrupa", "Bursa"],
        [
            {"name": "İstanbul Avrupa", "id": 3, "is_test": False},
            {"name": "Bursa", "id": 9, "is_test": False},
        ],
    )
    assert ids == [3, 9]
    assert missing == []
    assert test_only == []


def test_resolve_names_eksik_ad_ayirt_edilir():
    ids, missing, test_only = _resolve_scope_names(
        ["Bursa", "Burrsa"],
        [{"name": "Bursa", "id": 9, "is_test": False}],
    )
    assert ids == [9]
    assert missing == ["Burrsa"]
    assert test_only == []


def test_resolve_names_test_sehri_eksikten_ayrilir():
    """`Test` bölgesi gerçekten var ama analiz dışıdır — 'yazım hatası' denemez."""
    ids, missing, test_only = _resolve_scope_names(
        ["Test"],
        [{"name": "Test", "id": 8, "is_test": True}],
    )
    assert ids == []
    assert missing == []
    assert test_only == ["Test"]


def test_unknown_message_yakin_adi_onerir():
    """En olası hata: Türkçe 'İ' yerine ASCII 'I'. Öneri tam bunu yakalamalı."""
    msg = _unknown_scope_message("şehir", ["Istanbul Avrupa"], ["İstanbul Avrupa", "Bursa"])
    assert "Istanbul Avrupa" in msg
    assert "İstanbul Avrupa" in msg
    assert "Bunu mu demek istediniz" in msg


def test_unknown_message_yakin_ad_yoksa_gecerli_listeyi_verir():
    msg = _unknown_scope_message("ülke", ["Zzz"], ["Türkiye", "Kuzey Makedonya"])
    assert "Zzz" in msg
    assert "Türkiye" in msg and "Kuzey Makedonya" in msg


# --- fake engine (DB'siz) --------------------------------------------------
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeConn:
    def __init__(self, responses, log):
        self._responses = responses
        self.log = log

    def execute(self, statement, params=None):
        self.log.append((" ".join(str(statement).split()), params))
        return _FakeResult(self._responses.pop(0))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.log = []

    def connect(self):
        return _FakeConn(self._responses, self.log)


def test_resolve_scope_hepsi_bulunursa_id_dondurur():
    engine = _FakeEngine([{"name": "Bursa", "id": 9, "is_test": False}])
    scope = resolve_scope(engine, Scope(cities=("Bursa",)))
    assert scope == AnalysisScope(country_ids=None, city_ids=[9])


def test_resolve_scope_bilinmeyen_sehir_hata_verir():
    engine = _FakeEngine(
        [
            {"name": "Bursa", "id": 9, "is_test": False},
            {"name": "İstanbul Avrupa", "id": 3, "is_test": False},
        ]
    )
    with pytest.raises(UnknownScopeName) as exc:
        resolve_scope(engine, Scope(cities=("Burrsa",)))
    assert "Burrsa" in str(exc.value)
    assert "Bursa" in str(exc.value)  # geçerli adlar listelenmeli


def test_resolve_scope_kismi_eslesme_de_hata_verir():
    """İki şehirden biri tutmazsa veri sessizce YARIYA iner — bu da hatadır."""
    engine = _FakeEngine(
        [
            {"name": "Bursa", "id": 9, "is_test": False},
            {"name": "İstanbul Avrupa", "id": 3, "is_test": False},
        ]
    )
    with pytest.raises(UnknownScopeName):
        resolve_scope(engine, Scope(cities=("Bursa", "Burrsa")))


def test_resolve_scope_test_sehri_ayri_mesaj_verir():
    engine = _FakeEngine([{"name": "Test", "id": 8, "is_test": True}])
    with pytest.raises(UnknownScopeName) as exc:
        resolve_scope(engine, Scope(cities=("Test",)))
    assert "is_test" in str(exc.value) or "analiz dışı" in str(exc.value)


def test_resolve_scope_kapsamsizsa_hic_sorgu_calistirmaz():
    """`--all` yolunda DB'ye hiç gidilmemeli (mevcut sözleşme, kilitleniyor)."""
    engine = _FakeEngine()
    assert resolve_scope(engine, Scope()) == AnalysisScope(None, None)
    assert engine.log == []


def test_resolve_scope_is_test_filtresini_sorguya_gommez():
    """Test şehri 'yazım hatası' diye raporlanmasın diye sorgu is_test'e GÖRE
    filtrelemez; ayrımı Python tarafındaki saf yardımcı yapar."""
    engine = _FakeEngine([{"name": "Bursa", "id": 9, "is_test": False}])
    resolve_scope(engine, Scope(cities=("Bursa",)))
    sql, _ = engine.log[0]
    assert "is_test = false" not in sql
    assert "is_test" in sql  # kolon yine de SEÇİLMELİ


# --- SQL ↔ şema sözleşmesi -------------------------------------------------
_DDL = (ROOT / "db" / "01_reset_ve_kurulum.sql").read_text(encoding="utf-8")


def _table_ddl(table: str) -> str:
    start = _DDL.index(f"CREATE TABLE {table} (")
    return _DDL[start : _DDL.index("\n);", start)]


@pytest.mark.parametrize("kind", sorted(_SCOPE_TABLES))
def test_kapsam_sorgusu_yalniz_var_olan_kolonlari_secer(kind):
    """REGRESYON: `country` tablosunda `is_test` kolonu YOKTUR.

    İlk sürümde her iki tablo için de `SELECT ..., is_test` yazılmıştı; `--city`
    yolu çalışıyordu ama `--country` her çağrıda `UndefinedColumn` ile ÇÖKÜYORDU.
    Sahte engine SQL'i şemaya karşı doğrulamadığı için birim testler bunu
    yakalayamamıştı — bu test doğrudan `db/01`'in DDL'ine bakar.
    """
    table, id_col, is_test_expr = _SCOPE_TABLES[kind]
    ddl = _table_ddl(table)
    assert f"{id_col} " in ddl, f"{table}.{id_col} DDL'de yok"
    assert "name " in ddl, f"{table}.name DDL'de yok"
    if is_test_expr != "false":
        assert f"{is_test_expr} " in ddl, (
            f"{table}.{is_test_expr} DDL'de yok — sabit ifade kullanılmalı"
        )


def test_kapsam_tablolari_allowlist_disina_cikmaz():
    """Identifier'lar yalnız bu allowlist'ten gelir (SQL güvenlik sözleşmesi)."""
    assert set(_SCOPE_TABLES) == {"ülke", "şehir"}
    for table, id_col, _ in _SCOPE_TABLES.values():
        assert table.isidentifier() and id_col.isidentifier()
