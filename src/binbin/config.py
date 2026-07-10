"""Projenin Scope (Kapsam) Konfigürasyonları — TEK KAYNAK (Single Source of Truth).

Burada koda hardcode if-else yazmak yerine (örneğin `if city == "İstanbul"`), 
projeyi konfigürasyon üzerinden yönetiyoruz. Yarın projeye Bursa'yı da katmak istersek 
koda hiç dokunmadan sadece `DEFAULT_SCOPE` tuple'ını güncellememiz yetecek. Çok daha clean.

Not: `Scope` dışarıdan (CLI/CSV) gelen string adları tutar. Analiz katmanı (core) 
ise id-tabanlı çalışır. Bu çeviriyi Repository katmanında yapıyoruz.
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


# İstanbul iki idari bölgeye ayrılır (Avrupa / Anadolu).
# TODO: Eğer yeni bir şehir/ülke açılışı olursa buraya eklemeyi unutmayın.
DEFAULT_SCOPE = Scope(
    countries=("Türkiye",),
    cities=("İstanbul Avrupa", "İstanbul Anadolu"),
)

# Kısıtlamasız scope (--all flag'i gelirse bu kullanılır).
UNRESTRICTED_SCOPE = Scope()

# Veritabanına basarken damgaladığımız classifier/assessor versiyonları.
# İleride algoritma değişirse "v2" yapıp eski veriyi ayırt edebiliriz.
CLASSIFIER_VERSION = "v1"
ASSESSOR_VERSION = "v1"
