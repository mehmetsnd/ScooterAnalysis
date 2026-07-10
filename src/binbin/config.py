"""Kapsam konfigürasyonu — TEK KAYNAK.

Bu adımda işlenen veri yalnızca Türkiye + İstanbul'dur (406.814 satır). Ama bu
kapsam bir *konfigürasyon değeridir*, koda gömülü bir dallanma (`if city ==
"İstanbul"`) DEĞİLDİR. Yarın Bursa eklenecekse yalnızca `DEFAULT_SCOPE` tuple'ları
değişir; hiçbir fonksiyon dokunulmaz.

`Scope` ülke/şehir ADLARINI taşır (ingest ve CLI girdisi için). Analiz katmanı
id-tabanlı `AnalysisScope` (data/repository.py) kullanır; CLI/repo adları id'ye
çözer.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    """İşlenecek kapsamın ülke/şehir adları. Boş tuple = filtre yok (bu alanda)."""

    countries: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()

    @property
    def is_unrestricted(self) -> bool:
        """Hiç filtre yoksa (--all semantiği) True."""
        return not self.countries and not self.cities


# İstanbul iki idari bölgeye ayrılır (CSV region_name: 'İstanbul Avrupa' /
# 'İstanbul Anadolu'). Ülke adı CSV country_name ve country.name ile ('Türkiye')
# birebir eşleşir.
DEFAULT_SCOPE = Scope(
    countries=("Türkiye",),
    cities=("İstanbul Avrupa", "İstanbul Anadolu"),
)

# --all için: hiçbir filtre uygulanmaz (CSV/DB'nin tamamı).
UNRESTRICTED_SCOPE = Scope()

# Bu adımda üretilen sınıflandırıcı/değerlendirici sürümü (geri yazımda damgalanır).
CLASSIFIER_VERSION = "v1"
ASSESSOR_VERSION = "v1"
