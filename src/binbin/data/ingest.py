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

import csv
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, text

from binbin.config import INGEST_LOCK_KEY, Scope
from binbin.data.engine import get_engine
from binbin.data.queries import _unknown_scope_message
from binbin.data.repository import UnknownScopeName
from binbin.domain.enums import RawRentalStatus

_COPY_CHUNK = 1 << 20  # 1 MB

# Staging tablosu allowlist'i — COPY/TRUNCATE'e giden tek dinamik identifier
# (table adı). Yalnız buradaki sabit literaller kabul edilir; istekten gelen
# string asla interpolate edilmez (SQL güvenlik sözleşmesi).
_STAGING_TABLES = frozenset(
    {"stg_rental_raw", "stg_status_raw", "stg_maintenance_raw", "stg_geo_raw"}
)

# Aylık partition'lı üst tablo allowlist'i — aynı sözleşme, partition DDL'i için.
_PARTITIONED_PARENTS = frozenset({"ride", "fleet_status_event", "maintenance_event"})


def _month_partition_name_re(prefix: str) -> re.Pattern[str]:
    """`{prefix}_YYYY_MM` biçimini tam çapalı doğrulayan regex üretir.

    \\A…\\Z tam çapa — `$` sondaki `\\n`'i kaçırır. Enjeksiyon/serbest metin reddedilir.
    """
    return re.compile(rf"\A{re.escape(prefix)}_\d{{4}}_\d{{2}}\Z")


# Partition adı: CREATE TABLE'a giden tek dinamik identifier. int'ten türese de
# doğrulanır (SQL güvenlik sözleşmesi).
_PARTITION_NAME_RE = _month_partition_name_re("ride")
_FLEET_STATUS_EVENT_PARTITION_NAME_RE = _month_partition_name_re("fleet_status_event")
_MAINTENANCE_PARTITION_NAME_RE = _month_partition_name_re("maintenance_event")

# Ride'a uygun ham staging satırı — eligibility kuralı tek kaynak (aşağıda 3 sorgu paylaşır).
_STATUS_IN = (
    f"s.rental_status IN ('{RawRentalStatus.SUCCESS.value}','{RawRentalStatus.FAILED_HARD.value}')"
)
_ELIGIBLE_RAW = f"{_STATUS_IN} AND s.start_date_tr <> '' AND s.end_date_tr <> ''"

