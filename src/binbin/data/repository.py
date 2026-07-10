"""Repository arayüzü — core yalnızca bu Protocol'e (davranışa) bağımlıdır, somut
implementasyona değil.

Ağır agregasyon SQL'de yapılır: repo, DB'de hesaplanmış satırları (list[dict])
döndürür; core/analysis yalnızca yüzde/oran ekleyip raporlama dict'ine dönüştürür.

`AnalysisScope` id-tabanlıdır (country_ids / city_ids). `None` = filtre yok.
Ülke/şehir ADLARINDAN (config.Scope) id'ye çözme `resolve_scope` ile yapılır —
bu somut repoya özgüdür (Postgres'te DB lookup, mock'ta sabit eşleme).
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
    (`AT TIME ZONE country.timezone`) çevrilir. Somut implementasyonlar:
    PostgresRideRepository, MockRideRepository.
    """

    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        """Ülke/şehir adlarını id listelerine çözer (is_test şehirler hariç)."""
        raise NotImplementedError

    def failure_category_counts(self, scope: AnalysisScope) -> list[dict]:
        """Başarısız sürüşlerin failure_category kırılımı: [{category, count}] (None = sinyalsiz)."""
        raise NotImplementedError

    def failure_criteria_counts(self, scope: AnalysisScope) -> dict:
        """duration<120 & distance<60 kriterinin outcome ile uyumu + iki kuyruk sayacı."""
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
