"""Ingest (ETL): araç durum-değişim CSV'si → PostgreSQL, `ingest.py` ile aynı desen.

Akış (run_status_ingest): guard → kilit → data_load aç (RUNNING) → stg_status_raw
TRUNCATE + COPY → aylık partition'ları hazırla (fleet_status_event) → eksik
vehicle'ları staging'den ekle (hiç sürülmemiş araçlar için) → fleet_status_event
insert (idempotent) → data_load'ı SUCCESS/FAILED kapat.

Ortak plumbing (advisory lock, data_load aç/kapa, staging COPY, aylık partition
oluşturma) `ingest.py`'den yeniden kullanılır — kopyalanmaz.
"""

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, text

from binbin.config import Scope
from binbin.data.engine import get_engine
from binbin.data.ingest import (
    _FLEET_STATUS_EVENT_PARTITION_NAME_RE,
    _close_data_load_failed,
    _find_successful_load,
    _ingest_lock,
    _open_data_load,
    copy_csv_to_staging,
    ensure_month_partitions,
)


@dataclass
class StatusIngestReport:
    """Durum-değişim ingest metrikleri. status: RUNNING | SUCCESS | FAILED | SKIPPED."""

    data_load_id: int
    file_name: str
    status: str = "RUNNING"
    rows_read: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    vehicles_created: int = 0
    warnings: list[str] = field(default_factory=list)


def _insert_missing_vehicles(conn) -> int:
    """Durum defterinde geçen ama hiç sürülmemiş (rides ingest'te oluşmamış) araçları ekler."""
    res = conn.execute(
        text(
            """
            INSERT INTO vehicle (source_ref)
            SELECT DISTINCT s.vehicle_id
            FROM stg_status_raw s
            WHERE s.vehicle_id <> ''
            ON CONFLICT (source_ref) DO NOTHING
            """
        )
    )
    return res.rowcount


def _insert_status_events(conn, data_load_id: int) -> None:
    # Idempotent: (event_id, created_on) PK çakışırsa atla — aynı dosya --force ile
    # yeniden yüklense de veri kopyalanmaz.
    conn.execute(
        text(
            """
            INSERT INTO fleet_status_event (
                event_id, vehicle_id, status_id, status_reason_id,
                previous_status_id, previous_status_reason_id,
                description, created_by, created_on, data_load_id
            )
            SELECT
                s.id::bigint, v.vehicle_id, s.status_id::smallint,
                NULLIF(s.status_reason_id, '')::smallint,
                NULLIF(s.previous_status_id, '')::smallint,
                NULLIF(s.previous_status_reason_id, '')::smallint,
                NULLIF(s.description, ''),
                s.created_by::smallint,
                s.created_on::timestamptz,
                :data_load_id
            FROM stg_status_raw s
            JOIN vehicle v ON v.source_ref = s.vehicle_id
            WHERE s.id <> '' AND s.vehicle_id <> '' AND s.status_id <> ''
              AND s.created_by <> '' AND s.created_on <> ''
            ON CONFLICT (event_id, created_on) DO NOTHING
            """
        ),
        {"data_load_id": data_load_id},
    )


def transform_staging_to_status_events(
    engine: Engine, scope: Scope, data_load_id: int
) -> StatusIngestReport:
    """stg_status_raw → eksik vehicle + fleet_status_event (idempotent).

    `scope` şu an filtre ÜRETMEZ (bilinçli): stg_status_raw'da ülke/şehir adı yok
    (yalnız vehicle_id + durum olayı), durum defteri filoyu bütün olarak temsil
    eder. Parametre yalnızca `run_ingest`/CLI ile aynı imza şeklini korumak ve
    ileride city bazlı filtreleme eklenirse geriye uyumlu kalmak için tutulur.
    """
    report = StatusIngestReport(data_load_id=data_load_id, file_name="")

    bounds_sql = "SELECT min(created_on::timestamptz)::date AS lo, max(created_on::timestamptz)::date AS hi FROM stg_status_raw WHERE created_on <> ''"
    ensure_month_partitions(
        engine, "fleet_status_event", _FLEET_STATUS_EVENT_PARTITION_NAME_RE, bounds_sql, {}
    )

    with engine.begin() as conn:
        report.rows_read = conn.execute(text("SELECT count(*) FROM stg_status_raw")).scalar_one()

        report.vehicles_created = _insert_missing_vehicles(conn)
        _insert_status_events(conn, data_load_id)

        # NOT (--force yolu): ON CONFLICT DO NOTHING mevcut satırların data_load_id'sini
        # KORUR, bu yüzden aynı dosya yeniden yüklendiğinde rows_inserted=0 ve
        # rows_skipped=rows_read raporlanır. Kozmetik — veri doğru, audit satırı yanıltıcı.
        report.rows_inserted = conn.execute(
            text("SELECT count(*) FROM fleet_status_event WHERE data_load_id = :id"),
            {"id": data_load_id},
        ).scalar_one()
        report.rows_skipped = report.rows_read - report.rows_inserted

        # fleet_status_event_default DAİMA boş olmalı; doluysa aylık partition eksik demektir.
        default_rows = conn.execute(
            text("SELECT count(*) FROM fleet_status_event_default")
        ).scalar_one()
        if default_rows:
            raise RuntimeError(
                f"fleet_status_event_default {default_rows} satır içeriyor — aylık partition eksik."
            )

    return report


def run_status_ingest(
    csv_path: Path,
    scope: Scope,
    engine: Engine | None = None,
    force: bool = False,
) -> StatusIngestReport:
    """Uçtan uca durum-değişim ingest'i. `run_ingest` (ingest.py) ile aynı sözleşme:

    guard (zaten SUCCESS ise SKIPPED, --force ile aşılır) → advisory lock ile
    serialize → data_load aç → COPY → transform → kapat.
    """
    engine = engine if engine is not None else get_engine()
    file_bytes = csv_path.stat().st_size

    if not force:
        prior = _find_successful_load(engine, csv_path.name)
        if prior is not None:
            report = StatusIngestReport(
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
        return _run_status_ingest_locked(engine, csv_path, scope, file_bytes)


def _run_status_ingest_locked(
    engine: Engine, csv_path: Path, scope: Scope, file_bytes: int
) -> StatusIngestReport:
    """Kilit altındaki asıl yükleme: data_load aç → COPY → transform → kapat."""
    data_load_id = _open_data_load(engine, csv_path.name, file_bytes)

    try:
        copy_csv_to_staging(engine, csv_path, table="stg_status_raw")
        report = transform_staging_to_status_events(engine, scope, data_load_id)
        report.file_name = csv_path.name
        report.status = "SUCCESS"
        with engine.begin() as conn:
            period = conn.execute(
                text(
                    "SELECT min(created_on)::date AS lo, max(created_on)::date AS hi "
                    "FROM fleet_status_event WHERE data_load_id = :id"
                ),
                {"id": data_load_id},
            ).one()
            conn.execute(
                text(
                    """
                    UPDATE data_load SET
                        status = 'SUCCESS',
                        rows_read = :read, rows_inserted = :ins,
                        rows_skipped = :skip, rows_flagged = 0,
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
                    "lo": period.lo,
                    "hi": period.hi,
                    "notes": "; ".join(report.warnings) or None,
                    "id": data_load_id,
                },
            )
        return report
    except Exception as exc:
        _close_data_load_failed(engine, data_load_id, exc)
        raise