# Şema sözleşmesi: staging tablolarının (db/01, db/06) kolon SIRASIYLA aynıdır.
# COPY konumsal çalıştığı için sıra da ad kadar bağlayıcıdır.
RIDES_COLUMNS: tuple[str, ...] = (
    "rental_id", "user_id", "vehicle_id", "plate", "vehicle_type_id",
    "country_id", "country_name", "region_id", "region_name", "sub_region_id",
    "rental_status", "status_label", "start_date_tr", "end_date_tr",
    "checkout_date_tr", "gross_amount", "net_amount", "total_discount_amount",
    "refund_total", "is_refunded", "currency", "reason_id", "message",
    "distance", "duration", "minute_fee", "start_fee", "insurance_fee",
    "is_rental_insuranced", "source_id", "device_id", "is_group_rental",
    "created_on_tr", "updated_on_tr", "mongo_distance_meters",
    "distance_meters", "distance_source", "rental_rate_id",
    "ride_rating", "ride_comment", "rating_created_at_tr",
)
STATUS_COLUMNS: tuple[str, ...] = (
    "id", "vehicle_id", "status_id", "status_reason_id",
    "previous_status_id", "previous_status_reason_id",
    "description", "created_by", "created_on",
)
MAINTENANCE_COLUMNS: tuple[str, ...] = (
    "bakim_id", "scooter_id", "vehicle_id", "plate",
    "haziran_2026_basarili_surus_sayisi", "region_id", "region_name",
    "damage_sub_type_id", "damage_sub_type_name",
    "bakim_durumu", "bakim_durumu_aciklamasi",
    "bakim_sonucu", "bakim_sonucu_aciklamasi",
    "ariza_bildirim_zamani_istanbul", "depo_giris_zamani_istanbul",
    "onarim_baslangic_zamani_istanbul", "parca_bekleme_zamani_istanbul",
    "bakim_zamani_istanbul", "sonuc_kontrol_zamani_istanbul",
    "warehouse_id", "parca_kalemi", "parca_adedi", "toplam_parca_maliyeti",
)
GEO_COLUMNS: tuple[str, ...] = (
    "rental_id", "user_id", "vehicle_id", "plate", "vehicle_type_id",
    "country_id", "country_name", "region_id", "region_name", "sub_region_id",
    "rental_status", "status_label", "start_date_tr", "end_date_tr",
    "distance", "duration", "mongo_match", "mongo_vehicle_id", "mongo_is_completed",
    "start_longitude", "start_latitude", "end_longitude", "end_latitude",
    "end_geofence_id", "end_geofence_name", "end_geofence_type",
    "end_geofence_area_type", "end_geofence_sqr_km", "end_geofence_match_count",
    "end_geofence_all_ids", "end_geofence_all_names", "end_geofence_all_types",
)
_EXPECTED_COLUMNS = {
    "rides": RIDES_COLUMNS, "status": STATUS_COLUMNS,
    "maintenance": MAINTENANCE_COLUMNS, "geo": GEO_COLUMNS,
}
# Tür tespiti imzası, kolon listelerinden TÜRETİLİR (elle yazılan kopya sapardı).
# `rental_id` tek başına ayırt edici; `id` değil, o yüzden status 3 kolon ister.
# Sürüş imzası 15 kolon: koordinat/geofence CSV'si ilk 14'ü paylaşır, 15.'de ayrışır.
_KIND_SIGNATURES = {
    "rides": RIDES_COLUMNS[:15], "status": STATUS_COLUMNS[:3],
    "maintenance": MAINTENANCE_COLUMNS[:3], "geo": GEO_COLUMNS[:15],
}


class SchemaContractError(ValueError):
    """CSV başlığı beklenen şemayla uyuşmuyor — veri yanlış kolona akabilirdi."""


class UnknownCsvKindError(ValueError):
    """Başlık hiçbir bilinen kaynağa uymuyor — dosyanın ingest'i YOK (hata değil).

    `SchemaContractError`'dan AYRI tutulur: "bu dosya bizim işimiz değil" ile
    "bu dosya bizim işimiz ama bozuk" farklı kararlar gerektirir. Tek `ValueError`
    yakalanırsa bozuk bir sürüş CSV'si sessizce ATLANDI diye raporlanır.
    """


def _read_header(csv_path: Path) -> list[str]:
    """Başlık satırını CSV kurallarıyla okur (tırnak, BOM, CRLF, baş/son boşluk).

    `utf-8-sig`: Excel'den geçen dosyalar BOM ekler ve bu bir şema ihlali değildir.
    `csv.reader`: naif `split(",")` tırnaklı başlıkları (`"rental_id",…`) bozardı.
    """
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            row = next(csv.reader(f), [])
    except (csv.Error, UnicodeDecodeError) as exc:
        # csv.Error, ValueError alt sınıfı değil: sarmalanmazsa CLI'ı atlar.
        raise SchemaContractError(f"{csv_path.name}: başlık okunamadı ({exc}).")
    return [c.strip() for c in row]


def detect_csv_kind(csv_path: Path) -> str:
    """CSV başlık satırından veri türünü ayırır: 'rides' | 'status'.

    Yalnız ilk satırı okur (büyük dosyada ucuz). Bilinmeyen başlık → UnknownCsvKindError.
    Tür TESPİTİ yapar; şema DOĞRULAMASI `validate_csv_header`'ın işidir.
    """
    header = _read_header(csv_path)
    for kind, signature in _KIND_SIGNATURES.items():
        if tuple(header[: len(signature)]) == signature:
            return kind
    raise UnknownCsvKindError(
        f"Bilinmeyen CSV türü: {csv_path.name} (başlık: {','.join(header)[:50]!r})"
    )


