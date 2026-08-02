"""PostgreSQL repository — Protocol implementasyonu, ince delege katmanı. İş mantığı
burada değil: okuma → `queries.py`, sınıflandırma → `classify.py`, değerlendirme →
`assess.py`, bağlantı → `engine.py`. Tablo tanımları Python'da TUTULMAZ; şemanın tek
doğru kaynağı `db/*.sql`.
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

    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        return queries.resolve_scope(self.engine, scope)

    def analysis_timeline(
        self,
        scope: Optional[AnalysisScope],
        candidate_bounds: Optional[tuple[float, float]] = None,
    ) -> Iterable[dict]:
        return queries.analysis_timeline(self.engine, scope, candidate_bounds)

    def ops_cost_rows(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.ops_cost_rows(self.engine, scope)

    def out_of_content_counts(self, scope: Optional[AnalysisScope]) -> dict:
        return queries.out_of_content_counts(self.engine, scope)

    def signal_discrimination_rows(self, scope: Optional[AnalysisScope]) -> list[dict]:
        return queries.signal_discrimination_rows(self.engine, scope)

    def classify_all(
        self,
        scope: Optional[AnalysisScope],
        batch_size: int = 10000,
        version: str = CLASSIFIER_VERSION,
        refresh: bool = False,
    ) -> dict:
        return classify.classify_all(self.engine, scope, batch_size, version, refresh)

    def assess_all(
        self,
        scope: Optional[AnalysisScope],
        version: str = ASSESSOR_VERSION,
        refresh: bool = False,
    ) -> dict:
        return assess.assess_all(self.engine, scope, version, refresh)

    def list_data_loads(self) -> list[dict]:
        return queries.list_data_loads(self.engine)
