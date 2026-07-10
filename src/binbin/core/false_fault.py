"""Sahte Arıza (False Fault) Değerlendirme Algoritması — Functional Core (Pure, DB I/O yok).

İŞ PROBLEMİ: Müşteri cihaz için yersiz yere "arızalı" derse operasyona 3 boşa görev yazar:
(1) Sahadan toplama (Pickup)
(2) Atölye kontrolü (Workshop)
(3) Sahaya geri bırakma (Redeploy)
Amaç: Sahte alarmları tespit edip operasyonel zararı ölçmek ve minimize etmek.

ÖNEMLİ (Adlandırma): Olaylara "SAHTE" değil "ŞÜPHELİ" diyoruz. Çünkü elimizde geofence 
veya koordinat verisi tam yok, kesin yargı dağıtamayız.

Değerlendirme (Verdict) Önceliği:
    next_ride (sonraki sürüş) yoksa -> DEGERLENDIRILEMEDI (Yeterli data yok)
    şikayet/bildirim yoksa          -> BILDIRIM_YOK (Bizim kontrol/baseline grubumuz)
    bildirim var + alet sağlamsa    -> SAHTE_ALARM_SUPHESI (Müşteri yalan söylemiş/yanılmış)
    bildirim var + alet bozuksa     -> GERCEK_ARIZA_SUPHESI (Müşteri haklı)
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
    """Tek bir sürüş için sahte-arıza değerlendirme sonucu (DB'deki tabloyla hizalıdır).
    
    Not: ops_*_task_id gibi saha görevleri sonradan dışarıdan doldurulur; 
    bu pure fonksiyon sadece analitik değerlendirme (assessment) yapar.
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
    """Kullanıcının girdiği yorumda teknik bir arıza şikayeti geçiyor mu? NLP regex taraması."""
    return bool(text) and keywords.contains_any(text, keywords.TECHNICAL_KEYWORDS)


def _report_evidence(
    ride: Ride,
    comment_text: Optional[str],
    rating: Optional[int],
) -> ClassificationSource:
    """Arıza bildiriminin nereden geldiğini (kanıtını) bulur. Hiçbir şey yoksa NONE döner."""
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
    """Arızalı denilen bir sürüşün sahte mi yoksa gerçek mi olduğunu hesaplar.
    
    Bunu anlamak için AYNI aracın bir sonraki sürüşüne (`next_ride`) bakarız. 
    Eğer araç kısa süre içinde başka biri tarafından kiralanıp uzun mesafe 
    gidebilmişse, önceki kullanıcının arıza bildirimi sahtedir (healthy_proof = True).
    
    Ayrıca `rating` (yıldız) = 1 ise bunu potansiyel arıza şikayeti sayarız.
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