def group_source_csvs(files: list[Path]) -> tuple[dict[str, list[Path]], list[Path]]:
    """Dosyaları türe göre ayırır; ingest'i olmayan CSV'ler hata değil, `unknown` listesidir."""
    by_kind: dict[str, list[Path]] = {kind: [] for kind in _EXPECTED_COLUMNS}
    unknown: list[Path] = []
    for path in files:
        try:
            by_kind[detect_csv_kind(path)].append(path)
        except UnknownCsvKindError:
            # Yalnız "tür tanınmadı" atlanır. Okunamayan/bozuk başlık
            # SchemaContractError'dur ve YUKARI ÇIKAR (sessizce atlanmaz).
            unknown.append(path)
    return by_kind, unknown


def validate_csv_header(csv_path: Path, kind: str) -> None:
    """Başlığın TAMAMINI beklenen kolon listesiyle karşılaştırır; sapmada hata verir.

    Fazladan kolon da hatadır: COPY konumsal çalışır, staging tablosunda karşılığı
    olmayan sütun zaten "extra data after last expected column" ile reddedilir.
    Hata mesajı yeni kolonları adlarıyla sayar — eşleme kararı (ör. `distance` mi
    `mongo_distance_meters` mi) alan bilgisi gerektirir, tahmin edilmez.
    """
    expected = _EXPECTED_COLUMNS.get(kind)
    if expected is None:
        raise SchemaContractError(
            f"Bilinmeyen CSV türü {kind!r}; beklenen: {sorted(_EXPECTED_COLUMNS)}"
        )
    actual = _read_header(csv_path)
    if not actual or not actual[0]:
        raise SchemaContractError(f"{csv_path.name}: dosya boş veya başlık satırı yok.")
    # Ad kontrolü önce: ortadan silinen bir kolonda ilk sapmayı bildirmek,
    # "son sütun eksik" demekten çok daha bilgilendirici.
    for i, (name, got) in enumerate(zip(expected, actual), start=1):
        if got != name:
            raise SchemaContractError(
                f"{csv_path.name}: {i}. sütun {name!r} bekleniyordu, {got!r} geldi."
                " Kolon sırası değişmiş olabilir — veri yanlış kolonlara akardı."
            )
    if len(actual) < len(expected):
        raise SchemaContractError(
            f"{csv_path.name}: başlık {len(actual)} sütun, {len(expected)} bekleniyor;"
            f" ilk eksik: {expected[len(actual)]!r} ({len(actual) + 1}. sütun)"
        )
    extra = actual[len(expected):]
    if extra:
        named = ", ".join(c or "<adsız>" for c in extra)
        raise SchemaContractError(
            f"{csv_path.name}: {len(extra)} yeni kolon var, tabloya eşlenmemiş: {named}."
            " Yüklemeden önce staging + INSERT eşlemesi güncellenmeli (migration)."
        )


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


def copy_csv_to_staging(engine: Engine, csv_path: Path, table: str = "stg_rental_raw") -> int:
    """`table`'ı TRUNCATE edip CSV'yi COPY ile içeri stream eder; yazılan satır sayısını döner.

    `table` sabit allowlist'ten (`_STAGING_TABLES`) gelmelidir — SQL güvenlik sözleşmesi.
    Varsayılan `stg_rental_raw`, mevcut çağrı yerlerini değiştirmeden korur.
    """
    if table not in _STAGING_TABLES:
        raise ValueError(f"Bilinmeyen staging tablosu: {table!r}")
    raw = engine.raw_connection()
    try:
        dbapi = raw.driver_connection  # psycopg.Connection
        with dbapi.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            cur.execute(f"TRUNCATE {table}")
            copy_sql = f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)"
            with cur.copy(copy_sql) as copy, open(csv_path, "rb") as f:
                while chunk := f.read(_COPY_CHUNK):
                    copy.write(chunk)
            cur.execute(f"SELECT count(*) FROM {table}")
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


