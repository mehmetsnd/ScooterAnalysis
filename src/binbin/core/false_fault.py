"""Sahte Arıza Alarmı değerlendirmesi — functional core: SAF, I/O yok.

İŞ PROBLEMİ: Çalışan bir cihaz "arızalı" bildirilirse 3 boşa görev doğar —
(1) sahadan toplama, (2) atölye kontrolü, (3) sahaya geri bırakma. Bu sırada
gerçekten arızalı araçlar sahada bekler. Amaç: bunu ÖLÇMEK ve azaltmak.

ADLANDIRMA DİSİPLİNİ: "SAHTE" değil "ŞÜPHELİ". Bu veri kesin hüküm veremez
(geofence poligonu ve başlangıç koordinatı yok). Enum değerleri şemadakiyle birebir.

verdict önceliği (ck_verdict_consistency ile uyumlu; görevdeki liste kesin sıralama
değildir — "değerlendirilemez" durumu diğerlerinden önce gelir ki kontrol grubu
yalnızca değerlendirilebilir sürüşleri içersin):
    next_ride yok           → DEGERLENDIRILEMEDI
    bildirim yok            → BILDIRIM_YOK        (KONTROL GRUBU)
    bildirim var + sağlam   → SAHTE_ALARM_SUPHESI
    bildirim var + sağlam değil → GERCEK_ARIZA_SUPHESI
"""

from dataclasses import dataclass
from typing import Optional

from binbin.core import keywords
from binbin.domain.enums import (
    ClassificationSource,
    FalseFaultHypothesis,
    FaultVerdict,
    RideOutcome,
)
from binbin.domain.models import Ride

# Bir sahte alarm 3 boşa görev doğurur (toplama + atölye + geri bırakma).
FALSE_ALARM_WASTED_MISSIONS = 3


@dataclass
class FaultAssessment:
    """Bir sürüşün sahte-arıza değerlendirmesi (DB: false_fault_assessment ile hizalı).

    ops_*_task_id ve assessor_version saha görev verisi / CLI tarafından doldurulur;
    saf değerlendirme bunları üretmez.
    """

    fault_reported: bool
    report_evidence: ClassificationSource
    vehicle_moved: Optional[bool]
    next_ride_id: Optional[int]
    next_ride_start_time: Optional[object]  # datetime | None
    next_ride_gap_min: Optional[float]
    next_ride_ok: Optional[bool]
    next_ride_distance_m: Optional[float]
    healthy_proof: bool
    verdict: FaultVerdict
    hypothesis: FalseFaultHypothesis
    wasted_missions: int


def _has_fault_text(text: Optional[str]) -> bool:
    """Metin teknik arıza şikâyeti içeriyor mu? (arıza bildirimi sinyali)"""
    return bool(text) and keywords.contains_any(text, keywords.TECHNICAL_KEYWORDS)


def _report_evidence(
    ride: Ride,
    comment_text: Optional[str],
    rating: Optional[int],
) -> ClassificationSource:
    """Arıza bildirimini kuran en spesifik kanıt kaynağı; bildirim yoksa NONE."""
    if _has_fault_text(ride.end_message):
        return ClassificationSource.TEXT_MESSAGE
    if _has_fault_text(comment_text):
        return ClassificationSource.TEXT_COMMENT
    if ride.end_reason_id is not None:
        return ClassificationSource.REASON_CODE
    if rating == 1:
        return ClassificationSource.FIELD_SIGNAL
    return ClassificationSource.NONE


def _gap_min(ride: Ride, next_ride: Ride) -> float:
    """Bu sürüşün bitişi ile sonraki sürüşün başlangıcı arasındaki dakika."""
    anchor = ride.end_time or ride.start_time
    return (next_ride.start_time - anchor).total_seconds() / 60.0


def assess_ride(
    ride: Ride,
    next_ride: Optional[Ride],
    comment_text: Optional[str] = None,
    rating: Optional[int] = None,
    healthy_min_distance_m: float = 200.0,
    healthy_max_gap_min: float = 360.0,
) -> FaultAssessment:
    """Bir başarısız sürüşü, aynı aracın sonraki sürüşüne bakarak değerlendirir.

    `next_ride`: AYNI aracın kronolojik olarak bir sonraki sürüşü (outcome fark etmez).
    `rating`: sürüşün feedback puanı (varsa) — 1 yıldız bir arıza bildirimi sinyalidir.
    """
    report_evidence = _report_evidence(ride, comment_text, rating)
    fault_reported = report_evidence is not ClassificationSource.NONE

    vehicle_moved: Optional[bool]
    if ride.distance_m is None:
        vehicle_moved = None
    else:
        vehicle_moved = ride.distance_m > 0

    # Sonraki sürüş kanıtı
    next_ride_id: Optional[int] = None
    next_start = None
    gap_min: Optional[float] = None
    next_ok: Optional[bool] = None
    next_distance: Optional[float] = None
    healthy_proof = False

    if next_ride is not None:
        next_ride_id = next_ride.ride_id
        next_start = next_ride.start_time
        gap_min = _gap_min(ride, next_ride)
        next_ok = next_ride.outcome is RideOutcome.BASARILI
        next_distance = next_ride.distance_m
        healthy_proof = (
            next_ok
            and next_distance is not None
            and next_distance > healthy_min_distance_m
            and gap_min <= healthy_max_gap_min
        )

    # Hüküm (yukarıdaki öncelik)
    if next_ride is None:
        verdict = FaultVerdict.DEGERLENDIRILEMEDI
    elif not fault_reported:
        verdict = FaultVerdict.BILDIRIM_YOK
    elif healthy_proof:
        verdict = FaultVerdict.SAHTE_ALARM_SUPHESI
    else:
        verdict = FaultVerdict.GERCEK_ARIZA_SUPHESI

    # Hipotez: yalnızca sahte alarm şüphesinde anlamlı
    if verdict is FaultVerdict.SAHTE_ALARM_SUPHESI:
        if vehicle_moved is False:  # 0 m hareket + araç sağlam → geofence şüphesi
            hypothesis = FalseFaultHypothesis.REGULASYON_SUPHESI
        else:
            hypothesis = FalseFaultHypothesis.GECICI_TEKNIK
    else:
        hypothesis = FalseFaultHypothesis.BELIRSIZ

    wasted = (
        FALSE_ALARM_WASTED_MISSIONS
        if verdict is FaultVerdict.SAHTE_ALARM_SUPHESI
        else 0
    )

    return FaultAssessment(
        fault_reported=fault_reported,
        report_evidence=report_evidence,
        vehicle_moved=vehicle_moved,
        next_ride_id=next_ride_id,
        next_ride_start_time=next_start,
        next_ride_gap_min=gap_min,
        next_ride_ok=next_ok,
        next_ride_distance_m=next_distance,
        healthy_proof=healthy_proof,
        verdict=verdict,
        hypothesis=hypothesis,
        wasted_missions=wasted,
    )
