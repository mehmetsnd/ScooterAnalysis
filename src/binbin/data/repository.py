"""Repository Arayüzü (Protocol) — Dependency Inversion (DIP) kuralını burada uyguluyoruz.

Core (Analiz) katmanı doğrudan veritabanına bağlanmaz, sadece bu Protocol'e (arayüze) güvenir.
Yarın Postgres yerine MongoDB veya Mock bir database gelse bile Core katmanındaki tek satır kod değişmez.

Not: Canlı analiz yolu (`analysis_timeline`) araç/zaman sıralı sürüşleri DB'den
STREAM eder; iki senaryolu (Mevcut/Özel Kural) hesap Python tarafında, saf core
fonksiyonları (classify_ride/assess_ride) yeniden kullanılarak yapılır — böylece
classify/assess mantığı SQL'de tekrarlanmaz. `ops_cost_rows` küçük maliyet tablosunu
hazır list[dict] olarak döner.
"""

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

from binbin.config import Scope


@dataclass(frozen=True)
class AnalysisScope:
    """Analiz kapsamı — id listeleri. None = hepsi (filtre yok)."""

    country_ids: Optional[list[int]] = None
    city_ids: Optional[list[int]] = None


class RideCommandRepository(Protocol):
    """Veritabanına yazma/güncelleme yapan arayüz (CQRS - Command)."""

    def classify_all(self, scope: Optional[AnalysisScope], batch_size: int = 10000, version: str = ...) -> dict:
        """Sınıflandırılmamış başarısız sürüşleri sınıflandırıp geri yazar."""
        raise NotImplementedError

    def assess_all(self, scope: Optional[AnalysisScope], version: str = ..., refresh: bool = False) -> dict:
        """Başarısız sürüşleri değerlendirir ve false_fault_assessment tablosunu doldurur."""
        raise NotImplementedError


class RideQueryRepository(Protocol):
    """Analiz katmanının ihtiyaç duyduğu okuma arayüzü (CQRS - Query).

    `analysis_timeline` iki senaryolu analizin ham girdisidir (araç/zaman sıralı,
    `city.is_test = false`, yerel saat `AT TIME ZONE country.timezone` ile). `resolve_scope`
    kapsam adlarını id'ye çözer, `ops_cost_rows` maliyet modelini okur. Tek somut
    implementasyon: PostgresRideRepository.
    """

    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        """Ülke/şehir adlarını id listelerine çözer (is_test şehirler hariç)."""
        raise NotImplementedError

    def analysis_timeline(self, scope: AnalysisScope) -> Iterable[dict]:
        """İki senaryolu analiz için araç/zaman sıralı sürüş timeline'ı."""
        raise NotImplementedError

    def ops_cost_rows(self, scope: AnalysisScope) -> list[dict]:
        """ops_cost_model satırları (boşsa []). Boşsa analiz TL raporlamaz."""
        raise NotImplementedError

    def out_of_content_counts(self, scope: AnalysisScope) -> dict:
        """Analiz dışı (out-of-content) sürüş sayıları: total + mesafe/süre kırılımı."""
        raise NotImplementedError
