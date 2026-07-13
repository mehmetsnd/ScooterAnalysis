"""Yazma tarafı: başarısız sürüşleri sınıflandırıp `ride` tablosuna geri yazar.

Bu modül data→core kuplajını izole eder: her satır için saf `classify_ride`
çekirdek fonksiyonunu çağırıp sonucu UPDATE ile yazar. Idempotent (classified_at damgası).
"""

from typing import Optional

from sqlalchemy import Engine, text

from binbin.config import CLASSIFIER_VERSION
from binbin.core.classifier import classify_ride
from binbin.data.engine import _scope_clause
from binbin.data.repository import AnalysisScope
from binbin.domain.enums import RideOutcome
from binbin.domain.models import Ride


def classify_all(
    engine: Engine,
    scope: Optional[AnalysisScope],
    batch_size: int = 10000,
    version: str = CLASSIFIER_VERSION,
) -> dict:
    """Sınıflandırılmamış başarısız sürüşleri sınıflandırıp geri yazar.

    Yalnızca outcome='BASARISIZ_HARD' AND failure_category IS NULL AND
    classified_at IS NULL çekilir; sonuç NONE olsa bile classified_at damgalanır
    (idempotent — tekrar çalışınca aynı satırı işlemez).
    """
    clause, sparams = _scope_clause(scope)
    select_sql = text(
        f"""
        SELECT r.ride_id, r.start_time, r.end_message, f.comment_text
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        LEFT JOIN feedback f
               ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
        WHERE r.outcome = 'BASARISIZ_HARD'
          AND r.failure_category IS NULL
          AND r.classified_at IS NULL
          AND ci.is_test = false {clause}
        ORDER BY r.start_time
        LIMIT :batch
        """
    )
    update_sql = text(
        """
        UPDATE ride SET
            failure_category      = CAST(:category AS failure_category),
            failure_reason        = CAST(:reason AS failure_reason),
            classification_source = CAST(:source AS classification_source),
            classified_at         = now(),
            classifier_version    = :version
        WHERE ride_id = :ride_id AND start_time = :start_time
        """
    )
    total_processed = 0
    total_classified = 0
    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                select_sql, {**sparams, "batch": batch_size}
            ).mappings().all()
            if not rows:
                break
            updates = []
            for row in rows:
                ride = Ride(
                    ride_id=row["ride_id"],
                    source_ref="",
                    vehicle_id=0,
                    city_id=0,
                    user_ref="",
                    start_time=row["start_time"],
                    outcome=RideOutcome.BASARISIZ_HARD,
                    end_message=row["end_message"],
                )
                result = classify_ride(ride, row["comment_text"])
                if result.category is not None:
                    total_classified += 1
                updates.append(
                    {
                        "category": result.category.value if result.category else None,
                        "reason": result.reason.value if result.reason else None,
                        "source": result.source.value,
                        "version": version,
                        "ride_id": row["ride_id"],
                        "start_time": row["start_time"],
                    }
                )
            conn.execute(update_sql, updates)
            total_processed += len(rows)
        if len(rows) < batch_size:
            break
    return {"processed": total_processed, "classified": total_classified}
