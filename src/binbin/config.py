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

# Kısıtlamasız scope (--all flag'i gelirse bu kullanılır).
UNRESTRICTED_SCOPE = Scope()

# Classifier/assessor sürüm damgası — algoritma değişince "v2" ile eski veri ayrışır.
CLASSIFIER_VERSION = "v1"
ASSESSOR_VERSION = "v1"


# SQLAlchemy bağlantı havuzu ayarları (TEK yerde). CLI'da tek bağlantı yeter ama
# web'de her istek havuzdan bağlantı alır; havuz olmadan bağlantı tükenir.
#   pool_pre_ping : ölü bağlantıyı kullanmadan önce ping'le (kopmuş TCP'yi ele)
#   pool_recycle  : bu saniyeden eski bağlantıyı yenile (DB idle-timeout'a takılma)
DB_POOL_SIZE = 5
DB_MAX_OVERFLOW = 10
DB_POOL_RECYCLE_SEC = 1800
DB_POOL_PRE_PING = True

# Ingest advisory-lock anahtarı: eşzamanlı iki ingest'in paylaşımlı stg_rental_raw'ı
# ezmesini engeller (pg_advisory_lock). Sabit, projeye özgü bir sayı.
INGEST_LOCK_KEY = 918273
