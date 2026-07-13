"""PostgreSQL repository — projedeki tüm SQL burada.

Şema önceden SQL dosyalarıyla kurulur; burada create_all/DROP/migration YAPILMAZ
(create_type=False). Table tanımları yalnız Python'dan INSERT/SELECT içindir. Ağır
aggregation'lar Python yerine SQL'e (window function) yıkılır; dışarıya list[dict] döner.

Partitioning: `ride` DB'de aylık partition'lı, bu yüzden PK/FK'ler composite
(ride_id, start_time). Partition pruning için sorgulara daima start_time/city filtresi girer.

SQL GÜVENLİK SÖZLEŞMESİ (web'e taşırken kritik — ihlal etme):
  * DEĞERLER daima bind-parametre (`:param`) ile geçer, asla string'e gömülmez.
  * IDENTIFIER'lar (tablo/kolon/alias) yalnız SABİT LİTERAL; istekten gelen string
    interpolate edilmez. Tek dinamik string-DDL noktası (partition adı) ingest'te doğrulanır.
"""

import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Date,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    text,
)

from binbin.config import (
    ASSESSOR_VERSION,
    CLASSIFIER_VERSION,
    DB_MAX_OVERFLOW,
    DB_POOL_PRE_PING,
    DB_POOL_RECYCLE_SEC,
    DB_POOL_SIZE,
    Scope,
)
from binbin.core.classifier import classify_ride
from binbin.core.false_fault import assess_ride
from binbin.data.repository import AnalysisScope
from binbin.domain.enums import RideOutcome
from binbin.domain.models import Ride

metadata = MetaData()

# Tablo tanımları — şemayla birebir (enum kolonları String olarak yeterli).
country_table = Table(
    "country", metadata,
    Column("country_id", Integer, primary_key=True),
    Column("source_country_id", Integer, nullable=False, unique=True),
    Column("name", String, nullable=False, unique=True),
    Column("iso_code", String),
    Column("currency", String, nullable=False),
    Column("timezone", Text, nullable=False),
    Column("active", Boolean, nullable=False),
)

city_table = Table(
    "city", metadata,
    Column("city_id", Integer, primary_key=True),
    Column("country_id", Integer, nullable=False),
    Column("source_region_id", Integer, nullable=False),
    Column("name", String, nullable=False),
    Column("admin_authority", String),
    Column("is_test", Boolean, nullable=False),
    Column("active", Boolean, nullable=False),
)

sub_region_table = Table(
    "sub_region", metadata,
    Column("sub_region_id", Integer, primary_key=True),
    Column("city_id", Integer, nullable=False),
    Column("source_sub_region_id", Integer, nullable=False),
    Column("name", String),
)

end_reason_table = Table(
    "end_reason", metadata,
    Column("reason_id", Integer, primary_key=True),
    Column("label", String),
    Column("category_hint", String),
    Column("reason_hint", String),
    Column("verified", Boolean, nullable=False),
    Column("first_seen_at", DateTime(timezone=True)),
    Column("notes", Text),
)

vehicle_table = Table(
    "vehicle", metadata,
    Column("vehicle_id", Integer, primary_key=True),
    Column("source_ref", String, nullable=False, unique=True),
    Column("external_code", String, unique=True),
    Column("model", String),
    Column("firmware_version", String),
    Column("iot_box_id", String),
    Column("status", String, nullable=False),
)

ride_table = Table(
    "ride", metadata,
    Column("ride_id", Integer, primary_key=True),
    Column("source_ref", String, nullable=False),
    Column("vehicle_id", Integer, nullable=False),
    Column("city_id", Integer, nullable=False),
    Column("sub_region_id", Integer),
    Column("triggered_regulation_id", Integer),
    Column("user_ref", String, nullable=False),
    Column("start_time", DateTime(timezone=True), nullable=False),
    Column("end_time", DateTime(timezone=True)),
    Column("duration_sec", Numeric(10, 2)),
    Column("distance_m", Numeric(12, 2)),
    Column("outcome", String, nullable=False),
    Column("failure_category", String),
    Column("failure_reason", String),
    Column("classification_source", String, nullable=False),
    Column("classified_at", DateTime(timezone=True)),
    Column("classifier_version", String),
    Column("end_reason_id", Integer),
    Column("end_message", Text),
    Column("gross_amount", Numeric(12, 2)),
    Column("currency", String),
    Column("data_quality_flags", ARRAY(Text), nullable=False),
    Column("data_load_id", Integer),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
)

