"""Yazma tarafı: başarısız sürüşleri sınıflandırıp `ride` tablosuna geri yazar.

Bu modül data→core kuplajını izole eder: her satır için saf `classify_ride`
çekirdek fonksiyonunu çağırıp sonucu UPDATE ile yazar. Idempotent (classified_at damgası).
"""

from typing import Optional

from sqlalchemy import Engine, text

from binbin.config import CLASSIFIER_VERSION
from binbin.core.classifier import classify_ride
from binbin.data.engine import _scope_clause, field_signal_join_sql
from binbin.data.repository import AnalysisScope
from binbin.domain.enums import FailureCategory, FailureReason, RideOutcome
from binbin.domain.models import Ride


def _reset_classification(engine: Engine, clause: str, sparams: dict) -> int:
    """Kapsamdaki başarısız sürüşlerin sınıflandırma damgalarını temizler (refresh yolu).

    `classification_source` NOT NULL'dur ve `ck_category_needs_source` kısıtı "kategori
    doluysa kaynak NONE olamaz" der; kategoriyle birlikte kaynağı 'NONE'a çekmek bu
    kısıtı sağlar. Etkilenen satır sayısını döner.

    KASITLI ASİMETRİ: reset OUT_OF_CONTENT sürüşleri de KAPSAR, ama aşağıdaki SELECT
    onları dışlar. Böylece eski sürümde yanlışlıkla sınıflandırılmış OOC satırları
    temizlenir ve bir daha kategori almazlar — kalıcı tablo `analysis_timeline`'ın
    gördüğü kümeye yakınsar. Guard (`classified_at IS NOT NULL OR ...`) sayesinde
    tekrar çalıştırmak no-op'tur.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                f"""
                UPDATE ride SET
                    failure_category      = NULL,
                    failure_reason        = NULL,
                    classification_source = 'NONE',
                    classified_at         = NULL,
                    classifier_version    = NULL
                FROM city ci
                WHERE ci.city_id = ride.city_id
                  AND ride.outcome = 'BASARISIZ_HARD'
                  AND ci.is_test = false {clause}
                  AND (ride.classified_at IS NOT NULL
                       OR ride.failure_category IS NOT NULL)
                """
            ),
            sparams,
        )
        return result.rowcount


def classify_all(
    engine: Engine,
    scope: Optional[AnalysisScope],
    batch_size: int = 10000,
    version: str = CLASSIFIER_VERSION,
    refresh: bool = False,
) -> dict:
    """Sınıflandırılmamış başarısız sürüşleri sınıflandırıp geri yazar.

    Yalnızca outcome='BASARISIZ_HARD' AND failure_category IS NULL AND
    classified_at IS NULL çekilir; sonuç NONE olsa bile classified_at damgalanır
    (idempotent — tekrar çalışınca aynı satırı işlemez).

    refresh=True: kapsamdaki TÜM başarısız sürüşlerin damgaları önce temizlenir, sonra
    aynı artımlı döngü çalışır — sınıflandırma kuralı/sinyali değişince (ör. yeni bir
    veri kaynağı bağlanınca) kalıcı `ride.failure_category` tazelenir. `assess_all`'ın
    `refresh` bayrağıyla aynı sözleşme.

    NEDEN "önce temizle, sonra normal döngü": guard'ı (classified_at IS NULL) doğrudan
    WHERE'den kaldırmak SONSUZ DÖNGÜ yaratır — döngü `ORDER BY start_time LIMIT :batch`
    ile hep aynı ilk N satırı çeker, UPDATE'ten sonra da eşleşmeye devam ederler. Reset
    bunu yapısal olarak engeller.
    """
    clause, sparams = _scope_clause(scope)
    if refresh:
        _reset_classification(engine, clause, sparams)
    select_sql = text(
        f"""
        SELECT r.ride_id, r.start_time, r.end_message, f.comment_text,
               fsig.field_category::text AS field_category,
               fsig.field_reason::text AS field_reason
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        LEFT JOIN feedback f
               ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
        {field_signal_join_sql()}
        WHERE r.outcome = 'BASARISIZ_HARD'
          AND r.failure_category IS NULL
          AND r.classified_at IS NULL
          AND ci.is_test = false
          -- OUT_OF_CONTENT `analysis_timeline` tarafından DIŞLANIR (mesafe>20km veya
          -- süre≥6sa; ayrı kovada raporlanır). Burada da dışlanmazsa kalıcı
          -- ride.failure_category, canlı analizin hiç görmediği sürüşlere kategori
          -- atar ve iki çıktı farklı "kaç sürüş" sayısı basar.
          AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags)) {clause}
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
                result = classify_ride(
                    ride,
                    row["comment_text"],
                    field_category=FailureCategory(row["field_category"])
                    if row["field_category"] else None,
                    field_reason=FailureReason(row["field_reason"])
                    if row["field_reason"] else None,
                )
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
