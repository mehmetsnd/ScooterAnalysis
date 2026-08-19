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
    """Sonraki sürüş 72 saatten geç ise sağlam-kanıt sayılmaz."""
    a = assess_ride(_ride(), _next(ok=True, distance_m=800.0, gap_min=4400),
                    comment_text="araç bozuk")
    assert a.healthy_proof is False
    assert a.verdict is FaultVerdict.GERCEK_ARIZA_SUPHESI


def test_healthy_proof_72_saate_kadar_gecerlidir():
    """Sonraki sürüş 6 saati aşsa da 72 saat içindeyse sağlam-kanıt sayılır.

    Eşik 360 dk iken bu sürüş GERCEK_ARIZA_SUPHESI'ne düşüyordu; saha bakım
    döngüsü 72 saat olduğu için o pencere fazla dardı.
    """
    a = assess_ride(_ride(), _next(ok=True, distance_m=800.0, gap_min=4000),
                    comment_text="araç bozuk")
    assert a.healthy_proof is True
    assert a.verdict is FaultVerdict.SAHTE_ALARM_SUPHESI


def test_healthy_proof_gap_siniri_tam_72_saatte_dahildir():
    """Sınır dahil: tam 4320 dk (72 sa) hâlâ sağlam-kanıttır."""
    a = assess_ride(_ride(), _next(ok=True, distance_m=800.0, gap_min=4320),
                    comment_text="araç bozuk")
    assert a.healthy_proof is True


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


def test_healthy_proof_ayni_musteri_ile_bozulur():
    """Aynı müşteri tekrar deneyip başardıysa cihazın sağlamlığı kanıtlanmış olmaz."""
    a = assess_ride(_ride(), _next(ok=True, distance_m=800.0),
                    comment_text="araç bozuk", next_ride_different_user=False)
    assert a.healthy_proof is False
    assert a.verdict is FaultVerdict.GERCEK_ARIZA_SUPHESI


def test_healthy_proof_farkli_musteri_ile_gecerlidir():
    """Farklı müşteri aynı cihazı sorunsuz kullandıysa sağlam-kanıt güçlüdür."""
    a = assess_ride(_ride(), _next(ok=True, distance_m=800.0),
                    comment_text="araç bozuk", next_ride_different_user=True)
    assert a.healthy_proof is True


def test_vehicle_moved_uses_measured_displacement_when_available():
    """Koordinat varsa vehicle_moved odometre çıkarımı değil FİZİKSEL ölçümdür."""
    a = assess_ride(_ride(distance_m=140.0), _next(), displacement_m=3.0)
    assert a.vehicle_moved is False


def test_vehicle_moved_falls_back_to_odometer_without_coordinates():
    """Koordinat yoksa (sürüşlerin %3,8'i) eski davranış korunur."""
    a = assess_ride(_ride(distance_m=140.0), _next(), displacement_m=None)
    assert a.vehicle_moved is True