def ensure_month_partitions(
    engine: Engine,
    parent_table: str,
    partition_name_re: re.Pattern[str],
    bounds_sql: str,
    bounds_params: dict,
) -> None:
    """`bounds_sql`'in döndürdüğü (lo, hi) tarih aralığı için eksik aylık RANGE
    partition'ları oluşturur. `ride` ve `fleet_status_event` ingest yolları paylaşır.

    `parent_table` sabit allowlist'ten (`_PARTITIONED_PARENTS`) gelmelidir; `bounds_sql`
    çağıran tarafından hazırlanır (kendi uygunluk/scope filtresini kendi gömer) ve
    `min(...) AS lo, max(...) AS hi` döndürmelidir.
    """
    if parent_table not in _PARTITIONED_PARENTS:
        raise ValueError(f"Bilinmeyen partition'lı tablo: {parent_table!r}")
    with engine.begin() as conn:
        row = conn.execute(text(bounds_sql), bounds_params).one()
        lo, hi = row.lo, row.hi
        if lo is None or hi is None:
            return
        year, month = lo.year, lo.month
        while (year, month) <= (hi.year, hi.month):
            start = date(year, month, 1)
            ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
            end = date(ny, nm, 1)
            name = f"{parent_table}_{year:04d}_{month:02d}"
            if not partition_name_re.match(name):
                # Ulaşılamaz olmalı (int-türevli); defense-in-depth.
                raise ValueError(f"Geçersiz partition adı: {name!r}")
            exists = conn.execute(
                text("SELECT to_regclass(:n)"), {"n": name}
            ).scalar()
            if exists is None:
                conn.execute(
                    text(
                        f"CREATE TABLE {name} PARTITION OF {parent_table} "
                        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                    )
                )
            year, month = ny, nm


