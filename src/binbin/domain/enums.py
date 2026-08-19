"""Python'un DB'den OKUDUĞU enum tipleri. Değerler `db/*.sql` tipleriyle birebir aynıdır.

⚠️ Üye listeleri TAM olmalı: kod DB string'ini doğrudan enum'a çevirir (`RideOutcome(row[...])`,
`_enum_or_none`). Şu an hiçbir kod yolunun üretmediği bir üyeyi (ör. RideOutcome.DEGRADED)
silmek, DB'de o değer varsa çalışma zamanında ValueError demektir — ölü görünse de silinmez.
Python'un hiç okumadığı tablolara ait tipler (vehicle_status, rule_type, enforcement_action)
ise burada TUTULMAZ; onların tek doğru kaynağı `db/*.sql`'dir.
"""

from enum import Enum


class RawRentalStatus(str, Enum):
    """CSV'den gelen ham kiralama statüsü. Magic string'leri (3,4) engellemek için."""

    SUCCESS = "3"
    FAILED_HARD = "4"


class RideOutcome(str, Enum):

    BASARILI = "BASARILI"
    BASARISIZ_HARD = "BASARISIZ_HARD"
    DEGRADED = "DEGRADED"
    IPTAL = "IPTAL"


class FailureCategory(str, Enum):

    TEKNIK = "TEKNIK"
    REGULASYON = "REGULASYON"
    KULLANICI = "KULLANICI"
    ODEME = "ODEME"
    SISTEM = "SISTEM"
    # Komşu sürüş kanıtı problemin tarafını gösterir, alt türünü değil.
    ARAC_TARAFI = "ARAC_TARAFI"
    KULLANICI_TARAFI = "KULLANICI_TARAFI"


class PaymentStatus(str, Enum):

    OK = "OK"
    DECLINED = "DECLINED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    PREAUTH_FAILED = "PREAUTH_FAILED"


class FailureReason(str, Enum):

    UNLOCK_ACK_TIMEOUT = "UNLOCK_ACK_TIMEOUT"
    GPS_NO_FIX = "GPS_NO_FIX"
    CONNECTION_LOST = "CONNECTION_LOST"
    IOT_FAULT = "IOT_FAULT"
    LOW_BATTERY = "LOW_BATTERY"
    BMS_FAULT = "BMS_FAULT"
    MOTOR_ERROR = "MOTOR_ERROR"
    LOCK_JAM = "LOCK_JAM"
    QR_SCAN_FAIL = "QR_SCAN_FAIL"
    BLE_PAIR_FAIL = "BLE_PAIR_FAIL"
    NO_RIDE_ZONE = "NO_RIDE_ZONE"
    SLOW_ZONE_THROTTLE = "SLOW_ZONE_THROTTLE"
    NO_PARK_BLOCK = "NO_PARK_BLOCK"
    OPERATING_HOUR_BLOCK = "OPERATING_HOUR_BLOCK"
    CITY_BOUNDARY_CUTOFF = "CITY_BOUNDARY_CUTOFF"
    USER_CANCELLED = "USER_CANCELLED"
    PARKING_PHOTO_FAIL = "PARKING_PHOTO_FAIL"
    PAYMENT_DECLINED = "PAYMENT_DECLINED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    PREAUTH_FAILED = "PREAUTH_FAILED"
    BACKEND_ERROR = "BACKEND_ERROR"


class ClassificationSource(str, Enum):
    """Sınıflandırma kanıtının kaynağı. ck_category_needs_source: kategori doluysa NONE olamaz.

    FIELD_SIGNAL : sürüş telemetrisi — mevcut CSV'de hepsi NULL, kod hazır ama üretilmiyor.
    REASON_CODE  : durum defterinden gelen, kural kitabında açık teknik arıza sayılan sinyal.
    NEIGHBOR_RIDE: aynı aracın/kullanıcının komşu sürüşünden çıkarılan kanıt.
    MAINTENANCE  : sürüşten sonraki 24 sa içinde açılan bakım kaydı.
    """

    FIELD_SIGNAL = "FIELD_SIGNAL"
    REASON_CODE = "REASON_CODE"
    TEXT_MESSAGE = "TEXT_MESSAGE"
    TEXT_COMMENT = "TEXT_COMMENT"
    NEIGHBOR_RIDE = "NEIGHBOR_RIDE"
    MAINTENANCE = "MAINTENANCE"
    NONE = "NONE"


class FaultVerdict(str, Enum):
    """Sahte arıza değerlendirme hükmü (DB: fault_verdict).

    "SAHTE" değil "ŞÜPHELİ": mevcut veri kesin hüküm veremez.
    """

    GERCEK_ARIZA_SUPHESI = "GERCEK_ARIZA_SUPHESI"
    SAHTE_ALARM_SUPHESI = "SAHTE_ALARM_SUPHESI"
    BILDIRIM_YOK = "BILDIRIM_YOK"
    DEGERLENDIRILEMEDI = "DEGERLENDIRILEMEDI"


class FalseFaultHypothesis(str, Enum):
    """Sahte alarm hipotezi (DB: false_fault_hypothesis).

    REGULASYON_SUPHESI yalnızca vehicle_moved=false AND healthy_proof=true
    satırlarına atanabilir (ck_regulation_hypothesis).
    """

    REGULASYON_SUPHESI = "REGULASYON_SUPHESI"
    GECICI_TEKNIK = "GECICI_TEKNIK"
    KULLANICI_HATASI = "KULLANICI_HATASI"
    BELIRSIZ = "BELIRSIZ"
