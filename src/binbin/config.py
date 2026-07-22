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

# Sinyal-join penceresi (dk): başarısız sürüşe REASON_CODE sinyali beslerken,
# aracın [start_time, end_time + bu kadar dk] aralığındaki arıza-sinyalli
# fleet_status_event kayıtlarına bakılır. Sürüş ÖNCESİ olaylar KASITLI dışlanır —
# geçmiş bir arıza bu sürüşün nedenini açıklamaz, yalnız sürüş sırasında/hemen
# sonrasında düşen sinyal sayılır. `data/engine.py:field_signal_join_sql` kullanır.
FIELD_SIGNAL_WINDOW_POST_MIN = 10


# SQLAlchemy bağlantı ayarları (TEK yerde). CLI tek bağlantıyla çalışır; bu ikisi
# yine de gerekli çünkü `analyze` uzun sürer ve bağlantı arada ölebilir:
#   pool_pre_ping : ölü bağlantıyı kullanmadan önce ping'le (kopmuş TCP'yi ele)
#   pool_recycle  : bu saniyeden eski bağlantıyı yenile (DB idle-timeout'a takılma)
# (Havuz boyutu ayarları web planı için konmuştu; SQLAlchemy varsayılanlarıyla
#  birebir aynı oldukları için hiçbir şey yapmıyorlardı → kaldırıldı.)
DB_POOL_RECYCLE_SEC = 1800
DB_POOL_PRE_PING = True

# Ingest advisory-lock anahtarı: eşzamanlı iki ingest'in paylaşımlı stg_rental_raw'ı
# ezmesini engeller (pg_advisory_lock). Sabit, projeye özgü bir sayı.
INGEST_LOCK_KEY = 918273
