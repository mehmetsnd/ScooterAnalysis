"""PostgreSQL repository — Protocol implementasyonu (ince delege katmanı).

Bu sınıf `RideQueryRepository` + `RideCommandRepository` Protocol'lerini karşılar
ama iş mantığı burada DEĞİLDİR: her metot, kaynağa göre ayrılmış serbest
fonksiyonlara delege eder:
  * okuma sorguları      → `queries.py`
  * sınıflandırma (yaz)  → `classify.py`
  * değerlendirme (yaz)  → `assess.py`
  * bağlantı/plumbing    → `engine.py`
  * tablo tanımları      → `schema.py`

Böylece "hangi dosyada hangi fonksiyon" nettir ve tek dosya şişmez. Public API
(metot adları/imzaları) değişmemiştir → çağıranlar (core/analysis, cli) etkilenmez.
"""

from typing import Optional

from sqlalchemy import Engine

from binbin.config import ASSESSOR_VERSION, CLASSIFIER_VERSION, Scope
from binbin.data import assess, classify, queries
from binbin.data.engine import get_engine
from binbin.data.repository import AnalysisScope


class PostgresRideRepository:
    """Repository Protocol'lerinin Postgres implementasyonu (delege eder)."""

    def __init__(self, engine: Optional[Engine] = None) -> None:
        self.engine = engine if engine is not None else get_engine()

    # ------------------------------------------------------------------ scope
    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        return queries.resolve_scope(self.engine, scope)

    # -------------------------------------------------------------- analysis
    def failure_category_counts(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.failure_category_counts(self.engine, scope)

    def failure_criteria_counts(
        self,
        scope: Optional[AnalysisScope],
        dur_thr: float = 120,
        dist_thr: float = 60,
    ) -> dict:
        return queries.failure_criteria_counts(self.engine, scope, dur_thr, dist_thr)

    def vehicle_failure_counts(
        self, scope: Optional[AnalysisScope], min_failures: int
    ) -> list[dict]:
        return queries.vehicle_failure_counts(self.engine, scope, min_failures)

    def control_group_stats(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.control_group_stats(self.engine, scope)

    def false_fault_counts(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.false_fault_counts(self.engine, scope)

    def subregion_stats(
        self, scope: Optional[AnalysisScope], min_rides: int
    ) -> list[dict]:
        return queries.subregion_stats(self.engine, scope, min_rides)

    def hour_region_counts(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.hour_region_counts(self.engine, scope)

    def ops_cost_rows(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.ops_cost_rows(self.engine, scope)

    # ---------------------------------------------------------- classify (yaz)
    def classify_all(
        self,
        scope: Optional[AnalysisScope],
        batch_size: int = 10000,
        version: str = CLASSIFIER_VERSION,
    ) -> dict:
        return classify.classify_all(self.engine, scope, batch_size, version)

    # ------------------------------------------------------------ assess (yaz)
    def assess_all(
        self,
        scope: Optional[AnalysisScope],
        version: str = ASSESSOR_VERSION,
        refresh: bool = False,
    ) -> dict:
        return assess.assess_all(self.engine, scope, version, refresh)

    # -------------------------------------------------------------- data_load
    def list_data_loads(self) -> list[dict]:
        return queries.list_data_loads(self.engine)
