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

# Algoritma değişince "v2" yapılır; eski veri damgasıyla ayrışır.
# v2 (2026-08-05): kelime kural kitabı ölçümle revize edildi — `normalize()` artık
# kesme işaretini siler, ters-korelasyonlu kelimeler (fren ailesi, kamera, uygulama,
# 6 km…) elendi, ölçümü geçen yazım varyantları eklendi. Hem sınıflandırma hem
# kanıt üretimi değiştiği için İKİ damga da yükseldi (bkz. core/keywords.py).
CLASSIFIER_VERSION = "v3"
ASSESSOR_VERSION = "v3"

# Sinyal penceresi: [start_time, end_time + N dk]. Sürüş ÖNCESİ olaylar kasıtlı
# dışlanır — geçmiş bir arıza bu sürüşü açıklamaz.
FIELD_SIGNAL_WINDOW_POST_MIN = 10


# analyze uzun sürer, bağlantı arada ölebilir: ping'le ve eskiyeni yenile.
DB_POOL_RECYCLE_SEC = 1800
DB_POOL_PRE_PING = True

# Eşzamanlı iki ingest'in paylaşımlı stg_rental_raw'ı ezmesini engeller.
INGEST_LOCK_KEY = 918273