def _ensure_partitions(engine: Engine, scope_clause: str, params: dict) -> None:
    """Staging'deki min/max start_date_tr'den gereken aylık ride partition'larını oluşturur."""
    bounds_sql = (
        "SELECT min(start_date_tr::timestamp) AS lo, max(start_date_tr::timestamp) AS hi "
        "FROM stg_rental_raw s "
        f"WHERE {_STATUS_IN} {scope_clause}"
    )
    ensure_month_partitions(engine, "ride", _PARTITION_NAME_RE, bounds_sql, params)



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
                src.dist_raw,
                (CASE src.rental_status WHEN '{RawRentalStatus.SUCCESS.value}' THEN 'BASARILI'
                                        WHEN '{RawRentalStatus.FAILED_HARD.value}' THEN 'BASARISIZ_HARD' END)::ride_outcome,
                NULLIF(src.reason_id, '')::int,
                NULLIF(src.message, ''),
                NULLIF(src.gross_amount, '')::numeric,
                NULLIF(src.currency, ''),
                ARRAY_REMOVE(ARRAY[
                    -- Out-of-content: IoT/telemetri hatasi. Mesafe>20km VEYA sure>=6sa
                    -- olan surus gercek bir surus degildir; isaretlenir, analizde dislanir.
                    CASE WHEN src.dist_raw > 20000
                              OR EXTRACT(EPOCH FROM (src.end_ts - src.start_ts)) >= 21600
                         THEN 'OUT_OF_CONTENT' END,
                    CASE WHEN src.dist_raw IS NULL THEN 'DISTANCE_NULL' END,
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


def _assert_staging_scope_names(engine: Engine, scope: Scope) -> None:
    """Kapsam adları BU CSV'de gerçekten var mı? Yoksa hata.

    Ingest, analiz yolundan FARKLI eşleşir: `city`/`country` tabloları ilk yüklemede
    boş olabileceği için DB lookup kullanılamaz, ham staging metni (`country_name`,
    `region_name`) taranır. Guard olmazsa `_ensure_partitions` sessizce `return`
    ediyor (min/max NULL), hiçbir satır yazılmıyor ve ingest **SUCCESS/rows_read=0**
    raporluyor — dosya yanlış sanılıyor. Mesaj DB'nin değil BU DOSYANIN adlarını
    listeler: "bu CSV'de hiç Bursa yok" bilgisi, yazım hatasını yanlış dosyadan ayırır.
    """
    if scope.is_unrestricted:
        return
    with engine.connect() as conn:
        for kind, column, requested in (
            ("ülke", "country_name", scope.countries),
            ("şehir", "region_name", scope.cities),
        ):
            if not requested:
                continue
            available = [
                r[0]
                for r in conn.execute(
                    text(f"SELECT DISTINCT {column} FROM stg_rental_raw WHERE {column} <> ''")
                ).all()
            ]
            missing = [n for n in requested if n not in available]
            if missing:
                raise UnknownScopeName(
                    _unknown_scope_message(f"{kind} (CSV içinde)", missing, available)
                )


def transform_staging_to_ride(engine: Engine, scope: Scope, data_load_id: int) -> IngestReport:
    """stg_rental_raw → referans tabloları + vehicle + ride + feedback (scope-filtreli)."""
    _assert_staging_scope_names(engine, scope)
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


def _find_successful_load(engine: Engine, file_name: str):
    """Aynı dosya adıyla daha önce SUCCESS ile kapanmış bir `data_load` satırı var mı?

    Varsa (data_load_id, file_bytes, finished_at) satırını döner; yoksa None. `run_ingest`
    ve `run_status_ingest` aynı "zaten yüklü, --force ile yeniden yükle" sözleşmesini paylaşır.
    """
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT data_load_id, file_bytes, finished_at FROM data_load "
                "WHERE file_name = :name AND status = 'SUCCESS' "
                "ORDER BY data_load_id DESC LIMIT 1"
            ),
            {"name": file_name},
        ).one_or_none()


def _open_data_load(engine: Engine, file_name: str, file_bytes: int) -> int:
    """`data_load` satırını RUNNING durumunda açar, id'sini döner (ride/status ortak)."""
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO data_load (file_name, file_bytes, status) "
                "VALUES (:name, :bytes, 'RUNNING') RETURNING data_load_id"
            ),
            {"name": file_name, "bytes": file_bytes},
        ).scalar_one()


def _close_data_load_failed(engine: Engine, data_load_id: int, exc: Exception) -> None:
    """`data_load` satırını FAILED ile kapatır (ride/status ortak hata yolu)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE data_load SET status = 'FAILED', finished_at = now(), "
                "notes = :notes WHERE data_load_id = :id"
            ),
            {"notes": str(exc)[:2000], "id": data_load_id},
        )


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
        skipped = skip_report_if_already_loaded(engine, csv_path, file_bytes, IngestReport)
        if skipped is not None:
            return skipped

    with _ingest_lock(engine):
        return _run_ingest_locked(engine, csv_path, scope, file_bytes)


# Dönem sınırının okunacağı tablo → zaman kolonu. Identifier bind EDİLEMEZ; bu allowlist
# SQL güvenlik sözleşmesinin gereği (dışarıdan gelen string buraya asla giremez).
_LOAD_PERIOD_SOURCE = {
    "ride": "start_time",
    "fleet_status_event": "created_on",
    "maintenance_event": "reported_at",
    "ride_geo": "ride_start_time",
}


def skip_report_if_already_loaded(engine: Engine, csv_path: Path, file_bytes: int, report_cls):
    """Dosya daha önce SUCCESS ile yüklendiyse SKIPPED raporu, değilse None.

    İki ingest yolu (sürüş / durum defteri) bunu aynen paylaşır; uyarı metinleri
    kopyalanınca sessizce ayrışıyordu.
    """
    prior = _find_successful_load(engine, csv_path.name)
    if prior is None:
        return None
    report = report_cls(
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


# Yüklemeden sonra istatistik tazelenir; allowlist (SQL güvenlik sözleşmesi).
_ANALYZE_AFTER_LOAD = {
    "ride": ("ride", "feedback", "vehicle", "city", "sub_region", "end_reason"),
    "fleet_status_event": ("fleet_status_event", "vehicle"),
    "maintenance_event": ("maintenance_event", "damage_sub_type"),
    "ride_geo": ("ride_geo", "geofence"),
}


def _refresh_statistics(engine: Engine, table: str) -> None:
    """Toplu COPY sonrası ANALYZE; autovacuum'u beklemek bayat planlara yol açıyordu."""
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        for target in _ANALYZE_AFTER_LOAD[table]:
            conn.exec_driver_sql(f"ANALYZE {target}")


