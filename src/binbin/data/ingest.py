"""Ingest (ETL) Süreci — Ham CSV -> PostgreSQL (COPY komutuyla).

Süreç (run_ingest):
  0. `data_load` tablosunda yeni bir satır açıyoruz (RUNNING).
  1. `stg_rental_raw` (staging) tablosunu TRUNCATE edip, devasa CSV dosyasını RAM'i şişirmeden
     doğrudan PostgreSQL'in COPY metoduyla içeri akıtıyoruz (stream).
  2. Gerekli aylık partition'ları (parçaları) dinamik oluşturuyoruz.
  3. city / sub_region / end_reason gibi yan (referans) tabloları scope dahilinde dinamik dolduruyoruz.
  4. Vehicle (Araç) UPSERT ve ardından Ride (Sürüş) INSERT yapıyoruz.
  5. Sürüşle beraber puan/yorum varsa Feedback tablosuna basıyoruz.
  6. Bozuk datalar (Örn: end_time < start_time) data_quality_flags ile flag'lenip SKIP edilir.
  7. İşlem bitince `data_load` tablosundaki state'i SUCCESS veya FAILED olarak update edip raporluyoruz.

Önemli Not (Timezone Bug Önlemi):
CSV'den gelen `start_date_tr` her zaman Europe/Istanbul (UTC+3) kabul edilip direkt 
timestamptz'e çevrilir. Ülkenin veya bölgenin timezone'u burada dikkate ALINMAZ, kural böyledir.

Performans Notu: Python dünyasında genelde Pandas (df) kullanılır ama bu modül bilerek PANDAS KULLANMAZ!
Sebebi basit: 300+ MB CSV'yi RAM'e almak yerine psycopg `copy()` ile stream ederek veritabanına basıyoruz. 
Böylece çok daha az RAM tüketiyor ve çok daha hızlı çalışıyor.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, text

from binbin.config import Scope
from binbin.data.postgres_repo import build_engine
from binbin.domain.enums import RawRentalStatus

_COPY_CHUNK = 1 << 20  # 1 MB


@dataclass
class IngestReport:
    """Ingest (ETL) işleminin metriklerini ve sonucunu tutan sınıf. 
    State yönetimi: RUNNING | SUCCESS | FAILED | SKIPPED
    """

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
    """`data_dir` (default: data_raw) içindeki tüm `.csv` dosyalarını bulup isme göre sıralar.
    
    Not: Bu pure bir fonksiyondur, I/O prompt'u sormaz. Sadece data döner.
    Klasör yoksa patlar (Exception fırlatır).
    """
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Veri klasörü yok: {data_dir}")
    return sorted(data_dir.glob("*.csv"))


def find_source_csv(data_dir: Path = Path("data_raw")) -> Path:
    """`data_dir` içindeki TEK `.csv` dosyasını bulur (isminin ne olduğu önemli değil).
    
    Eğer klasörde 2 veya daha fazla CSV varsa hangisini alacağını bilemediği için hata patlatır.
    Çoklu dosya seçimi için CLI'daki `select_csv` metodunu kullanıyoruz.
    """
    csvs = list_source_csvs(data_dir)
    if not csvs:
        raise FileNotFoundError(f"{data_dir} içinde .csv bulunamadı.")
    if len(csvs) > 1:
        names = ", ".join(p.name for p in csvs)
        raise ValueError(f"{data_dir} içinde birden fazla .csv var, tekini bırakın: {names}")
    return csvs[0]


def copy_csv_to_staging(engine: Engine, csv_path: Path) -> int:
    """Önceki stg_rental_raw tablosunu TRUNCATE edip (uçurup), CSV'yi psycopg2 COPY metoduyla içeri basar. 
    Döndüğü değer: DB'ye stream edilen satır sayısı.
    """
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
                f"WHERE s.rental_status IN ('{RawRentalStatus.SUCCESS.value}','{RawRentalStatus.FAILED_HARD.value}') {{scope_clause}}"
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
    # --- city (country_id, source_region_id) ---
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

    # --- sub_region (city_id, source_sub_region_id) ---
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
    # --- end_reason (distinct reason_id, label NULL) ---
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
    # --- vehicle (source_ref = vehicle_id, external_code = plate) ---
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
    # --- ride (idempotent, scope-filtreli, timestamp UTC'ye) ---
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
                    NULLIF(s.distance_meters, '')::numeric AS dist_raw
                FROM stg_rental_raw s
                WHERE s.rental_status IN ('{RawRentalStatus.SUCCESS.value}','{RawRentalStatus.FAILED_HARD.value}')
                  AND s.start_date_tr <> '' AND s.end_date_tr <> '' {clause}
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
    # --- feedback (puan veya yorum doluysa) ---
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
    """stg_rental_raw → referans tabloları + vehicle + ride + feedback (scope-filtreli).

    Not: task'taki (engine, scope) imzası, ride.data_load_id referansı için
    `data_load_id` ile genişletildi (data_load satırı run_ingest'te açılır).
    """
    clause, params = _staging_scope_clause(scope)
    report = IngestReport(data_load_id=data_load_id, file_name="")

    _ensure_partitions(engine, clause, params)

    with engine.begin() as conn:
        # rows_read
        report.rows_read = conn.execute(
            text("SELECT count(*) FROM stg_rental_raw")
        ).scalar_one()

        # Bilinmeyen ülke uyarısı (kapsam içi)
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

        # --- sayaçlar ---
        report.rows_eligible = conn.execute(
            text(
                "SELECT count(*) FROM stg_rental_raw s "
                f"WHERE s.rental_status IN ('{RawRentalStatus.SUCCESS.value}','{RawRentalStatus.FAILED_HARD.value}') "
                "AND s.start_date_tr <> '' AND s.end_date_tr <> '' " + clause
            ),
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

        # --- partition doğrulama: ride_default DAİMA boş olmalı ---
        default_rows = conn.execute(text("SELECT count(*) FROM ride_default")).scalar_one()
        if default_rows:
            raise RuntimeError(
                f"ride_default {default_rows} satır içeriyor — aylık partition eksik."
            )

    return report



def run_ingest(
    csv_path: Path,
    scope: Scope,
    engine: Engine | None = None,
    force: bool = False,
) -> IngestReport:
    """Uçtan uca ingest: guard → data_load aç → COPY → transform → data_load kapat.

    Guard: bu dosya (`file_name`) ile daha önce `SUCCESS` yükleme varsa ve `force`
    değilse 327 MB'ı tekrar okumadan SKIPPED döner (idempotent israfı önler).
    Yalnız SUCCESS bloklar; FAILED/RUNNING yeniden denenebilir.
    """
    engine = engine if engine is not None else build_engine()
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
