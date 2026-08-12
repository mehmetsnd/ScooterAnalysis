"""Scope (kapsam) konfigürasyonu — tek kaynak (SSoT).

Lokasyon filtreleri koda hardcode edilmez; yeni şehir/ülke eklemek DEFAULT_SCOPE
tuple'ını güncellemekle sınırlıdır. Scope dışarıdan (CLI/CSV) string ad tutar; core
id-tabanlı çalışır — bu çeviri Repository katmanında yapılır.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    """İşlenecek kapsam. Tuple'lar boşsa filtreleme yapılmaz (--all mantığı)."""

    countries: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()

    @property
    def is_unrestricted(self) -> bool:
        """Kapsam filtresi yoksa True döner (tüm data çekilir)."""
        return not self.countries and not self.cities


# İstanbul iki idari bölgeye ayrılır (Avrupa / Anadolu). Yeni lokasyon → buraya eklenir.
DEFAULT_SCOPE = Scope(
    countries=("Türkiye",),
    cities=("İstanbul Avrupa", "İstanbul Anadolu"),
)

UNRESTRICTED_SCOPE = Scope()

# Algoritma değişince yükseltilir; eski veri damgasıyla ayrışır.
# v4 (2026-08-12): kalıcı tablolar canlı motorla hizalandı — classify_all/assess_all
# artık Mevcut Kural'ı kullanıyor (eskiden yalnız outcome'a bakıyorlardı, eşik
# altındaki 13.210 BASARILI sürüş kalıcı tablolarda yoktu). assess_all ayrıca
# out-of-content'i dışlıyor ve sonraki sürüşü senaryo statüsüne göre değerlendiriyor.
# ÖNKOŞUL: db/08_align_persisted_with_current_rule.sql çalıştırılmış olmalı.
CLASSIFIER_VERSION = "v4"
ASSESSOR_VERSION = "v4"

# Sinyal penceresi: [start_time, end_time + N dk]. Sürüş ÖNCESİ olaylar kasıtlı
# dışlanır — geçmiş bir arıza bu sürüşü açıklamaz.
FIELD_SIGNAL_WINDOW_POST_MIN = 10


# analyze uzun sürer, bağlantı arada ölebilir: ping'le ve eskiyeni yenile.
DB_POOL_RECYCLE_SEC = 1800
DB_POOL_PRE_PING = True

# Eşzamanlı iki ingest'in paylaşımlı stg_rental_raw'ı ezmesini engeller.
INGEST_LOCK_KEY = 918273