def close_data_load_success(engine: Engine, data_load_id: int, report, *, table: str) -> None:
    """data_load'ı SUCCESS'e kapatır; dönem sınırlarını yüklenen tablodan okur."""
    time_col = _LOAD_PERIOD_SOURCE[table]
    with engine.begin() as conn:
        period = conn.execute(
            text(
                f"SELECT min({time_col})::date AS lo, max({time_col})::date AS hi "
                f"FROM {table} WHERE data_load_id = :id"
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
                # Durum defteri raporunda bu alan yok — kalite bayrağı üretmiyor.
                "flag": getattr(report, "rows_flagged", 0),
                "lo": period.lo,
                "hi": period.hi,
                "notes": "; ".join(report.warnings) or None,
                "id": data_load_id,
            },
        )


def _run_ingest_locked(
    engine: Engine, csv_path: Path, scope: Scope, file_bytes: int
) -> IngestReport:
    """Kilit altındaki asıl yükleme: data_load aç → COPY → transform → kapat."""
    data_load_id = _open_data_load(engine, csv_path.name, file_bytes)

    try:
        copy_csv_to_staging(engine, csv_path)
        report = transform_staging_to_ride(engine, scope, data_load_id)
        report.file_name = csv_path.name
        report.status = "SUCCESS"
        close_data_load_success(engine, data_load_id, report, table="ride")
    except Exception as exc:
        _close_data_load_failed(engine, data_load_id, exc)
        raise
    # ANALYZE try'ın DIŞINDA: SUCCESS zaten COMMIT oldu. İçeride kalırsa bir istatistik
    # hatası, yüklenmiş bir dosyayı FAILED'a çevirir ve sonraki koşu onu baştan yükler.
    _refresh_statistics(engine, "ride")
    return report

# ------------------------------------------------------------------ ortak sarmalayıcı
def run_source_ingest(
    csv_path: Path,
    scope: Scope,
    *,
    staging_table: str,
    target_table: str,
    transform,
    report_cls,
    engine: Engine | None = None,
    force: bool = False,
):
    """Her kaynağın paylaştığı akış: guard → kilit → data_load aç → COPY → transform → kapat.

    Kaynağa özgü olan yalnız `transform`; sarmalayıcı kopyalanmaz.
    """
    engine = engine if engine is not None else get_engine()
    file_bytes = csv_path.stat().st_size
    if not force:
        skipped = skip_report_if_already_loaded(engine, csv_path, file_bytes, report_cls)
        if skipped is not None:
            return skipped
    with _ingest_lock(engine):
        data_load_id = _open_data_load(engine, csv_path.name, file_bytes)
        try:
            copy_csv_to_staging(engine, csv_path, table=staging_table)
            report = transform(engine, scope, data_load_id)
            report.file_name = csv_path.name
            report.status = "SUCCESS"
            close_data_load_success(engine, data_load_id, report, table=target_table)
        except Exception as exc:
            _close_data_load_failed(engine, data_load_id, exc)
            raise
        # ANALYZE try'ın DIŞINDA — gerekçe için bkz. ingest_rides_csv.
        _refresh_statistics(engine, target_table)
        return report


