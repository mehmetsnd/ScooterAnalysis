"""Repository Arayüzü (Protocol) — Dependency Inversion (DIP) kuralını burada uyguluyoruz.

Core (Analiz) katmanı doğrudan veritabanına bağlanmaz, sadece bu Protocol'e (arayüze) güvenir.
Yarın Postgres yerine MongoDB veya Mock bir database gelse bile Core katmanındaki tek satır kod değişmez.

Not: Ağır matematiksel agregasyonları (gruplama vb.) DB katmanına SQL ile yaptırıyoruz.
Bu arayüzden dönen data, memory'i şişirmeyen hazır list[dict] objeleridir.
"""

from dataclasses import dataclass
from typing import Optional, Protocol

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
    """Analiz katmanının ihtiyaç duyduğu, DB'de agregelenmiş veri sağlayan arayüz (CQRS - Query).

    Her sorgu `city.is_test = false` filtreler; saatlik kırılımlar yerel saate
    (`AT TIME ZONE country.timezone`) çevrilir. Tek somut implementasyon:
    PostgresRideRepository (test tarafı inline `_FakeRepo` duck-typing kullanır).
    """

    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        """Ülke/şehir adlarını id listelerine çözer (is_test şehirler hariç)."""
        raise NotImplementedError

    def failure_category_counts(self, scope: AnalysisScope) -> list[dict]:
        """Başarısız sürüşlerin failure_category kırılımı: [{category, count}] (None = sinyalsiz)."""
        raise NotImplementedError

    def failure_criteria_counts(
        self, scope: AnalysisScope, dur_thr: float = 120, dist_thr: float = 60
    ) -> dict:
        """Eşik (duration<dur_thr & distance<dist_thr) kuralının outcome ile uyumu +
        iki kuyruk sayacı. Eşikler parametrik (default 120sn/60m); what-if için değişir."""
        raise NotImplementedError

    def vehicle_failure_counts(self, scope: AnalysisScope, min_failures: int) -> list[dict]:
        """En çok başarısızlık üreten araçlar: [{vehicle_id, external_code, failures}]."""
        raise NotImplementedError

    def control_group_stats(self, scope: AnalysisScope) -> list[dict]:
        """Üç grubun healthy_proof sayıları: [{group, total, healthy}]."""
        raise NotImplementedError

    def false_fault_counts(self, scope: AnalysisScope) -> list[dict]:
        """verdict×hypothesis kırılımı: [{verdict, hypothesis, events, vehicles, wasted}]."""
        raise NotImplementedError

    def subregion_stats(self, scope: AnalysisScope, min_rides: int) -> list[dict]:
        """Alt bölge (şehir, kod) çifti bazında başarısızlık + sahte-alarm yoğunluğu."""
        raise NotImplementedError

    def hour_region_counts(self, scope: AnalysisScope) -> list[dict]:
        """Yerel saat × şehir kırılımında toplam/başarısız sürüş: [{city, hour, total, failed}]."""
        raise NotImplementedError

    def ops_cost_rows(self, scope: AnalysisScope) -> list[dict]:
        """ops_cost_model satırları (boşsa []). Boşsa analiz TL raporlamaz."""
        raise NotImplementedError
