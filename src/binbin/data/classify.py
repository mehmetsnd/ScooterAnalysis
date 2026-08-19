"""Yazma tarafı: başarısız sürüşleri sınıflandırıp `ride` tablosuna geri yazar.

Bu modül data→core kuplajını izole eder: her satır için saf `classify_ride`
çekirdek fonksiyonunu çağırıp sonucu UPDATE ile yazar. Idempotent (classified_at damgası).
"""

from typing import Optional

from sqlalchemy import Engine, text

from binbin.config import CLASSIFIER_VERSION
from binbin.core.classifier import classify_ride
from binbin.core.ratios import enum_or_none
from binbin.data.engine import (
    _scope_clause,
    current_rule_params,
    current_rule_sql,
    field_signal_join_sql,
    maintenance_signal_sql,
    neighbor_base_sql,
    neighbor_signal_sql,
)
from binbin.data.repository import AnalysisScope
from binbin.domain.enums import (
    FailureCategory,
    FailureReason,
    PaymentStatus,
    RideOutcome,
)
from binbin.domain.models import Ride


def _require_alignment_migration(engine: Engine) -> None:
    """Kurulum SQL uygulanmadıysa reset'ten ÖNCE durur.

    Reset kendi transaction'ında COMMIT olur; migration yoksa sonraki UPDATE
    CheckViolation verir ve ride.failure_category tablo genelinde silinmiş kalır.
    """
    with engine.begin() as conn:
        migrated = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_constraint
                     WHERE conrelid = 'public.ride'::regclass
                       AND conname  = 'ck_success_has_no_failure'
                       AND pg_get_constraintdef(oid) LIKE '%%duration_sec%%'
                ) AS migrated
                """
            )
        ).mappings().all()[0]["migrated"]
    if not migrated:
        raise RuntimeError(
            "ck_success_has_no_failure eski tanımda: db/01_setup.sql "
            "çalıştırılmadan classify --refresh yapılamaz "
            "(reset commit olur, yazma CheckViolation ile patlar)."
        )


def _reset_classification(engine: Engine, clause: str, sparams: dict) -> int:
    """Kapsamdaki başarısız sürüşlerin sınıflandırma damgalarını temizler (refresh yolu).

    Kaynağı 'NONE'a çekmek `ck_category_needs_source` kısıtını sağlar. KASITLI ASİMETRİ:
    reset OUT_OF_CONTENT satırları da kapsar, ama aşağıdaki SELECT onları dışlar — eski
    sürümde yanlışlıkla sınıflanmış OOC satırları temizlenir, bir daha kategori almaz.
    Guard sayesinde tekrar çalıştırmak no-op. Etkilenen satır sayısını döner.
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
                  AND {current_rule_sql("ride")}
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
    # Batch başına CTE baştan hesaplanır; 10.000'de 7 tam tarama oluyordu (ölçüm 121→43 sn).
    batch_size: int = 100000,
    version: str = CLASSIFIER_VERSION,
    refresh: bool = False,
) -> dict:
    """Sınıflandırılmamış başarısız sürüşleri sınıflandırıp geri yazar.

    Yalnız `failure_category IS NULL AND classified_at IS NULL` çekilir; sonuç NONE olsa
    bile damgalanır (idempotent). refresh=True damgaları önce temizler, sonra aynı artımlı
    döngüyü çalıştırır — kural değişince kalıcı `ride.failure_category` tazelenir.

    Guard'ı doğrudan WHERE'den kaldırmak SONSUZ DÖNGÜ yaratır (LIMIT'li döngü hep aynı ilk
    N satırı çeker, UPDATE sonrası da eşleşirler); reset bunu yapısal olarak engeller.
    """
    clause, sparams = _scope_clause(scope)
    reset_clause, reset_params = _scope_clause(scope, alias="ride")
    rule_params = current_rule_params()
    if refresh:
        _require_alignment_migration(engine)
        _reset_classification(engine, reset_clause, {**reset_params, **rule_params})
    # Komşu penceresi BAŞARILI sürüşleri de görmeli; ince CTE'de hesaplanıp aday
    # satırlara join edilir (geniş satırı pencereye sokmak diske taşıyordu).
    select_sql = text(
        f"""
        WITH nb_base AS (
            SELECT {neighbor_base_sql("r")}
            FROM ride r
            JOIN city ci ON ci.city_id = r.city_id
            WHERE r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
              AND ci.is_test = false
              AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags)) {clause}
        ), nb AS ({neighbor_signal_sql()}
        )
        SELECT r.ride_id, r.start_time, r.end_message, f.comment_text,
               r.triggered_regulation_id, r.unlock_ack, r.start_battery_pct,
               r.connection_lost, r.motor_error_code, r.bms_error_code,
               r.user_cancelled, r.payment_status::text AS payment_status,
               fsig.field_category::text AS field_category,
               fsig.field_reason::text AS field_reason,
               COALESCE(mnt.maintenance_fault, false) AS maintenance_fault,
               nb.neighbor_vehicle_fault, nb.neighbor_user_fault
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        LEFT JOIN feedback f
               ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
        JOIN nb ON nb.ride_id = r.ride_id AND nb.start_time = r.start_time
        {field_signal_join_sql(candidate_guard="thresholds")}
        {maintenance_signal_sql()}
        WHERE {current_rule_sql("r")}
          AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
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
    # Satır başına UPDATE 65.963 satırda 22,5 sn sürüyordu; dizi parametreli tek
    # ifade aynı işi bir turda yapar (değerler yine bind-param).
    update_sql = text(
        """
        UPDATE ride SET
            failure_category      = CAST(v.category AS failure_category),
            failure_reason        = CAST(v.reason AS failure_reason),
            classification_source = CAST(v.source AS classification_source),
            classified_at         = now(),
            classifier_version    = :version
        FROM unnest(
            CAST(:ride_ids AS bigint[]), CAST(:start_times AS timestamptz[]),
            CAST(:categories AS text[]), CAST(:reasons AS text[]),
            CAST(:sources AS text[])
        ) AS v(ride_id, start_time, category, reason, source)
        WHERE ride.ride_id = v.ride_id AND ride.start_time = v.start_time
        """
    )
    total_processed = 0
    total_classified = 0
    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                select_sql, {**sparams, **rule_params, "batch": batch_size}
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
                    # Hardcode kasıtlı: classify_ride yalnız BASARISIZ_HARD'a
                    # kategori atar. Canlı motor da aynısını yapar.
                    outcome=RideOutcome.BASARISIZ_HARD,
                    end_message=row["end_message"],
                    triggered_regulation_id=row["triggered_regulation_id"],
                    unlock_ack=row["unlock_ack"],
                    start_battery_pct=row["start_battery_pct"],
                    connection_lost=row["connection_lost"],
                    motor_error_code=row["motor_error_code"],
                    bms_error_code=row["bms_error_code"],
                    user_cancelled=row["user_cancelled"],
                    payment_status=enum_or_none(
                        PaymentStatus, row["payment_status"]
                    ),
                )
                result = classify_ride(
                    ride,
                    row["comment_text"],
                    field_category=FailureCategory(row["field_category"])
                    if row["field_category"] else None,
                    field_reason=FailureReason(row["field_reason"])
                    if row["field_reason"] else None,
                    maintenance_fault=row["maintenance_fault"],
                    neighbor_vehicle_fault=row["neighbor_vehicle_fault"],
                    neighbor_user_fault=row["neighbor_user_fault"],
                )
                if result.category is not None:
                    total_classified += 1
                updates.append(
                    {
                        "category": result.category.value if result.category else None,
                        "reason": result.reason.value if result.reason else None,
                        "source": result.source.value,
                        "ride_id": row["ride_id"],
                        "start_time": row["start_time"],
                    }
                )
            written = conn.execute(
                update_sql,
                {
                    "version": version,
                    "ride_ids": [u["ride_id"] for u in updates],
                    "start_times": [u["start_time"] for u in updates],
                    "categories": [u["category"] for u in updates],
                    "reasons": [u["reason"] for u in updates],
                    "sources": [u["source"] for u in updates],
                },
            ).rowcount
            # Eşleşmeyen yazma sessizce boş tablo bırakır ve canlı analyze ile ayrışır.
            if written != len(updates):
                raise RuntimeError(
                    f"classify: {len(updates)} satır yazılacaktı, {written} yazıldı."
                )
            total_processed += len(rows)
        if len(rows) < batch_size:
            break
    return {"processed": total_processed, "classified": total_classified}
