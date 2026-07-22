"""Okuma tarafı (read-side) analitik sorgular — serbest fonksiyonlar.

Her fonksiyon bir `Engine` + `scope` alır ve `list[dict]`/`dict`/generator döner.
Scope enjeksiyonu `engine._scope_clause` ile üretilen WHERE parçası bind-param'larla
yapılır (ham değer SQL'e gömülmez).

Not: Bu modül `PostgresRideRepository` tarafından delege edilerek çağrılır; ayrıca
doğrudan da kullanılabilir (repo nesnesi olmadan engine ile).
"""

from typing import Iterable, Optional

from sqlalchemy import Engine, text

from binbin.config import FIELD_SIGNAL_WINDOW_POST_MIN, Scope
from binbin.data.engine import _as_dicts, _scope_clause, field_signal_join_sql
from binbin.data.repository import AnalysisScope


def resolve_scope(engine: Engine, scope: Scope) -> AnalysisScope:
    """Ülke/şehir adlarını id listelerine çözer. is_test şehirler daima dışlanır."""
    if scope.is_unrestricted:
        return AnalysisScope(None, None)
    country_ids: Optional[list[int]] = None
    city_ids: Optional[list[int]] = None
    with engine.connect() as conn:
        if scope.countries:
            rows = conn.execute(
                text("SELECT country_id FROM country WHERE name = ANY(:names)"),
                {"names": list(scope.countries)},
            ).scalars().all()
            country_ids = list(rows)
        if scope.cities:
            rows = conn.execute(
                text(
                    "SELECT city_id FROM city "
                    "WHERE name = ANY(:names) AND is_test = false"
                ),
                {"names": list(scope.cities)},
            ).scalars().all()
            city_ids = list(rows)
    return AnalysisScope(country_ids, city_ids)


def analysis_timeline(
    engine: Engine,
    scope: Optional[AnalysisScope],
    candidate_bounds: Optional[tuple[float, float]] = None,
) -> Iterable[dict]:
    """İki senaryolu analiz için araç/zaman sıralı, stream edilen timeline.

    LEAD alanları seçili kapsamın içindeki aynı aracın sonraki sürüşünü gösterir.
    Sonuç generator olduğu için yüz binlerce satır bellekte biriktirilmez.

    `candidate_bounds` = (azami süre sn, azami mesafe m) — verilirse sinyal-join
    yalnız BAŞARISIZ OLABİLECEK sürüşler için çalışır (bkz.
    `engine.field_signal_join_sql`). Değeri `scenario_analysis.candidate_bounds()`
    üretir; senaryo eşiklerinin maksimumu olduğu için başarısız kümesinin üstkümesidir.

    VERİLMEZSE guard uygulanmaz — yavaş ama DAİMA doğru. Yani bu parametreyi unutmak
    yanlış sayı değil, yalnız yavaşlık üretir (güvenli varsayılan).
    """
    clause, params = _scope_clause(scope)
    if candidate_bounds is not None:
        params = {
            **params,
            "fsig_max_dur": float(candidate_bounds[0]),
            "fsig_max_dist": float(candidate_bounds[1]),
        }
    sql = text(
        f"""
        WITH scoped AS (
            SELECT
                r.ride_id, r.source_ref, r.user_ref, r.start_time, r.end_time,
                r.duration_sec, r.distance_m, r.outcome::text AS outcome,
                r.vehicle_id, r.city_id, v.external_code,
                ci.name AS city,
                sr.source_sub_region_id AS sub_region_code,
                sr.name AS sub_region_name,
                EXTRACT(HOUR FROM (r.start_time AT TIME ZONE co.timezone))::int AS local_hour,
                r.triggered_regulation_id, r.end_reason_id, r.end_message,
                r.unlock_ack, r.start_battery_pct, r.connection_lost,
                r.motor_error_code, r.bms_error_code, r.user_cancelled,
                r.payment_status::text AS payment_status,
                r.data_quality_flags,
                f.rating, f.comment_text,
                fsig.field_signal_reason_id, fsig.field_category::text AS field_category,
                fsig.field_reason::text AS field_reason, fsig.field_signal_desc
            FROM ride r
            JOIN city ci ON ci.city_id = r.city_id
            JOIN country co ON co.country_id = ci.country_id
            JOIN vehicle v ON v.vehicle_id = r.vehicle_id
            LEFT JOIN sub_region sr ON sr.sub_region_id = r.sub_region_id
            LEFT JOIN feedback f
                   ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
            {field_signal_join_sql(
                candidate_guard="thresholds" if candidate_bounds is not None else None
            )}
            WHERE ci.is_test = false
              AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
              AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags))
              {clause}
        ), timeline AS (
            SELECT
                *,
                LEAD(ride_id) OVER w AS next_ride_id,
                LEAD(start_time) OVER w AS next_start_time,
                LEAD(duration_sec) OVER w AS next_duration_sec,
                LEAD(distance_m) OVER w AS next_distance_m,
                LEAD(outcome) OVER w AS next_outcome
            FROM scoped
            WINDOW w AS (PARTITION BY vehicle_id ORDER BY start_time, ride_id)
        )
        SELECT *
        FROM timeline
        ORDER BY vehicle_id, start_time, ride_id
        """
    )

    def _iter_rows():
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(sql, params)
            for row in result.mappings():
                yield dict(row)

    return _iter_rows()