feedback_table = Table(
    "feedback", metadata,
    Column("feedback_id", Integer, primary_key=True),
    Column("ride_id", Integer, nullable=False),
    Column("ride_start_time", DateTime(timezone=True), nullable=False),
    Column("rating", Integer),
    Column("comment_text", Text),
    Column("created_at", DateTime(timezone=True)),
)

data_load_table = Table(
    "data_load", metadata,
    Column("data_load_id", Integer, primary_key=True),
    Column("file_name", Text, nullable=False),
    Column("file_bytes", Integer),
    Column("period_start", Date),
    Column("period_end", Date),
    Column("rows_read", Integer),
    Column("rows_inserted", Integer),
    Column("rows_skipped", Integer),
    Column("rows_flagged", Integer),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String, nullable=False),
    Column("notes", Text),
)


def _database_url() -> str:
    """`.env`/ortamdan DATABASE_URL okur; yoksa ham KeyError yerine anlaşılır hata."""
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL tanımlı değil. `.env.example`'ı `.env` olarak kopyalayıp "
            "DATABASE_URL değerini doldurun (örn. postgresql+psycopg://user:pass@host/db)."
        )
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Süreç başına TEK, havuzlu SQLAlchemy Engine (paylaşımlı, thread-safe).

    Web'de her istek yeni engine kurarsa bağlantı tükenir; burada tek engine'in
    connection pool'u yeniden kullanılır. CLI'da da zararsız (tek bağlantı yeter).
    """
    return create_engine(
        _database_url(),
        pool_pre_ping=DB_POOL_PRE_PING,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_recycle=DB_POOL_RECYCLE_SEC,
    )


def _as_dicts(result) -> list[dict]:
    """SQLAlchemy sonucunu list[dict]'e çevirir."""
    return [dict(m) for m in result.mappings().all()]


# city alias'ı — sabit LİTERAL, asla dışarıdan gelmez (SQL güvenlik sözleşmesi).
_CITY_ALIAS = "ci"


def _scope_clause(scope: Optional[AnalysisScope]) -> tuple[str, dict]:
    """AnalysisScope'tan WHERE parçası + parametreler üretir (is_test filtresi ayrıdır).

    None alanlar filtrelenmez. Şehir/ülke id'leri DAİMA bind-param `ANY(:param)` ile
    bağlanır; alias sabit literaldir → interpolasyona kullanıcı girdisi akmaz.
    """
    if scope is None:
        return "", {}
    clause = ""
    params: dict = {}
    if scope.country_ids is not None:
        clause += f" AND {_CITY_ALIAS}.country_id = ANY(:sc_country_ids)"
        params["sc_country_ids"] = list(scope.country_ids)
    if scope.city_ids is not None:
        clause += f" AND {_CITY_ALIAS}.city_id = ANY(:sc_city_ids)"
        params["sc_city_ids"] = list(scope.city_ids)
    return clause, params


