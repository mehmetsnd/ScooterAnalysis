"""Projedeki tüm sabitler ve Enum'lar. 
Not: Buradaki değerler, DB şemasındaki (01_reset_ve_kurulum.sql vb.) tiplerle birebir aynıdır.
Veritabanına yazarken veya okurken string eşleşmesi için burası referans alınır.
"""

from enum import Enum


class VehicleStatus(str, Enum):

    AVAILABLE = "AVAILABLE"
    ON_TRIP = "ON_TRIP"
    REMOVED = "REMOVED"
    MAINTENANCE = "MAINTENANCE"


class RuleType(str, Enum):

    NO_RIDE = "NO_RIDE"
    SLOW_ZONE = "SLOW_ZONE"
    NO_PARKING = "NO_PARKING"
    MANDATORY_PARKING = "MANDATORY_PARKING"
    OPERATING_HOUR = "OPERATING_HOUR"
    CITY_BOUNDARY = "CITY_BOUNDARY"
    SPEED_LIMIT = "SPEED_LIMIT"


class EnforcementAction(str, Enum):

    MOTOR_CUTOFF = "MOTOR_CUTOFF"
    SPEED_THROTTLE = "SPEED_THROTTLE"
    BLOCK_END_RIDE = "BLOCK_END_RIDE"
    BLOCK_START = "BLOCK_START"
    AUDIBLE_WARNING = "AUDIBLE_WARNING"


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
    """Sınıflandırma kanıtının kaynağı (DB: classification_source).

    ck_category_needs_source: failure_category NULL DEĞİLSE source NONE olamaz.
    REASON_CODE ileriye dönüktür — reason_id anlamları (end_reason.category_hint)
    doğrulanana kadar classifier bu kaynağı üretmez.
    """

    FIELD_SIGNAL = "FIELD_SIGNAL"
    REASON_CODE = "REASON_CODE"
    TEXT_MESSAGE = "TEXT_MESSAGE"
    TEXT_COMMENT = "TEXT_COMMENT"
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
