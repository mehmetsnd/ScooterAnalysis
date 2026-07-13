"""Ingest (ETL): ham CSV → PostgreSQL, psycopg COPY ile stream.

Akış (run_ingest): data_load aç (RUNNING) → staging TRUNCATE + COPY → aylık
partition'ları hazırla → referans tabloları + vehicle + ride + feedback (scope
filtreli) → data_load'ı SUCCESS/FAILED kapat. Bozuk satırlar (ör. end_time <
start_time) data_quality_flags ile işaretlenip yazılmaz.

Timezone kuralı: CSV'deki start/end DAİMA Europe/Istanbul kabul edilip timestamptz'e
çevrilir; ülke/bölge timezone'u burada dikkate ALINMAZ.

Pandas bilinçli olarak kullanılmaz: büyük CSV'yi RAM'e almak yerine COPY ile stream
ederiz — bellek sabit kalır, ingest hızlanır.
"""

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, text

from binbin.config import INGEST_LOCK_KEY, Scope
from binbin.data.engine import get_engine
from binbin.domain.enums import RawRentalStatus

_COPY_CHUNK = 1 << 20  # 1 MB

# Partition adı: CREATE TABLE'a giden tek dinamik identifier. int'ten türese de
# doğrulanır (SQL güvenlik sözleşmesi). \A…\Z tam çapa — `$` sondaki `\n`'i kaçırır.
_PARTITION_NAME_RE = re.compile(r"\Aride_\d{4}_\d{2}\Z")

# Ride'a uygun ham staging satırı — eligibility kuralı tek kaynak (aşağıda 3 sorgu paylaşır).
_STATUS_IN = (
    f"s.rental_status IN ('{RawRentalStatus.SUCCESS.value}','{RawRentalStatus.FAILED_HARD.value}')"
)
_ELIGIBLE_RAW = f"{_STATUS_IN} AND s.start_date_tr <> '' AND s.end_date_tr <> ''"


@dataclass
class IngestReport:
    """Ingest metrikleri ve sonucu. status: RUNNING | SUCCESS | FAILED | SKIPPED."""

    data_load_id: int
    file_name: str
    status: str = "RUNNING"
    rows_read: int = 0
    rows_eligible: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    rows_flagged: int = 0
    cities: int = 0
    sub_regions: int = 0
    end_reasons: int = 0
    warnings: list[str] = field(default_factory=list)


def list_source_csvs(data_dir: Path = Path("data_raw")) -> list[Path]:
    """`data_dir` içindeki `.csv` dosyalarını isme göre sıralı döner. Klasör yoksa hata."""
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Veri klasörü yok: {data_dir}")
    return sorted(data_dir.glob("*.csv"))


def copy_csv_to_staging(engine: Engine, csv_path: Path) -> int:
    """stg_rental_raw'ı TRUNCATE edip CSV'yi COPY ile içeri stream eder; yazılan satır sayısını döner."""
    raw = engine.raw_connection()
    try:
        dbapi = raw.driver_connection  # psycopg.Connection
        with dbapi.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            cur.execute("TRUNCATE stg_rental_raw")
            copy_sql = "COPY stg_rental_raw FROM STDIN WITH (FORMAT csv, HEADER true)"
            with cur.copy(copy_sql) as copy, open(csv_path, "rb") as f:
                while chunk := f.read(_COPY_CHUNK):
                    copy.write(chunk)
            cur.execute("SELECT count(*) FROM stg_rental_raw")
            n = cur.fetchone()[0]
        dbapi.commit()
        return int(n)
    finally:
        raw.close()


def _staging_scope_clause(scope: Scope) -> tuple[str, dict]:
    """Staging (ham metin) üzerinde ülke/şehir ADIYLA scope WHERE parçası.

    İsimler config'ten parametre olarak gelir; sorguya gömülmez. --all → boş.
    """
    if scope.is_unrestricted:
        return "", {}
    clause = ""
    params: dict = {}
    if scope.countries:
        clause += " AND s.country_name = ANY(:scope_countries)"
        params["scope_countries"] = list(scope.countries)
    if scope.cities:
        clause += " AND s.region_name = ANY(:scope_cities)"
        params["scope_cities"] = list(scope.cities)
    return clause, params