class PostgresRideRepository:
    """AnalysisRepository Protocol'ünün Postgres implementasyonu (raw SQL + Core)."""

    def __init__(self, engine: Optional[Engine] = None) -> None:
        self.engine = engine if engine is not None else get_engine()

    # ------------------------------------------------------------------ scope
    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        """Ülke/şehir adlarını id listelerine çözer. is_test şehirler daima dışlanır."""
        if scope.is_unrestricted:
            return AnalysisScope(None, None)
        country_ids: Optional[list[int]] = None
        city_ids: Optional[list[int]] = None
        with self.engine.connect() as conn:
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

    def _rows(self, sql: str, scope: Optional[AnalysisScope], extra: Optional[dict] = None):
        clause, params = _scope_clause(scope)
        if extra:
            params.update(extra)
        # `.replace` (`.format` değil): SQL'de literal `{}` geçse bile patlamaz,
        # format-injection yüzeyi sıfır. `clause` kod-üretimli, yalnız bind placeholder içerir.
        with self.engine.connect() as conn:
            result = conn.execute(text(sql.replace("{scope}", clause)), params)
            return _as_dicts(result)

    # -------------------------------------------------------------- analysis
    def failure_category_counts(self, scope: Optional[AnalysisScope]) -> list[dict]:
        sql = """
            SELECT r.failure_category::text AS category, count(*) AS count
            FROM ride r JOIN city ci ON ci.city_id = r.city_id
            WHERE r.outcome = 'BASARISIZ_HARD' AND ci.is_test = false {scope}
            GROUP BY r.failure_category
        """
        return self._rows(sql, scope)

    def failure_criteria_counts(self, scope: Optional[AnalysisScope]) -> dict:
        sql = """
            SELECT
              count(*) FILTER (WHERE r.outcome = 'BASARISIZ_HARD') AS failed_total,
              count(*) FILTER (WHERE r.outcome = 'BASARILI')       AS success_total,
              count(*) FILTER (WHERE r.outcome = 'BASARISIZ_HARD'
                    AND r.duration_sec < 120 AND r.distance_m < 60) AS criteria_and_failed,
              count(*) FILTER (WHERE r.outcome = 'BASARILI'
                    AND r.duration_sec < 120 AND r.distance_m < 60) AS criteria_and_success,
              count(*) FILTER (WHERE r.distance_m IS NOT NULL
                    AND r.distance_m < 1 AND r.duration_sec > 120)  AS zero_distance_long_duration
            FROM ride r JOIN city ci ON ci.city_id = r.city_id
            WHERE ci.is_test = false {scope}
        """
        return self._rows(sql, scope)[0]

    def vehicle_failure_counts(self, scope: Optional[AnalysisScope], min_failures: int) -> list[dict]:
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
        return self._rows(sql, scope, {"min_failures": min_failures})

    def control_group_stats(self, scope: Optional[AnalysisScope]) -> list[dict]:
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
        c = self._rows(sql, scope)[0]
        return [
            {"group": "ariza_metinli", "total": c["text_total"], "healthy": c["text_healthy"]},
            {"group": "herhangi_bildirimli", "total": c["reported_total"], "healthy": c["reported_healthy"]},
            {"group": "bildirimsiz", "total": c["control_total"], "healthy": c["control_healthy"]},
        ]

    def false_fault_counts(self, scope: Optional[AnalysisScope]) -> list[dict]:
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
        return self._rows(sql, scope)

    def subregion_stats(self, scope: Optional[AnalysisScope], min_rides: int) -> list[dict]:
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
        return self._rows(sql, scope, {"min_rides": min_rides})

    def hour_region_counts(self, scope: Optional[AnalysisScope]) -> list[dict]:
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
        return self._rows(sql, scope)

    def ops_cost_rows(self, scope: Optional[AnalysisScope]) -> list[dict]:
        # ops_cost_model bilerek boştur; boşsa [] döner ve analiz TL raporlamaz.
        with self.engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT mission_type, labor_cost, fuel_cost, currency "
                    "FROM ops_cost_model"
                )
            )
            return _as_dicts(result)

    # ---------------------------------------------------------- classify (yaz)
    def classify_all(
        self,
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
            with self.engine.begin() as conn:
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

    # ------------------------------------------------------------ assess (yaz)
    def assess_all(
        self,
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
            "" if refresh else " AND (a.ride_id IS NULL OR a.verdict = 'DEGERLENDIRILEMEDI')"
        )
        timeline_sql = text(
            f"""
            WITH scoped AS (
                SELECT r.ride_id, r.start_time, r.end_time, r.vehicle_id, r.outcome,
                       r.distance_m, r.end_reason_id, r.end_message,
                       f.rating, f.comment_text
                FROM ride r
                JOIN city ci ON ci.city_id = r.city_id
                LEFT JOIN feedback f
                       ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
                WHERE ci.is_test = false {clause}
            ),
            seq AS (
                SELECT *,
                    LEAD(ride_id)    OVER w AS next_ride_id,
                    LEAD(start_time) OVER w AS next_start_time,
                    LEAD(outcome)    OVER w AS next_outcome,
                    LEAD(distance_m) OVER w AS next_distance_m
                FROM scoped
                WINDOW w AS (PARTITION BY vehicle_id ORDER BY start_time)
            )
            SELECT seq.* FROM seq{incremental_join}
            WHERE seq.outcome = 'BASARISIZ_HARD'{incremental_where}
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
        with self.engine.begin() as conn:
            rows = conn.execute(timeline_sql, sparams).mappings().all()
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
                    next_ride = Ride(
                        ride_id=row["next_ride_id"],
                        source_ref="",
                        vehicle_id=row["vehicle_id"],
                        city_id=0,
                        user_ref="",
                        start_time=row["next_start_time"],
                        outcome=RideOutcome(row["next_outcome"]),
                        distance_m=float(row["next_distance_m"])
                        if row["next_distance_m"] is not None
                        else None,
                    )
                a = assess_ride(
                    ride,
                    next_ride,
                    comment_text=row["comment_text"],
                    rating=row["rating"],
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

    # -------------------------------------------------------------- data_load
    def list_data_loads(self) -> list[dict]:
        """Yüklenen CSV'lerin denetim kaydı (en yeniden eskiye)."""
        with self.engine.connect() as conn:
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
