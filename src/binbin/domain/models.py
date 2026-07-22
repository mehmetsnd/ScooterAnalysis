"""Domain modelleri — saf veri taşıyıcıları (DTO/Entity).

Alan sırası DB ile birebir olmayabilir (dataclass: default'suz alanlar üstte) ama
içerik DB tablolarıyla eşleşir. Numeric(x,2) kolonları core'da float taşınır.

KAPSAM: yalnız fiilen KULLANILAN DTO'lar burada yaşar. Bir zamanlar tüm tablolar
için (Country, City, Vehicle, Feedback, DataLoad…) dataclass envanteri tutuluyordu;
hiçbirinin çalışma zamanında kullanıcısı yoktu ve DB şemasını ikinci kez —
senkron kalması elle sağlanan, hiçbir testin korumadığı biçimde — anlatıyorlardı.
Şemanın TEK doğru kaynağı `db/*.sql`'dir. Yeni bir DTO'ya gerçekten ihtiyaç
duyulduğunda o zaman eklenir.
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