def _ensure_partitions(engine: Engine, scope_clause: str, params: dict) -> None:
    """Staging'deki min/max start_date_tr'den gereken aylık ride partition'larını oluşturur."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT min(start_date_tr::timestamp) AS lo, max(start_date_tr::timestamp) AS hi "
                "FROM stg_rental_raw s "
                f"WHERE {_STATUS_IN} {scope_clause}"
            ),
            params,
        ).one()
        lo, hi = row.lo, row.hi
        if lo is None or hi is None:
            return
        year, month = lo.year, lo.month
        while (year, month) <= (hi.year, hi.month):
            start = date(year, month, 1)
            ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
            end = date(ny, nm, 1)
            name = f"ride_{year:04d}_{month:02d}"
            if not _PARTITION_NAME_RE.match(name):
                # Ulaşılamaz olmalı (int-türevli); defense-in-depth.
                raise ValueError(f"Geçersiz partition adı: {name!r}")
            exists = conn.execute(
                text("SELECT to_regclass(:n)"), {"n": name}
            ).scalar()
            if exists is None:
                conn.execute(
                    text(
                        f"CREATE TABLE {name} PARTITION OF ride "
                        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                    )
                )
            year, month = ny, nm



def _insert_cities_and_regions(conn, clause: str, params: dict, report: IngestReport) -> None:
    res = conn.execute(
        text(
            f"""
            INSERT INTO city (country_id, source_region_id, name, is_test)
            SELECT co.country_id, d.region_id::int, d.region_name,
                   (d.region_name = 'Test')
            FROM (SELECT DISTINCT s.country_id, s.region_id, s.region_name
                  FROM stg_rental_raw s
                  WHERE s.region_id <> '' {clause}) d
            JOIN country co ON co.source_country_id = d.country_id::int
            ON CONFLICT (country_id, source_region_id) DO NOTHING
            """
        ),
        params,
    )
    report.cities = res.rowcount

    res = conn.execute(
        text(
            f"""
            INSERT INTO sub_region (city_id, source_sub_region_id)
            SELECT c.city_id, d.sub_region_id::int
            FROM (SELECT DISTINCT s.country_id, s.region_id, s.sub_region_id
                  FROM stg_rental_raw s
                  WHERE s.sub_region_id <> '' AND s.region_id <> '' {clause}) d
            JOIN country co ON co.source_country_id = d.country_id::int
            JOIN city c ON c.country_id = co.country_id
                       AND c.source_region_id = d.region_id::int
            ON CONFLICT (city_id, source_sub_region_id) DO NOTHING
            """
        ),
        params,
    )
    report.sub_regions = res.rowcount


def _insert_end_reasons(conn, clause: str, params: dict, report: IngestReport) -> None:
    # label NULL bırakılır: reason_id anlamları saha ekibince doğrulanana kadar tahmin yok.
    res = conn.execute(
        text(
            f"""
            INSERT INTO end_reason (reason_id)
            SELECT DISTINCT s.reason_id::int FROM stg_rental_raw s
            WHERE s.reason_id <> '' {clause}
            ON CONFLICT (reason_id) DO NOTHING
            """
        ),
        params,
    )
    report.end_reasons = res.rowcount


def _insert_vehicles(conn, clause: str, params: dict) -> None:
    conn.execute(
        text(
            f"""
            INSERT INTO vehicle (source_ref, external_code)
            SELECT s.vehicle_id, NULLIF(max(s.plate), '')
            FROM stg_rental_raw s
            WHERE s.vehicle_id <> '' {clause}
            GROUP BY s.vehicle_id
            ON CONFLICT (source_ref) DO NOTHING
            """
        ),
        params,
    )


def _insert_rides(conn, clause: str, params: dict, data_load_id: int) -> None:
    # Idempotent: (source_ref, start_time) çakışırsa atla. end_time < start_time olan satır yazılmaz.
    conn.execute(
        text(
            f"""
            WITH src AS (
                SELECT
                    s.rental_id, s.user_id, s.vehicle_id AS src_vehicle,
                    s.country_id, s.region_id, s.sub_region_id,
                    s.rental_status, s.reason_id, s.message,
                    s.gross_amount, s.currency,
                    (s.start_date_tr::timestamp AT TIME ZONE 'Europe/Istanbul') AS start_ts,
                    (s.end_date_tr::timestamp   AT TIME ZONE 'Europe/Istanbul') AS end_ts,
                    -- Mongo telemetri mesafesi kanoniktir. distance_meters/distance
                    -- alanlarına bilinçli olarak fallback yapılmaz.
                    NULLIF(s.mongo_distance_meters, '')::numeric AS dist_raw
                FROM stg_rental_raw s
                WHERE {_ELIGIBLE_RAW} {clause}
            )
            INSERT INTO ride (
                source_ref, vehicle_id, city_id, sub_region_id, user_ref,
                start_time, end_time, duration_sec, distance_m, outcome,
                end_reason_id, end_message, gross_amount, currency,
                data_quality_flags, data_load_id
            )
            SELECT
                src.rental_id, v.vehicle_id, c.city_id, sr.sub_region_id, src.user_id,
                src.start_ts, src.end_ts,
                EXTRACT(EPOCH FROM (src.end_ts - src.start_ts)),
                CASE WHEN src.dist_raw > 50000 THEN NULL ELSE src.dist_raw END,
                (CASE src.rental_status WHEN '{RawRentalStatus.SUCCESS.value}' THEN 'BASARILI'
                                        WHEN '{RawRentalStatus.FAILED_HARD.value}' THEN 'BASARISIZ_HARD' END)::ride_outcome,
                NULLIF(src.reason_id, '')::int,
                NULLIF(src.message, ''),
                NULLIF(src.gross_amount, '')::numeric,
                NULLIF(src.currency, ''),
                ARRAY_REMOVE(ARRAY[
                    CASE WHEN src.dist_raw > 50000 THEN 'DISTANCE_IMPLAUSIBLE' END,
                    CASE WHEN src.dist_raw IS NULL THEN 'DISTANCE_NULL' END,
                    CASE WHEN EXTRACT(EPOCH FROM (src.end_ts - src.start_ts)) > 21600
                         THEN 'DURATION_IMPLAUSIBLE' END,
                    CASE WHEN c.is_test THEN 'TEST_REGION' END
                ], NULL),
                :data_load_id
            FROM src
            JOIN country co ON co.source_country_id = src.country_id::int
            JOIN city c ON c.country_id = co.country_id
                       AND c.source_region_id = src.region_id::int
            JOIN vehicle v ON v.source_ref = src.src_vehicle
            LEFT JOIN sub_region sr ON sr.city_id = c.city_id
                   AND sr.source_sub_region_id = NULLIF(src.sub_region_id, '')::int
            WHERE src.end_ts >= src.start_ts
            ON CONFLICT (source_ref, start_time) DO NOTHING
            """
        ),
        {**params, "data_load_id": data_load_id},
    )


def _insert_feedback(conn, data_load_id: int) -> None:
    # Yalnız puan VEYA yorum dolu satırlar (DB constraint: en az biri zorunlu).
    conn.execute(
        text(
            """
            INSERT INTO feedback (ride_id, ride_start_time, rating, comment_text, created_at)
            SELECT r.ride_id, r.start_time,
                   NULLIF(s.ride_rating, '')::int,
                   NULLIF(s.ride_comment, ''),
                   (NULLIF(s.rating_created_at_tr, '')::timestamp
                        AT TIME ZONE 'Europe/Istanbul')
            FROM stg_rental_raw s
            JOIN ride r ON r.source_ref = s.rental_id AND r.data_load_id = :data_load_id
            WHERE NULLIF(s.ride_rating, '') IS NOT NULL
               OR NULLIF(s.ride_comment, '') IS NOT NULL
            ON CONFLICT (ride_id, ride_start_time) DO NOTHING
            """
        ),
        {"data_load_id": data_load_id},
    )


def transform_staging_to_ride(engine: Engine, scope: Scope, data_load_id: int) -> IngestReport:
    """stg_rental_raw → referans tabloları + vehicle + ride + feedback (scope-filtreli)."""
    clause, params = _staging_scope_clause(scope)
    report = IngestReport(data_load_id=data_load_id, file_name="")

    _ensure_partitions(engine, clause, params)

    with engine.begin() as conn:
        report.rows_read = conn.execute(
            text("SELECT count(*) FROM stg_rental_raw")
        ).scalar_one()

        # country tablosunda karşılığı olmayan ülkeler → satırlar atlanır, uyarı yazılır.
        unknown = conn.execute(
            text(
                "SELECT DISTINCT s.country_id, s.country_name FROM stg_rental_raw s "
                "LEFT JOIN country co ON co.source_country_id = s.country_id::int "
                f"WHERE s.country_id <> '' AND co.country_id IS NULL {clause}"
            ),
            params,
        ).all()
        for cid, cname in unknown:
            report.warnings.append(f"Bilinmeyen country_id={cid} ({cname}) — satırlar atlandı.")

        _insert_cities_and_regions(conn, clause, params, report)
        _insert_end_reasons(conn, clause, params, report)
        _insert_vehicles(conn, clause, params)
        _insert_rides(conn, clause, params, data_load_id)
        _insert_feedback(conn, data_load_id)

        # sayaçlar
        report.rows_eligible = conn.execute(
            text(f"SELECT count(*) FROM stg_rental_raw s WHERE {_ELIGIBLE_RAW} {clause}"),
            params,
        ).scalar_one()
        report.rows_inserted = conn.execute(
            text("SELECT count(*) FROM ride WHERE data_load_id = :id"),
            {"id": data_load_id},
        ).scalar_one()
        report.rows_flagged = conn.execute(
            text(
                "SELECT count(*) FROM ride "
                "WHERE data_load_id = :id AND cardinality(data_quality_flags) > 0"
            ),
            {"id": data_load_id},
        ).scalar_one()
        report.rows_skipped = report.rows_eligible - report.rows_inserted

        # ride_default DAİMA boş olmalı; doluysa aylık partition eksik demektir.
        default_rows = conn.execute(text("SELECT count(*) FROM ride_default")).scalar_one()
        if default_rows:
            raise RuntimeError(
                f"ride_default {default_rows} satır içeriyor — aylık partition eksik."
            )

    return report



@contextmanager
def _ingest_lock(engine: Engine):
    """Eşzamanlı ingest'lerin paylaşımlı stg_rental_raw'ı ezmesini engelleyen Postgres
    advisory lock. Session-level: commit/rollback'ten etkilenmez, yalnız unlock ya da
    bağlantı kapanınca serbest kalır (süreç çökse bile DB bırakır). Tek kullanıcıda hep boş.
    """
    conn = engine.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": INGEST_LOCK_KEY})
        conn.commit()
        yield
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": INGEST_LOCK_KEY})
            conn.commit()
        finally:
            conn.close()


def run_ingest(
    csv_path: Path,
    scope: Scope,
    engine: Engine | None = None,
    force: bool = False,
) -> IngestReport:
    """Uçtan uca ingest: guard → kilit → data_load aç → COPY → transform → kapat.

    Guard: aynı dosya daha önce SUCCESS ile yüklendiyse ve force yoksa, dosyayı tekrar
    okumadan SKIPPED döner. Yalnız SUCCESS bloklar; FAILED/RUNNING yeniden denenebilir.
    COPY + transform advisory lock altında serialize olur.
    """
    engine = engine if engine is not None else get_engine()
    file_bytes = csv_path.stat().st_size

    if not force:
        with engine.connect() as conn:
            prior = conn.execute(
                text(
                    "SELECT data_load_id, file_bytes, finished_at FROM data_load "
                    "WHERE file_name = :name AND status = 'SUCCESS' "
                    "ORDER BY data_load_id DESC LIMIT 1"
                ),
                {"name": csv_path.name},
            ).one_or_none()
        if prior is not None:
            report = IngestReport(
                data_load_id=prior.data_load_id,
                file_name=csv_path.name,
                status="SKIPPED",
            )
            report.warnings.append(
                f"'{csv_path.name}' zaten yüklü (data_load_id={prior.data_load_id}, "
                f"{prior.finished_at}). Yeniden yüklemek için --force."
            )
            if prior.file_bytes is not None and prior.file_bytes != file_bytes:
                report.warnings.append(
                    f"UYARI: dosya boyutu değişmiş ({prior.file_bytes} → {file_bytes}) "
                    "— içerik güncellenmişse --force kullan."
                )
            return report

    with _ingest_lock(engine):
        return _run_ingest_locked(engine, csv_path, scope, file_bytes)


def _run_ingest_locked(
    engine: Engine, csv_path: Path, scope: Scope, file_bytes: int
) -> IngestReport:
    """Kilit altındaki asıl yükleme: data_load aç → COPY → transform → kapat."""
    with engine.begin() as conn:
        data_load_id = conn.execute(
            text(
                "INSERT INTO data_load (file_name, file_bytes, status) "
                "VALUES (:name, :bytes, 'RUNNING') RETURNING data_load_id"
            ),
            {"name": csv_path.name, "bytes": file_bytes},
        ).scalar_one()

    try:
        copy_csv_to_staging(engine, csv_path)
        report = transform_staging_to_ride(engine, scope, data_load_id)
        report.file_name = csv_path.name
        report.status = "SUCCESS"
        with engine.begin() as conn:
            period = conn.execute(
                text(
                    "SELECT min(start_time)::date AS lo, max(start_time)::date AS hi "
                    "FROM ride WHERE data_load_id = :id"
                ),
                {"id": data_load_id},
            ).one()
            conn.execute(
                text(
                    """
                    UPDATE data_load SET
                        status = 'SUCCESS',
                        rows_read = :read, rows_inserted = :ins,
                        rows_skipped = :skip, rows_flagged = :flag,
                        period_start = :lo, period_end = :hi,
                        finished_at = now(),
                        notes = :notes
                    WHERE data_load_id = :id
                    """
                ),
                {
                    "read": report.rows_read,
                    "ins": report.rows_inserted,
                    "skip": report.rows_skipped,
                    "flag": report.rows_flagged,
                    "lo": period.lo,
                    "hi": period.hi,
                    "notes": "; ".join(report.warnings) or None,
                    "id": data_load_id,
                },
            )
        return report
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE data_load SET status = 'FAILED', finished_at = now(), "
                    "notes = :notes WHERE data_load_id = :id"
                ),
                {"notes": str(exc)[:2000], "id": data_load_id},
            )
        raise