def out_of_content_counts(engine: Engine, scope: Optional[AnalysisScope]) -> dict:
    """Analiz dışı (out-of-content) sürüş sayıları.

    IoT/telemetri hatası: mesafe>20km VEYA süre>=6sa. Bu sürüşler `analysis_timeline`'da
    dışlanır; burada ayrı kova olarak (toplam + mesafe/süre kırılımı) sayılır.
    """
    clause, params = _scope_clause(scope)
    sql = text(
        f"""
        SELECT
            count(*) FILTER (WHERE r.distance_m > 20000)   AS by_distance,
            count(*) FILTER (WHERE r.duration_sec >= 21600) AS by_duration,
            count(*) FILTER (
                WHERE r.distance_m > 20000 OR r.duration_sec >= 21600
            ) AS total
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        WHERE ci.is_test = false
          AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
          {clause}
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, params).mappings().one()
    return {"total": row["total"], "by_distance": row["by_distance"],
            "by_duration": row["by_duration"]}


def ops_cost_rows(engine: Engine, scope: Optional[AnalysisScope]) -> list[dict]:
    # ops_cost_model bilerek boştur; boşsa [] döner ve analiz TL raporlamaz.
    # (scope parametrik değil — maliyet modeli küçük ve global okunur.)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT mission_type, labor_cost, fuel_cost, currency "
                "FROM ops_cost_model"
            )
        )
        return _as_dicts(result)


def signal_discrimination_rows(
    engine: Engine, scope: Optional[AnalysisScope]
) -> list[dict]:
    """Kural kitabındaki HER kod için: başarısız/başarılı sürüş penceresinde kaç kez düştü.

    `field_signal_join_sql` yalnız `is_fault_signal=true` kodları seçer; bu sorgu
    KASITLI olarak 58 kodun TAMAMINI tarar — bir kodun sinyal yapılıp yapılmayacağına
    karar verebilmek için ADAY kodların da ölçülmesi gerekir (governance).

    Pencere, üretimdeki sinyal-join ile AYNI sözleşmeyi kullanır (sonraki sürüşte
    kesilir) — yoksa denetim raporu, denetlediği mekanizmadan farklı bir şey ölçer.
    `next_start` LEAD'i TÜM sürüşler üzerinden alınır (out-of-content dahil), çünkü
    analizden dışlanan bir sürüş de aracı fiilen meşgul eder.

    Sürüş başına kod başına TEK sayım (DISTINCT) — aynı kod pencerede 5 kez düşse de
    bu 1 sürüştür; oranların paydası sürüş sayısıdır.
    """
    clause, params = _scope_clause(scope)
    sql = text(
        f"""
        WITH win AS (
            SELECT r.ride_id, r.start_time, r.vehicle_id, r.city_id, r.outcome,
                   r.data_quality_flags,
                   COALESCE(r.end_time, r.start_time) AS end_time,
                   LEAD(r.start_time) OVER (
                       PARTITION BY r.vehicle_id ORDER BY r.start_time) AS next_start
            FROM ride r
        ),
        scoped AS (
            SELECT w.* FROM win w
            JOIN city ci ON ci.city_id = w.city_id
            WHERE ci.is_test = false
              AND w.outcome IN ('BASARILI', 'BASARISIZ_HARD')
              AND NOT ('OUT_OF_CONTENT' = ANY(w.data_quality_flags))
              {clause}
        ),
        base AS (
            SELECT count(*) FILTER (WHERE outcome = 'BASARISIZ_HARD') AS n_fail,
                   count(*) FILTER (WHERE outcome = 'BASARILI')       AS n_ok
            FROM scoped
        ),
        hits AS (
            SELECT DISTINCT s.ride_id, s.outcome, e.status_reason_id AS reason_id
            FROM scoped s
            JOIN fleet_status_event e
              ON e.vehicle_id = s.vehicle_id
             AND e.created_on >= s.start_time
             AND e.created_on < LEAST(
                     s.end_time + make_interval(mins => :win_min),
                     COALESCE(s.next_start, 'infinity'::timestamptz))
            WHERE e.status_reason_id IS NOT NULL
        )
        SELECT fsr.reason_id, fsr.description, fsr.is_fault_signal, fsr.verified,
               count(h.ride_id) FILTER (WHERE h.outcome = 'BASARISIZ_HARD') AS fail_rides,
               count(h.ride_id) FILTER (WHERE h.outcome = 'BASARILI')       AS ok_rides,
               b.n_fail, b.n_ok
        FROM fleet_status_reason fsr
        LEFT JOIN hits h ON h.reason_id = fsr.reason_id
        CROSS JOIN base b
        GROUP BY fsr.reason_id, fsr.description, fsr.is_fault_signal, fsr.verified,
                 b.n_fail, b.n_ok
        ORDER BY fail_rides DESC
        """
    )
    with engine.connect() as conn:
        result = conn.execute(
            sql, {**params, "win_min": FIELD_SIGNAL_WINDOW_POST_MIN}
        )
        return _as_dicts(result)


def list_data_loads(engine: Engine) -> list[dict]:
    """Yüklenen CSV'lerin denetim kaydı (en yeniden eskiye)."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT data_load_id, file_name, period_start, period_end,
                       rows_read, rows_inserted, rows_skipped, rows_flagged,
                       status, started_at, finished_at
                FROM data_load
                ORDER BY data_load_id DESC
                """
            )
        )
        return _as_dicts(result)
