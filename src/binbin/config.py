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
# ÖNKOŞUL: db/01_setup.sql çalıştırılmış olmalı.
CLASSIFIER_VERSION = "v5"
ASSESSOR_VERSION = "v5"

# Sinyal penceresi: [start_time, end_time + N dk]. Sürüş ÖNCESİ olaylar kasıtlı
# dışlanır — geçmiş bir arıza bu sürüşü açıklamaz.
FIELD_SIGNAL_WINDOW_POST_MIN = 10

# Komşu sürüş kanıtı pencereleri: araç ekseni 72 sa, kullanıcı ekseni 1 sa.
NEIGHBOR_VEHICLE_WINDOW_MIN = 4320
NEIGHBOR_USER_WINDOW_MIN = 60

# Bakım kanıtı penceresi: sürüş bitişinden sonraki 24 sa (lift 4,94x; 72sa'te 3,53x'e düşer).
MAINTENANCE_WINDOW_POST_MIN = 1440


# analyze uzun sürer, bağlantı arada ölebilir: ping'le ve eskiyeni yenile.
DB_POOL_RECYCLE_SEC = 1800
DB_POOL_PRE_PING = True

# Oturum work_mem'i. Varsayılan 4MB'de timeline'ın pencere sıralamaları diske
# taşıyordu (71MB + 49MB + 10MB "external merge"). ÖLÇÜLDÜ: 8,3 → 7,1 sn.
# DİKKAT: bu ayar TEK BAŞINA işe yaramaz — daha önce denendiğinde fark etmemişti,
# çünkü asıl darboğaz plan şekliydi (kapsam predikatı `city` üzerindeydi ve
# planlayıcı satır sayısını 79x şaşırıp nested loop seçiyordu). Önce o düzeldi.
# 128MB en büyük dökümü (71MB) karşılar; daha yükseği ölçümde fark etmiyor.
DB_WORK_MEM = "128MB"

# Eşzamanlı iki ingest'in paylaşımlı stg_rental_raw'ı ezmesini engeller.
INGEST_LOCK_KEY = 918273
