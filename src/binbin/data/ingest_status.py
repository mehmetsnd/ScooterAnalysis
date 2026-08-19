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
    ensure_month_partitions,
    run_source_ingest,
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
    """Uçtan uca durum-değişim ingest'i; akış `ingest.run_source_ingest`'te ortaktır."""
    return run_source_ingest(
        csv_path, scope, staging_table="stg_status_raw",
        target_table="fleet_status_event", transform=transform_staging_to_status_events,
        report_cls=StatusIngestReport, engine=engine, force=force,
    )
