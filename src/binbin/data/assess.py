"""Yazma tarafı: sahte arıza değerlendirmesi (`false_fault_assessment` upsert).

Aynı aracın sonraki sürüşünü LEAD() penceresiyle bulup her başarısız sürüş için
saf `assess_ride` çekirdek fonksiyonunu çağırır ve sonucu tabloya upsert eder.
Bu modül data→core kuplajını (assess tarafında) izole eder.
"""

from typing import Optional

from sqlalchemy import Engine, text

from binbin.config import ASSESSOR_VERSION
from binbin.core.false_fault import assess_ride
from binbin.core.ratios import different_user
from binbin.core.scenario_analysis import CURRENT_RULE, ScenarioStatus
from binbin.data.engine import (
    _scope_clause,
    current_rule_params,
    current_rule_sql,
    field_signal_join_sql,
)
from binbin.data.repository import AnalysisScope
from binbin.domain.enums import RideOutcome
from binbin.domain.models import Ride


def _delete_stale_assessments(conn, clause: str, params: dict) -> int:
    """Aday kümesinden düşmüş değerlendirmeleri siler (refresh yolu). UPSERT tek
    başına yetmez: kural daraldığında eski satırlar tabloda kalır."""
    return conn.execute(
        text(
            f"""
            DELETE FROM false_fault_assessment a
            USING ride r, city ci
            WHERE r.ride_id = a.ride_id
              AND r.start_time = a.ride_start_time
              AND ci.city_id = r.city_id
              {clause}
              AND NOT (
                  ci.is_test = false
                  AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
                  AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags))
                  AND {current_rule_sql("r")}
              )
            """
        ),
        params,
    ).rowcount


