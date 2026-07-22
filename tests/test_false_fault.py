"""assess_ride testleri — DB'siz, saf çekirdek."""

from datetime import datetime, timedelta

from binbin.core.false_fault import assess_ride
from binbin.domain.enums import ClassificationSource, FalseFaultHypothesis, FaultVerdict, RideOutcome
from binbin.domain.models import Ride

_T0 = datetime(2026, 6, 1, 12, 0)


def _ride(distance_m=100.0, end_offset_min=5, **overrides) -> Ride:
    defaults = dict(
        ride_id=1,
        source_ref="r1",
        vehicle_id=7,
        city_id=1,
        user_ref="u1",
        start_time=_T0,
        outcome=RideOutcome.BASARISIZ_HARD,
        end_time=_T0 + timedelta(minutes=end_offset_min),
        distance_m=distance_m,
    )
    defaults.update(overrides)
    return Ride(**defaults)


def _next(ok=True, distance_m=500.0, gap_min=30) -> Ride:
    start = _T0 + timedelta(minutes=5 + gap_min)
    return Ride(
        ride_id=2,
        source_ref="r2",
        vehicle_id=7,
        city_id=1,
        user_ref="u9",
        start_time=start,
        outcome=RideOutcome.BASARILI if ok else RideOutcome.BASARISIZ_HARD,
        distance_m=distance_m,
    )


def test_next_ride_yok_degerlendirilemedi():
    a = assess_ride(_ride(), None, comment_text="araç bozuk")
    assert a.verdict is FaultVerdict.DEGERLENDIRILEMEDI


def test_bildirim_yok_kontrol_grubu():
    """Metin/kod/puan yoksa → BILDIRIM_YOK (kontrol grubu)."""
    a = assess_ride(_ride(), _next(), comment_text=None, rating=None)
    assert a.verdict is FaultVerdict.BILDIRIM_YOK
    assert a.fault_reported is False


def test_sahte_alarm_suphesi():
    """Arıza bildirimi + araç sonradan sağlam → SAHTE_ALARM_SUPHESI."""
    a = assess_ride(_ride(distance_m=50.0), _next(ok=True, distance_m=800.0),
                    comment_text="araç çalışmıyor")
    assert a.verdict is FaultVerdict.SAHTE_ALARM_SUPHESI
    assert a.healthy_proof is True


def test_gercek_ariza_suphesi():
    """Arıza bildirimi + araç toparlanmadı → GERCEK_ARIZA_SUPHESI."""
    a = assess_ride(_ride(), _next(ok=False), comment_text="araç bozuk")
    assert a.verdict is FaultVerdict.GERCEK_ARIZA_SUPHESI
    assert a.healthy_proof is False


def test_regulasyon_suphesi_yalniz_sifir_mesafede():
    """0 m hareket + sahte alarm → REGULASYON_SUPHESI."""
    a = assess_ride(_ride(distance_m=0.0), _next(ok=True, distance_m=800.0),
                    comment_text="araç çalışmıyor")
    assert a.verdict is FaultVerdict.SAHTE_ALARM_SUPHESI
    assert a.hypothesis is FalseFaultHypothesis.REGULASYON_SUPHESI


def test_regulasyon_suphesi_atanmaz_hareket_varsa():
    """Mesafe > 0 iken sahte alarm → REGULASYON_SUPHESI DEĞİL (GECICI_TEKNIK)."""
    a = assess_ride(_ride(distance_m=120.0), _next(ok=True, distance_m=800.0),
                    comment_text="araç çalışmıyor")
    assert a.verdict is FaultVerdict.SAHTE_ALARM_SUPHESI
    assert a.hypothesis is FalseFaultHypothesis.GECICI_TEKNIK


def test_healthy_proof_gap_asiminda_bozulur():
    """Sonraki sürüş 6 saatten geç ise sağlam-kanıt sayılmaz."""
    a = assess_ride(_ride(), _next(ok=True, distance_m=800.0, gap_min=400),
                    comment_text="araç bozuk")
    assert a.healthy_proof is False
    assert a.verdict is FaultVerdict.GERCEK_ARIZA_SUPHESI


def test_puan_bir_arica_bildirimi_sayilir():
    """rating==1 tek başına arıza bildirimidir."""
    a = assess_ride(_ride(), _next(ok=False), comment_text=None, rating=1)
    assert a.fault_reported is True
    assert a.verdict is FaultVerdict.GERCEK_ARIZA_SUPHESI


def test_sahte_alarm_uc_bosa_gorev():
    a = assess_ride(_ride(distance_m=0.0), _next(ok=True, distance_m=800.0),
                    comment_text="araç çalışmıyor")
    assert a.wasted_missions == 3


# --- field_fault (araç durum-değişim defteri arıza sinyali) -----------------
def test_field_fault_tek_basina_bildirim_sayilir():
    """Metin/puan yok ama field_fault=True → REASON_CODE kanıtı, bildirim var sayılır."""
    a = assess_ride(_ride(), _next(ok=False), comment_text=None, rating=None, field_fault=True)
    assert a.fault_reported is True
    assert a.report_evidence is ClassificationSource.REASON_CODE
    assert a.verdict is FaultVerdict.GERCEK_ARIZA_SUPHESI


def test_field_fault_metin_kanitindan_sonra_gelir():
    """Metin kanıtı varsa (TEXT_MESSAGE/TEXT_COMMENT) field_fault onu EZMEZ, öncelik metinde kalır."""
    a = assess_ride(
        _ride(), _next(ok=True, distance_m=800.0),
        comment_text="araç çalışmıyor", field_fault=True,
    )
    assert a.report_evidence is ClassificationSource.TEXT_COMMENT