@dataclass
class SourceIngestReport:
    """Bakım/geo ingest metrikleri (sürüş raporunun aksine kalite bayrağı üretmezler)."""

    data_load_id: int
    file_name: str
    status: str = "RUNNING"
    rows_read: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ bakım geçmişi
def transform_staging_to_maintenance(
    engine: Engine, scope: Scope, data_load_id: int
) -> SourceIngestReport:
    """stg_maintenance_raw → damage_sub_type + maintenance_event. Kapsam YOK SAYILIR
    (staging'de sürüş kapsamıyla eşleşen bir şehir kolonu yok)."""
    ensure_month_partitions(
        engine,
        "maintenance_event",
        _MAINTENANCE_PARTITION_NAME_RE,
        "SELECT min(NULLIF(ariza_bildirim_zamani_istanbul, '')::timestamp)::date AS lo,"
        "       max(NULLIF(ariza_bildirim_zamani_istanbul, '')::timestamp)::date AS hi"
        "  FROM stg_maintenance_raw",
        {},
    )
    report = SourceIngestReport(data_load_id=data_load_id, file_name="")
    with engine.begin() as conn:
        report.rows_read = conn.execute(
            text("SELECT count(*) FROM stg_maintenance_raw")
        ).scalar_one()
        # Arıza tipi boyutu: kategori ATANMAZ, tip bazında atıf ölçümü geçemedi.
        conn.execute(
            text(
                """
                INSERT INTO damage_sub_type (damage_sub_type_id, name)
                SELECT DISTINCT s.damage_sub_type_id::int, s.damage_sub_type_name
                FROM stg_maintenance_raw s
                WHERE s.damage_sub_type_id <> ''
                ON CONFLICT (damage_sub_type_id) DO NOTHING
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO maintenance_event (
                    maintenance_id, vehicle_id, damage_sub_type_id, reported_at,
                    warehouse_entry_at, repaired_at, result_code, part_count, data_load_id
                )
                SELECT s.bakim_id::bigint, v.vehicle_id,
                       NULLIF(s.damage_sub_type_id, '')::int,
                       s.ariza_bildirim_zamani_istanbul::timestamp AT TIME ZONE 'Europe/Istanbul',
                       NULLIF(s.depo_giris_zamani_istanbul, '')::timestamp
                           AT TIME ZONE 'Europe/Istanbul',
                       NULLIF(s.bakim_zamani_istanbul, '')::timestamp
                           AT TIME ZONE 'Europe/Istanbul',
                       NULLIF(s.bakim_sonucu, '')::numeric::smallint,
                       NULLIF(s.parca_adedi, '')::int,
                       :load_id
                FROM stg_maintenance_raw s
                JOIN vehicle v ON v.source_ref = s.vehicle_id
                WHERE s.bakim_id <> '' AND s.ariza_bildirim_zamani_istanbul <> ''
                ON CONFLICT (maintenance_id, reported_at) DO NOTHING
                """
            ),
            {"load_id": data_load_id},
        )
        report.rows_inserted = conn.execute(
            text("SELECT count(*) FROM maintenance_event WHERE data_load_id = :id"),
            {"id": data_load_id},
        ).scalar_one()
    report.rows_skipped = report.rows_read - report.rows_inserted
    if report.rows_skipped:
        report.warnings.append(
            f"{report.rows_skipped} satır atlandı (bildirim zamanı boş veya araç eşleşmedi)."
        )
    return report


