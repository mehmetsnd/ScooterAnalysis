"""Domain modelleri — saf veri taşıyıcıları, DB şemasındaki tablolarla birebir.

Not: dataclass kuralı gereği varsayılansız alanlar önce gelir; bu yüzden alan
sırası şemadan sapabilir ama alan KÜMESİ şemayla birebir aynıdır.

numeric(x,2) kolonları (duration_sec, distance_m, gross_amount) core'da `float`
olarak taşınır — analiz karşılaştırmaları için yeterli, eşikler zaten float.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from binbin.domain.enums import (
    ClassificationSource,
    EnforcementAction,
    FailureCategory,
    FailureReason,
    PaymentStatus,
    RideOutcome,
    RuleType,
    VehicleStatus,
)


@dataclass
class Country:
    """Ülke (DB: country). Seed'li: Türkiye/Bosna/K.Makedonya."""

    country_id: int
    source_country_id: int
    name: str
    currency: str
    timezone: str
    iso_code: Optional[str] = None
    active: bool = True


@dataclass
class City:
    """Şehir (DB: city). Doğal anahtar (country_id, source_region_id).

    is_test: region_id=8 ('Test') gerçek sürüş değildir; analizler daima dışlar.
    """

    city_id: int
    country_id: int
    source_region_id: int
    name: str
    admin_authority: Optional[str] = None
    is_test: bool = False
    active: bool = True


@dataclass
class SubRegion:
    """Alt bölge (DB: sub_region). Doğal anahtar (city_id, source_sub_region_id).

    source_sub_region_id TEK BAŞINA benzersiz DEĞİLDİR (591/599/605/623 birden
    fazla bölgede geçer). Geofence bölgesi için mekânsal proxy.
    """

    sub_region_id: int
    city_id: int
    source_sub_region_id: int
    name: Optional[str] = None


@dataclass
class EndReason:
    """Sürüş sonlandırma kodu (DB: end_reason). Anlamları BİLİNMİYOR.

    label/category_hint/reason_hint saha ekibi doğrulayana kadar NULL kalır;
    kod anlamı TAHMİN EDİLMEZ.
    """

    reason_id: int
    label: Optional[str] = None
    category_hint: Optional[FailureCategory] = None
    reason_hint: Optional[FailureReason] = None
    verified: bool = False
    first_seen_at: Optional[datetime] = None
    notes: Optional[str] = None


@dataclass
class Vehicle:
    """Araç / skuter (DB: vehicle)."""

    vehicle_id: int
    source_ref: str
    external_code: Optional[str] = None
    model: Optional[str] = None
    firmware_version: Optional[str] = None
    iot_box_id: Optional[str] = None
    status: VehicleStatus = VehicleStatus.AVAILABLE


@dataclass
class Regulation:
    """Şehir regülasyonu (DB: regulation). Ceza tutarı ülkeye göre para birimi değiştirir."""

    regulation_id: int
    city_id: int
    rule_type: RuleType
    enforcement_action: EnforcementAction
    active: bool
    sub_region_id: Optional[int] = None
    zone_name: Optional[str] = None
    speed_limit_kmh: Optional[int] = None
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    fine_amount: Optional[float] = None
    fine_currency: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    source_ref: Optional[str] = None


@dataclass
class Ride:
    """Sürüş kaydı — sürüş başına indirgenmiş sinyaller (DB: ride).

    Telemetri alanları (unlock_ack, connection_lost, motor_error_code, ...) mevcut
    CSV'de YOK → hepsi NULL. Sınıflandırma/analiz kodu NULL'a dayanıklı olmalıdır.
    """

    ride_id: int
    source_ref: str
    vehicle_id: int
    city_id: int
    user_ref: str
    start_time: datetime
    outcome: RideOutcome
    sub_region_id: Optional[int] = None
    triggered_regulation_id: Optional[int] = None
    end_time: Optional[datetime] = None
    duration_sec: Optional[float] = None
    distance_m: Optional[float] = None
    failure_category: Optional[FailureCategory] = None
    failure_reason: Optional[FailureReason] = None
    classification_source: ClassificationSource = ClassificationSource.NONE
    classified_at: Optional[datetime] = None
    classifier_version: Optional[str] = None
    end_reason_id: Optional[int] = None
    end_message: Optional[str] = None
    gross_amount: Optional[float] = None
    currency: Optional[str] = None
    # Telemetri — CSV'de yok, NULL. Kod NULL-güvenli olmalı.
    unlock_ack: Optional[bool] = None
    ack_latency_ms: Optional[int] = None
    start_battery_pct: Optional[int] = None
    connection_lost: Optional[bool] = None
    gps_fix_ok: Optional[bool] = None
    motor_error_code: Optional[str] = None
    bms_error_code: Optional[str] = None
    lock_state_ok: Optional[bool] = None
    parking_photo_ok: Optional[bool] = None
    user_cancelled: Optional[bool] = None
    payment_status: Optional[PaymentStatus] = None
    data_quality_flags: list[str] = field(default_factory=list)
    data_load_id: Optional[int] = None
    ingested_at: Optional[datetime] = None


@dataclass
class Feedback:
    """Sürüş geri bildirimi (DB: feedback). FK bileşik: (ride_id, ride_start_time).

    Puan veya yorumdan en az biri dolu olmalıdır (ck_feedback_not_empty).
    """

    feedback_id: int
    ride_id: int
    ride_start_time: datetime
    rating: Optional[int] = None
    comment_text: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class DataLoad:
    """Veri yükleme denetim satırı (DB: data_load)."""

    data_load_id: int
    file_name: str
    status: str = "RUNNING"
    file_bytes: Optional[int] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    rows_read: Optional[int] = None
    rows_inserted: Optional[int] = None
    rows_skipped: Optional[int] = None
    rows_flagged: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    notes: Optional[str] = None
