"""Domain modelleri — saf DTO'lar. Numeric(x,2) kolonları core'da float taşınır.

Yalnız fiilen KULLANILAN DTO'lar burada yaşar; tablo envanteri tutulmaz, şemanın tek
doğru kaynağı `db/*.sql`'dir.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from binbin.domain.enums import (
    ClassificationSource,
    FailureCategory,
    FailureReason,
    PaymentStatus,
    RideOutcome,
)


@dataclass
class Ride:
    """Sürüş verisi (DB: ride). Yalnız core'un OKUDUĞU alanları taşır — tam tablo aynası değil.

    Telemetri alanları mevcut CSV'de NULL gelir (classifier null-safe okur); DB'de var olup
    hiçbir kod yolunun okumadığı kolonlar (ack_latency_ms, gps_fix_ok…) burada TUTULMAZ.
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
    start_battery_pct: Optional[int] = None
    connection_lost: Optional[bool] = None
    motor_error_code: Optional[str] = None
    bms_error_code: Optional[str] = None
    user_cancelled: Optional[bool] = None
    payment_status: Optional[PaymentStatus] = None
    data_quality_flags: list[str] = field(default_factory=list)
    data_load_id: Optional[int] = None

