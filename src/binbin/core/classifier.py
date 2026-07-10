"""Sürüş sınıflandırıcı (Classifier v1) — functional core: SAF, I/O yok.

TEK sürüşe bakar. Yalnızca `outcome == BASARISIZ_HARD` sürüşlere kategori atar.

Öncelik (ilk eşleşen kazanır) — gerekçesiyle:
  1. payment_status dolu & ≠ OK       → ODEME       (FIELD_SIGNAL)
  2. user_cancelled is True           → KULLANICI   (FIELD_SIGNAL)
  3. triggered_regulation_id dolu     → REGULASYON  (FIELD_SIGNAL)
  4. unlock_ack is False              → TEKNIK      (FIELD_SIGNAL)
  5. connection_lost / motor / bms /  → TEKNIK      (FIELD_SIGNAL)
     düşük batarya
  6. end_message metni → keywords     → (TEXT_MESSAGE)
  7. comment_text → keywords          → (TEXT_COMMENT)
  8. hiçbiri                          → (None, None, NONE)

Adımlar 1–5 mevcut CSV'de hep NULL (telemetri yok); ileriye dönüktür ve kod
NULL'a dayanıklıdır. Bugün sınıflandırma fiilen 6–7 (metin) üzerinden yürür.

MUTLAK KURAL: sinyalsiz sürüşe kategori UYDURULMAZ. `BILINMIYOR` yoktur → None.
Başarısızların ~%89'u bu kütledir; analiz katmanı bunları davranışsal çıkarımla
ele alır (bkz. core/false_fault.py, core/analysis.py).

REASON_CODE kaynağı bilinçli olarak üretilmez: reason_id anlamları BİLİNMİYOR
(end_reason.category_hint doğrulanmamış). Hint'ler doğrulanınca ayrı bir yol açılır.
"""

from typing import NamedTuple, Optional

from binbin.core import keywords
from binbin.domain.enums import (
    ClassificationSource,
    FailureCategory,
    FailureReason,
    PaymentStatus,
    RideOutcome,
)
from binbin.domain.models import Ride

# Batarya bu eşiğin altındaysa teknik (düşük batarya) sayılır.
LOW_BATTERY_PCT = 5

# payment_status → alt sebep eşlemesi (ODEME kategorisi için)
_PAYMENT_REASON = {
    PaymentStatus.DECLINED: FailureReason.PAYMENT_DECLINED,
    PaymentStatus.INSUFFICIENT_BALANCE: FailureReason.INSUFFICIENT_BALANCE,
    PaymentStatus.PREAUTH_FAILED: FailureReason.PREAUTH_FAILED,
}


class ClassificationResult(NamedTuple):
    """(kategori, alt sebep, kaynak). Tuple gibi karşılaştırılabilir."""

    category: Optional[FailureCategory]
    reason: Optional[FailureReason]
    source: ClassificationSource


_NONE_RESULT = ClassificationResult(None, None, ClassificationSource.NONE)


def _classify_text(text: str, source: ClassificationSource) -> Optional[ClassificationResult]:
    """Serbest metni kategoriye eşler. Metin içi öncelik:

    REGULASYON > KULLANICI > SISTEM > TEKNIK
    (çakışırsa regülasyon kazanır: "yasak bölge" + "gitmiyor" birlikte geçebilir.)

    Eşleşme yoksa None.
    """
    if keywords.contains_any(text, keywords.REGULATION_KEYWORDS):
        return ClassificationResult(
            FailureCategory.REGULASYON, FailureReason.NO_RIDE_ZONE, source
        )
    if keywords.contains_any(text, keywords.USER_KEYWORDS):
        return ClassificationResult(
            FailureCategory.KULLANICI, FailureReason.USER_CANCELLED, source
        )
    if keywords.contains_any(text, keywords.SYSTEM_KEYWORDS):
        return ClassificationResult(
            FailureCategory.SISTEM, FailureReason.BACKEND_ERROR, source
        )
    if keywords.contains_any(text, keywords.TECHNICAL_KEYWORDS):
        return ClassificationResult(FailureCategory.TEKNIK, _technical_reason(text), source)
    return None


def _technical_reason(text: str) -> FailureReason:
    """Teknik metinden alt sebep: kilit → LOCK_JAM, gaz/motor → MOTOR_ERROR, diğer → IOT_FAULT."""
    if keywords.contains_any(text, keywords.LOCK_KEYWORDS):
        return FailureReason.LOCK_JAM
    if keywords.contains_any(text, keywords.MOTOR_KEYWORDS):
        return FailureReason.MOTOR_ERROR
    return FailureReason.IOT_FAULT


def classify_ride(
    ride: Ride,
    comment_text: Optional[str] = None,
) -> ClassificationResult:
    """Sürüşü sinyallerine göre (kategori, alt sebep, kaynak) sonucuna sınıflandırır."""
    if ride.outcome is not RideOutcome.BASARISIZ_HARD:
        return _NONE_RESULT

    src = ClassificationSource.FIELD_SIGNAL

    # 1. Ödeme
    if ride.payment_status is not None and ride.payment_status is not PaymentStatus.OK:
        reason = _PAYMENT_REASON.get(ride.payment_status)
        return ClassificationResult(FailureCategory.ODEME, reason, src)

    # 2. Kullanıcı iptali
    if ride.user_cancelled is True:
        return ClassificationResult(FailureCategory.KULLANICI, FailureReason.USER_CANCELLED, src)

    # 3. Regülasyon tetikleyicisi (hangi kural olduğu classify anında bilinmez → reason None)
    if ride.triggered_regulation_id is not None:
        return ClassificationResult(FailureCategory.REGULASYON, None, src)

    # 4. Unlock ACK zaman aşımı
    if ride.unlock_ack is False:
        return ClassificationResult(
            FailureCategory.TEKNIK, FailureReason.UNLOCK_ACK_TIMEOUT, src
        )

    # 5. Diğer teknik saha sinyalleri
    if ride.connection_lost is True:
        return ClassificationResult(FailureCategory.TEKNIK, FailureReason.CONNECTION_LOST, src)
    if ride.motor_error_code:
        return ClassificationResult(FailureCategory.TEKNIK, FailureReason.MOTOR_ERROR, src)
    if ride.bms_error_code:
        return ClassificationResult(FailureCategory.TEKNIK, FailureReason.BMS_FAULT, src)
    if ride.start_battery_pct is not None and ride.start_battery_pct <= LOW_BATTERY_PCT:
        return ClassificationResult(FailureCategory.TEKNIK, FailureReason.LOW_BATTERY, src)

    # 6. Sürüş sonlandırma mesajı
    if ride.end_message:
        result = _classify_text(ride.end_message, ClassificationSource.TEXT_MESSAGE)
        if result is not None:
            return result

    # 7. Kullanıcı yorumu
    if comment_text:
        result = _classify_text(comment_text, ClassificationSource.TEXT_COMMENT)
        if result is not None:
            return result

    # 8. Sinyalsiz → kategori UYDURMA
    return _NONE_RESULT
