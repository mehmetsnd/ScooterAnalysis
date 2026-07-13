"""SQLAlchemy tablo tanımları — şemayla birebir (tek şema kaynağı, Python tarafı).

Bu modül YALNIZCA Table() metadata'sı tutar; `create_all`/DROP/migration YAPILMAZ.
Gerçek şema `db/01_reset_ve_kurulum.sql` + `db/02_false_fault.sql` ile PostgreSQL'de
kurulur (partition'lar, view'ler, CHECK'ler, enum tipleri orada). Buradaki tanımlar
Python'dan INSERT/SELECT ve dokümantasyon içindir; enum kolonları String olarak yeterli.

Sorgular (queries.py) ağırlıklı raw `text()` SQL kullandığından bu Table nesneleri
şu an çoğunlukla referans/dokümantasyon amaçlıdır — ama şemanın Python'daki TEK
envanteridir, bu yüzden `false_fault_assessment` ve `ops_cost_model` da burada beyan edilir.
"""

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

metadata = MetaData()

# --- Coğrafi hiyerarşi + referans tabloları --------------------------------
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

# --- Ana tablo: ride (DB'de aylık partition'lı → PK/FK bileşik) -------------
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

# --- Sahte arıza modülü (db/02_false_fault.sql) ----------------------------
# Türetilmiş değerlendirme + parametrik maliyet modeli. Sorgular bu tabloları
# okur (control_group_stats, false_fault_counts, subregion_stats, ops_cost_rows).
false_fault_assessment_table = Table(
    "false_fault_assessment", metadata,
    Column("ride_id", Integer, primary_key=True),
    Column("ride_start_time", DateTime(timezone=True), primary_key=True),
    Column("fault_reported", Boolean, nullable=False),
    Column("report_evidence", String, nullable=False),
    Column("vehicle_moved", Boolean),
    Column("next_ride_id", Integer),
    Column("next_ride_start_time", DateTime(timezone=True)),
    Column("next_ride_gap_min", Numeric(10, 2)),
    Column("next_ride_ok", Boolean),
    Column("next_ride_distance_m", Numeric(12, 2)),
    Column("healthy_proof", Boolean, nullable=False),
    Column("verdict", String, nullable=False),
    Column("hypothesis", String, nullable=False),
    Column("ops_pickup_task_id", String),
    Column("ops_workshop_task_id", String),
    Column("ops_redeploy_task_id", String),
    Column("wasted_missions", Integer, nullable=False),
    Column("assessed_at", DateTime(timezone=True), nullable=False),
    Column("assessor_version", String, nullable=False),
)

ops_cost_model_table = Table(
    "ops_cost_model", metadata,
    Column("cost_model_id", Integer, primary_key=True),
    Column("country_id", Integer),
    Column("city_id", Integer),
    Column("mission_type", String, nullable=False),
    Column("labor_cost", Numeric(12, 2)),
    Column("fuel_cost", Numeric(12, 2)),
    Column("currency", String, nullable=False),
    Column("avg_minutes", Integer),
    Column("opportunity_cost", Numeric(12, 2)),
    Column("effective_from", Date, nullable=False),
    Column("source_note", Text),
)