def run_maintenance_ingest(csv_path: Path, scope: Scope, engine=None, force=False):
    return run_source_ingest(
        csv_path, scope, staging_table="stg_maintenance_raw",
        target_table="maintenance_event", transform=transform_staging_to_maintenance,
        report_cls=SourceIngestReport, engine=engine, force=force,
    )


# ------------------------------------------------------------------ koordinat + geofence
def transform_staging_to_ride_geo(
    engine: Engine, scope: Scope, data_load_id: int
) -> SourceIngestReport:
    """stg_geo_raw → geofence + ride_geo. Yalnız DB'de KARŞILIĞI OLAN sürüşler yazılır
    (dosya sürüş tablosunun üstkümesi değil; %2,1'i eşleşmiyor)."""
    report = SourceIngestReport(data_load_id=data_load_id, file_name="")
    with engine.begin() as conn:
        report.rows_read = conn.execute(
            text("SELECT count(*) FROM stg_geo_raw")
        ).scalar_one()
        # area_type/type anlamı bilinmiyor → verified=false, kategori ATANMAZ.
        conn.execute(
            text(
                """
                INSERT INTO geofence (geofence_id, name, geofence_type, area_type, sqr_km)
                SELECT DISTINCT ON (s.end_geofence_id)
                       s.end_geofence_id::bigint, s.end_geofence_name,
                       NULLIF(s.end_geofence_type, '')::numeric::smallint,
                       NULLIF(s.end_geofence_area_type, '')::numeric::smallint,
                       NULLIF(s.end_geofence_sqr_km, '')::numeric
                FROM stg_geo_raw s
                WHERE s.end_geofence_id <> ''
                ORDER BY s.end_geofence_id
                ON CONFLICT (geofence_id) DO NOTHING
                """
            )
        )
        # Haversine SQL'de: earthdistance/cube eklentisine bağımlılık yaratmamak için.
        conn.execute(
            text(
                """
                INSERT INTO ride_geo (
                    ride_id, ride_start_time, start_lat, start_lon, end_lat, end_lon,
                    displacement_m, end_geofence_id, data_load_id
                )
                SELECT r.ride_id, r.start_time, g.slat, g.slon, g.elat, g.elon,
                       2 * 6371000 * asin(sqrt(
                           power(sin(radians(g.elat - g.slat) / 2), 2)
                           + cos(radians(g.slat)) * cos(radians(g.elat))
                             * power(sin(radians(g.elon - g.slon) / 2), 2))),
                       NULLIF(g.gid, '')::bigint, :load_id
                FROM (
                    SELECT s.rental_id,
                           NULLIF(s.start_latitude, '')::double precision  AS slat,
                           NULLIF(s.start_longitude, '')::double precision AS slon,
                           NULLIF(s.end_latitude, '')::double precision    AS elat,
                           NULLIF(s.end_longitude, '')::double precision   AS elon,
                           s.end_geofence_id AS gid
                    FROM stg_geo_raw s
                    WHERE s.rental_id <> ''
                ) g
                JOIN ride r ON r.source_ref = g.rental_id
                WHERE g.slat IS NOT NULL AND g.elat IS NOT NULL
                ON CONFLICT (ride_id, ride_start_time) DO NOTHING
                """
            ),
            {"load_id": data_load_id},
        )
        report.rows_inserted = conn.execute(
            text("SELECT count(*) FROM ride_geo WHERE data_load_id = :id"),
            {"id": data_load_id},
        ).scalar_one()
    report.rows_skipped = report.rows_read - report.rows_inserted
    if report.rows_skipped:
        report.warnings.append(
            f"{report.rows_skipped} satırın DB'de karşılığı yok veya koordinatı boş."
        )
    return report


def run_geo_ingest(csv_path: Path, scope: Scope, engine=None, force=False):
    return run_source_ingest(
        csv_path, scope, staging_table="stg_geo_raw", target_table="ride_geo",
        transform=transform_staging_to_ride_geo, report_cls=SourceIngestReport,
        engine=engine, force=force,
    )
