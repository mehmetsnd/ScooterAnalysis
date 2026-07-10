"""Domain (İş Alanı) Modelleri — Sadece saf veri (data) taşıyıcılarıdır (DTO/Entity mantığı).

Not: Dataclass kuralı gereği default değeri (varsayılan) olmayan alanlar yukarıda tanımlanır.
Bu yüzden field sıralaması veritabanındaki sırayla birebir aynı olmayabilir ama
içerdikleri veriler DB tablolarıyla birebir eşleşir.

Numeric(x,2) kolonlarını (süre, mesafe, para vb.) core katmanında `float` olarak taşıyoruz.
Zaten threshold'larımız (eşiklerimiz) float olduğu için analizlerde yeterli oluyor.
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

    country_id: int
    source_country_id: int
    name: str
    currency: str
    timezone: str
    iso_code: Optional[str] = None
    active: bool = True


@dataclass
class City:
    """Şehir verisi (DB: city). Doğal anahtarı (country_id, source_region_id).
    
    Not: region_id=8 ('Test') olan bölgeler gerçek sürüş değildir. 
    Analiz katmanında bu verileri her zaman filtreleyip drop ediyoruz.
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
    """Alt bölge (DB: sub_region). Doğal anahtarı (city_id, source_sub_region_id).
    
    Not: source_sub_region_id kendi başına unique (benzersiz) DEĞİLDİR 
    (örneğin 591 ID'si birden fazla bölgede geçebilir). Genelde Geofence (sınır) 
    bölgesi için proxy olarak kullanıyoruz.
    """

    sub_region_id: int
    city_id: int
    source_sub_region_id: int
    name: Optional[str] = None


@dataclass
class EndReason:
    """Sürüşü sonlandırma kodu (DB: end_reason). Anlamlarını şu an tam BİLMİYORUZ.
    
    Saha ekibi reason_id'lerin ne anlama geldiğini doğrulayana kadar
    label/category_hint gibi alanları NULL bırakıyoruz. Tahmin yürütmek yok.
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

    vehicle_id: int
    source_ref: str
    external_code: Optional[str] = None
    model: Optional[str] = None
    firmware_version: Optional[str] = None
    iot_box_id: Optional[str] = None
    status: VehicleStatus = VehicleStatus.AVAILABLE


@dataclass
class Regulation:
    """Şehir bazlı regülasyon kuralları (DB: regulation). Ceza tutarı para birimi lokasyona göre değişir."""

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
    """Sürüş verisi (DB: ride). Uygulamanın ana Aggregate Root'udur.
    
    ÖNEMLİ: Telemetri alanları (unlock_ack, connection_lost vb.) mevcut CSV'de 
    bulunmadığı için hepsi NULL gelir. İleride Lead'den bu dataları istersek 
    diye önden tasarlandı. Kod analizleri bu NULL durumlara karşı güvenli (Null-safe) olmalıdır.
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
    """Kullanıcı geri bildirimi (DB: feedback). Composite FK: (ride_id, ride_start_time).
    
    DB kısıtlaması (Constraint): Puan veya yorumdan en az biri dolu olmak zorundadır.
    """

    feedback_id: int
    ride_id: int
    ride_start_time: datetime
    rating: Optional[int] = None
    comment_text: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class DataLoad:
    """ETL süreçleri için veri yükleme audit (denetim) logu (DB: data_load)."""

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
