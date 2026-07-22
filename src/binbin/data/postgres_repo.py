"""PostgreSQL repository — Protocol implementasyonu (ince delege katmanı).

Bu sınıf `RideQueryRepository` + `RideCommandRepository` Protocol'lerini karşılar
ama iş mantığı burada DEĞİLDİR: her metot, kaynağa göre ayrılmış serbest
fonksiyonlara delege eder:
  * okuma sorguları      → `queries.py`
  * sınıflandırma (yaz)  → `classify.py`
  * değerlendirme (yaz)  → `assess.py`
  * bağlantı/plumbing    → `engine.py`
  * tablo tanımları      → `schema.py`

Böylece "hangi dosyada hangi fonksiyon" nettir ve tek dosya şişmez. Canlı analiz
yolu `analysis_timeline` (stream) + `ops_cost_rows`; yazma yolu classify_all/assess_all.
"""

from typing import Iterable, Optional

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

    def analysis_timeline(self, scope: Optional[AnalysisScope]) -> Iterable[dict]:
        return queries.analysis_timeline(self.engine, scope)

    # ------------------------------------------------------- analysis (okuma)
    def ops_cost_rows(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.ops_cost_rows(self.engine, scope)

    def out_of_content_counts(self, scope: Optional[AnalysisScope]) -> dict:
        return queries.out_of_content_counts(self.engine, scope)

    def signal_discrimination_rows(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.signal_discrimination_rows(self.engine, scope)

    # ---------------------------------------------------------- classify (yaz)
    def classify_all(
        self,
        scope: Optional[AnalysisScope],
        batch_size: int = 10000,
        version: str = CLASSIFIER_VERSION,
        refresh: bool = False,
    ) -> dict:
        return classify.classify_all(self.engine, scope, batch_size, version, refresh)

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
