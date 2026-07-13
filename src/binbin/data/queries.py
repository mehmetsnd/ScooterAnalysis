"""Okuma tarafı (read-side) analitik sorgular — serbest fonksiyonlar.

Her fonksiyon bir `Engine` + `scope` alır ve `list[dict]`/`dict` döner. Ağır
aggregation SQL'e (FILTER, window) yıkılır; Python sadece şekillendirir. Scope
enjeksiyonu `engine.run_scoped` üzerinden `{scope}` yer tutucusuyla yapılır.

Not: Bu modül `PostgresRideRepository` tarafından delege edilerek çağrılır; ayrıca
doğrudan da kullanılabilir (repo nesnesi olmadan engine ile).
"""

from typing import Optional

from sqlalchemy import Engine, text

from binbin.config import Scope
from binbin.data.engine import _as_dicts, run_scoped
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


def failure_category_counts(engine: Engine, scope: Optional[AnalysisScope]) -> list[dict]:
    sql = """
        SELECT r.failure_category::text AS category, count(*) AS count
        FROM ride r JOIN city ci ON ci.city_id = r.city_id
        WHERE r.outcome = 'BASARISIZ_HARD' AND ci.is_test = false {scope}
        GROUP BY r.failure_category
    """
    return run_scoped(engine, sql, scope)


def failure_criteria_counts(
    engine: Engine,
    scope: Optional[AnalysisScope],
    dur_thr: float = 120,
    dist_thr: float = 60,
) -> dict:
    """Başarısızlık eşiği (süre < dur_thr VE mesafe < dist_thr) sayaçları.

    Eşikler PARAMETRİKTİR (bind-param): default 120sn/60m gerçek kuraldır, ama
    what-if senaryosu için farklı değerlerle çağrılabilir. `zero_distance_long_duration`
    kuyruğu süre eşiğini (dur_thr) 'açıldı hiç gitmedi' sınırı olarak kullanır.
    """
    sql = """
        SELECT
          count(*) FILTER (WHERE r.outcome = 'BASARISIZ_HARD') AS failed_total,
          count(*) FILTER (WHERE r.outcome = 'BASARILI')       AS success_total,
          count(*) FILTER (WHERE r.outcome = 'BASARISIZ_HARD'
                AND r.duration_sec < :dur_thr AND r.distance_m < :dist_thr) AS criteria_and_failed,
          count(*) FILTER (WHERE r.outcome = 'BASARILI'
                AND r.duration_sec < :dur_thr AND r.distance_m < :dist_thr) AS criteria_and_success,
          count(*) FILTER (WHERE r.distance_m IS NOT NULL
                AND r.distance_m < 1 AND r.duration_sec > :dur_thr)  AS zero_distance_long_duration
        FROM ride r JOIN city ci ON ci.city_id = r.city_id
        WHERE ci.is_test = false {scope}
    """
    return run_scoped(engine, sql, scope, {"dur_thr": dur_thr, "dist_thr": dist_thr})[0]


def vehicle_failure_counts(
    engine: Engine, scope: Optional[AnalysisScope], min_failures: int
) -> list[dict]:
    sql = """
        SELECT r.vehicle_id, v.external_code, count(*) AS failures
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        JOIN vehicle v ON v.vehicle_id = r.vehicle_id
        WHERE r.outcome = 'BASARISIZ_HARD' AND ci.is_test = false {scope}
        GROUP BY r.vehicle_id, v.external_code
        HAVING count(*) >= :min_failures
        ORDER BY failures DESC
        LIMIT 100
    """
    return run_scoped(engine, sql, scope, {"min_failures": min_failures})


def control_group_stats(engine: Engine, scope: Optional[AnalysisScope]) -> list[dict]:
    sql = """
        SELECT
          count(*) FILTER (WHERE a.report_evidence IN ('TEXT_MESSAGE','TEXT_COMMENT'))
              AS text_total,
          count(*) FILTER (WHERE a.report_evidence IN ('TEXT_MESSAGE','TEXT_COMMENT')
              AND a.healthy_proof) AS text_healthy,
          count(*) FILTER (WHERE a.fault_reported)                       AS reported_total,
          count(*) FILTER (WHERE a.fault_reported AND a.healthy_proof)   AS reported_healthy,
          count(*) FILTER (WHERE NOT a.fault_reported)                   AS control_total,
          count(*) FILTER (WHERE NOT a.fault_reported AND a.healthy_proof) AS control_healthy
        FROM false_fault_assessment a
        JOIN ride r ON r.ride_id = a.ride_id AND r.start_time = a.ride_start_time
        JOIN city ci ON ci.city_id = r.city_id
        WHERE ci.is_test = false AND a.verdict <> 'DEGERLENDIRILEMEDI' {scope}
    """
    c = run_scoped(engine, sql, scope)[0]
    return [
        {"group": "ariza_metinli", "total": c["text_total"], "healthy": c["text_healthy"]},
        {"group": "herhangi_bildirimli", "total": c["reported_total"], "healthy": c["reported_healthy"]},
        {"group": "bildirimsiz", "total": c["control_total"], "healthy": c["control_healthy"]},
    ]


def false_fault_counts(engine: Engine, scope: Optional[AnalysisScope]) -> list[dict]:
    sql = """
        SELECT a.verdict::text AS verdict, a.hypothesis::text AS hypothesis,
               count(*) AS events,
               count(DISTINCT r.vehicle_id) AS vehicles,
               COALESCE(sum(a.wasted_missions), 0) AS wasted
        FROM false_fault_assessment a
        JOIN ride r ON r.ride_id = a.ride_id AND r.start_time = a.ride_start_time
        JOIN city ci ON ci.city_id = r.city_id
        WHERE ci.is_test = false {scope}
        GROUP BY a.verdict, a.hypothesis
        ORDER BY events DESC
    """
    return run_scoped(engine, sql, scope)


def subregion_stats(
    engine: Engine, scope: Optional[AnalysisScope], min_rides: int
) -> list[dict]:
    sql = """
        SELECT ci.name AS city,
               sr.source_sub_region_id AS sub_region_code,
               sr.name AS sub_region_name,
               count(*) AS total_rides,
               count(*) FILTER (WHERE r.outcome = 'BASARISIZ_HARD') AS failed,
               count(*) FILTER (WHERE a.verdict = 'SAHTE_ALARM_SUPHESI') AS false_alarm
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        JOIN sub_region sr ON sr.sub_region_id = r.sub_region_id
        LEFT JOIN false_fault_assessment a
               ON a.ride_id = r.ride_id AND a.ride_start_time = r.start_time
        WHERE ci.is_test = false {scope}
        GROUP BY ci.name, sr.source_sub_region_id, sr.name
        HAVING count(*) >= :min_rides
        ORDER BY failed DESC
    """
    return run_scoped(engine, sql, scope, {"min_rides": min_rides})


def hour_region_counts(engine: Engine, scope: Optional[AnalysisScope]) -> list[dict]:
    sql = """
        SELECT ci.name AS city,
               EXTRACT(HOUR FROM (r.start_time AT TIME ZONE co.timezone))::int AS hour,
               count(*) AS total,
               count(*) FILTER (WHERE r.outcome = 'BASARISIZ_HARD') AS failed
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        JOIN country co ON co.country_id = ci.country_id
        WHERE ci.is_test = false {scope}
        GROUP BY ci.name, hour
        ORDER BY ci.name, hour
    """
    return run_scoped(engine, sql, scope)


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