def assess_all(
    engine: Engine,
    scope: Optional[AnalysisScope],
    version: str = ASSESSOR_VERSION,
    refresh: bool = False,
) -> dict:
    """Aynı aracın sonraki sürüşünü LEAD() ile bulup başarısız sürüşleri
    değerlendirir ve false_fault_assessment tablosunu doldurur (upsert).

    refresh=False (varsayılan, ARTIMLI): yalnız henüz değerlendirilmemiş VEYA
    önceden DEGERLENDIRILEMEDI olan sürüşler işlenir. Bir sonraki DEGERLENDIRILEMEDI
    durumu, yeni bir ay yüklendiğinde (aracın 'son sürüş'ü artık bir sonrakine
    sahip) tazelenir — hem hızlı hem doğru.
    refresh=True: tüm başarısız sürüşler yeniden hesaplanır (eşik/mantık değişince).
    """
    clause, sparams = _scope_clause(scope)
    # ARTIMLI: seq'i mevcut değerlendirmelerle LEFT JOIN edip filtrele.
    incremental_join = (
        ""
        if refresh
        else """
        LEFT JOIN false_fault_assessment a
               ON a.ride_id = seq.ride_id AND a.ride_start_time = seq.start_time"""
    )
    incremental_where = (
        ""
        if refresh
        else " AND (a.ride_id IS NULL"
             " OR a.verdict = 'DEGERLENDIRILEMEDI'"
             " OR a.assessor_version <> :version)"
    )
    timeline_sql = text(
        f"""
        WITH scoped AS (
            SELECT r.ride_id, r.start_time, r.end_time, r.vehicle_id, r.user_ref,
                   r.outcome, r.duration_sec, r.distance_m, r.end_reason_id, r.end_message,
                   f.rating, f.comment_text,
                   (fsig.field_signal_reason_id IS NOT NULL) AS field_fault,
                   rg.displacement_m
            FROM ride r
            JOIN city ci ON ci.city_id = r.city_id
            LEFT JOIN feedback f
                   ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
            -- ADAY GUARD'I ("thresholds"): guard ile final WHERE aynı eşikleri
            -- `current_rule_params()`ten okur, bu yüzden TAM EŞLEŞMEDİR.
            {field_signal_join_sql(candidate_guard="thresholds")}
            LEFT JOIN ride_geo rg
                   ON rg.ride_id = r.ride_id AND rg.ride_start_time = r.start_time
            WHERE ci.is_test = false
              AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
              -- OOC ve outcome filtreleri LEAD'den ÖNCE koşmalı; yoksa "sonraki
              -- sürüş" analysis_timeline'dakinden farklı satır olur.
              AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags))
              {clause}
        ),
        seq AS (
            SELECT *,
                LEAD(ride_id)      OVER w AS next_ride_id,
                LEAD(start_time)   OVER w AS next_start_time,
                LEAD(outcome)      OVER w AS next_outcome,
                LEAD(duration_sec) OVER w AS next_duration_sec,
                LEAD(distance_m)   OVER w AS next_distance_m,
                LEAD(user_ref)     OVER w AS next_user_ref
            FROM scoped
            WINDOW w AS (PARTITION BY vehicle_id ORDER BY start_time, ride_id)
        )
        SELECT seq.* FROM seq{incremental_join}
        WHERE {current_rule_sql("seq")}{incremental_where}
        """
    )
    insert_sql = text(
        """
        INSERT INTO false_fault_assessment (
            ride_id, ride_start_time, fault_reported, report_evidence, vehicle_moved,
            next_ride_id, next_ride_start_time, next_ride_gap_min, next_ride_ok,
            next_ride_distance_m, healthy_proof, verdict, hypothesis,
            wasted_missions, assessor_version
        ) VALUES (
            :ride_id, :ride_start_time, :fault_reported,
            CAST(:report_evidence AS classification_source), :vehicle_moved,
            :next_ride_id, :next_ride_start_time, :next_ride_gap_min, :next_ride_ok,
            :next_ride_distance_m, :healthy_proof,
            CAST(:verdict AS fault_verdict), CAST(:hypothesis AS false_fault_hypothesis),
            :wasted_missions, :assessor_version
        )
        ON CONFLICT (ride_id, ride_start_time) DO UPDATE SET
            fault_reported = EXCLUDED.fault_reported,
            report_evidence = EXCLUDED.report_evidence,
            vehicle_moved = EXCLUDED.vehicle_moved,
            next_ride_id = EXCLUDED.next_ride_id,
            next_ride_start_time = EXCLUDED.next_ride_start_time,
            next_ride_gap_min = EXCLUDED.next_ride_gap_min,
            next_ride_ok = EXCLUDED.next_ride_ok,
            next_ride_distance_m = EXCLUDED.next_ride_distance_m,
            healthy_proof = EXCLUDED.healthy_proof,
            verdict = EXCLUDED.verdict,
            hypothesis = EXCLUDED.hypothesis,
            wasted_missions = EXCLUDED.wasted_missions,
            assessor_version = EXCLUDED.assessor_version,
            assessed_at = now()
        """
    )
    assessed = 0
    with engine.begin() as conn:
        params = {**sparams, **current_rule_params()}
        if refresh:
            _delete_stale_assessments(conn, clause, params)
        else:
            params["version"] = version
        rows = conn.execute(timeline_sql, params).mappings().all()
        payload = []
        for row in rows:
            ride = Ride(
                ride_id=row["ride_id"],
                source_ref="",
                vehicle_id=row["vehicle_id"],
                city_id=0,
                user_ref="",
                start_time=row["start_time"],
                outcome=RideOutcome.BASARISIZ_HARD,
                end_time=row["end_time"],
                distance_m=float(row["distance_m"]) if row["distance_m"] is not None else None,
                end_reason_id=row["end_reason_id"],
                end_message=row["end_message"],
            )
            next_ride = None
            if row["next_ride_id"] is not None:
                # Sonraki sürüşün outcome'u Mevcut Kural'a göre yeniden yazılır;
                # canlı motorun `_next_ride`i de aynısını yapar.
                next_status = CURRENT_RULE.status(
                    row["next_outcome"],
                    row["next_duration_sec"],
                    row["next_distance_m"],
                )
                next_ride = Ride(
                    ride_id=row["next_ride_id"],
                    source_ref="",
                    vehicle_id=row["vehicle_id"],
                    city_id=0,
                    user_ref="",
                    start_time=row["next_start_time"],
                    outcome=(
                        RideOutcome.BASARILI
                        if next_status is ScenarioStatus.SUCCESS
                        else RideOutcome.BASARISIZ_HARD
                    ),
                    distance_m=float(row["next_distance_m"])
                    if row["next_distance_m"] is not None
                    else None,
                )
            a = assess_ride(
                ride,
                next_ride,
                comment_text=row["comment_text"],
                rating=row["rating"],
                field_fault=row["field_fault"],
                next_ride_different_user=different_user(row["user_ref"], row["next_user_ref"]),
                displacement_m=row["displacement_m"],
            )
            payload.append(
                {
                    "ride_id": row["ride_id"],
                    "ride_start_time": row["start_time"],
                    "fault_reported": a.fault_reported,
                    "report_evidence": a.report_evidence.value,
                    "vehicle_moved": a.vehicle_moved,
                    "next_ride_id": a.next_ride_id,
                    "next_ride_start_time": a.next_ride_start_time,
                    "next_ride_gap_min": a.next_ride_gap_min,
                    "next_ride_ok": a.next_ride_ok,
                    "next_ride_distance_m": a.next_ride_distance_m,
                    "healthy_proof": a.healthy_proof,
                    "verdict": a.verdict.value,
                    "hypothesis": a.hypothesis.value,
                    "wasted_missions": a.wasted_missions,
                    "assessor_version": version,
                }
            )
        if payload:
            conn.execute(insert_sql, payload)
            assessed = len(payload)
    return {"assessed": assessed}
