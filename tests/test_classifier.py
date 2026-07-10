"""classify_ride testleri — DB'siz, saf çekirdek."""

from datetime import datetime

from binbin.core.classifier import classify_ride
from binbin.domain.enums import (
    ClassificationSource,
    FailureCategory,
    FailureReason,
    PaymentStatus,
    RideOutcome,
)
from binbin.domain.models import Ride


def _ride(**overrides) -> Ride:
    """Varsayılan BAŞARISIZ_HARD (sinyalsiz) bir Ride kurar."""
    defaults = dict(
        ride_id=1,
        source_ref="test-ride-1",
        vehicle_id=1,
        city_id=1,
        user_ref="user-1",
        start_time=datetime(2026, 6, 1, 12, 0),
        outcome=RideOutcome.BASARISIZ_HARD,
    )
    defaults.update(overrides)
    return Ride(**defaults)


def test_basarili_surus_none_doner():
    """Başarılı sürüşe kategori atanmaz."""
    assert classify_ride(_ride(outcome=RideOutcome.BASARILI)) == (
        None,
        None,
        ClassificationSource.NONE,
    )


def test_sinyalsiz_basarisiz_none_doner():
    """Sinyalsiz başarısız sürüş → kategori UYDURULMAZ."""
    result = classify_ride(_ride())
    assert result == (None, None, ClassificationSource.NONE)


def test_odeme_field_signal():
    r = _ride(payment_status=PaymentStatus.DECLINED)
    result = classify_ride(r)
    assert result.category is FailureCategory.ODEME
    assert result.reason is FailureReason.PAYMENT_DECLINED
    assert result.source is ClassificationSource.FIELD_SIGNAL


def test_kullanici_iptali_field_signal():
    result = classify_ride(_ride(user_cancelled=True))
    assert result.category is FailureCategory.KULLANICI
    assert result.source is ClassificationSource.FIELD_SIGNAL


def test_regulasyon_tetikleyici_field_signal():
    result = classify_ride(_ride(triggered_regulation_id=42))
    assert result.category is FailureCategory.REGULASYON
    assert result.source is ClassificationSource.FIELD_SIGNAL


def test_unlock_ack_false_teknik():
    result = classify_ride(_ride(unlock_ack=False))
    assert result.category is FailureCategory.TEKNIK
    assert result.reason is FailureReason.UNLOCK_ACK_TIMEOUT


def test_dusuk_batarya_teknik():
    result = classify_ride(_ride(start_battery_pct=2))
    assert result.category is FailureCategory.TEKNIK
    assert result.reason is FailureReason.LOW_BATTERY


def test_mesaj_metni_teknik_kilit_lock_jam():
    result = classify_ride(_ride(end_message="Kilit açılmadı, gitmiyor"))
    assert result.category is FailureCategory.TEKNIK
    assert result.reason is FailureReason.LOCK_JAM
    assert result.source is ClassificationSource.TEXT_MESSAGE


def test_yorum_metni_teknik_text_comment():
    result = classify_ride(_ride(), comment_text="araç bozuk, çalışmıyor")
    assert result.category is FailureCategory.TEKNIK
    assert result.source is ClassificationSource.TEXT_COMMENT


def test_oncelik_cakismasi_regulasyon_kazanir():
    """'yasak bölge' + 'gitmiyor' birlikte → REGULASYON kazanır."""
    result = classify_ride(_ride(end_message="yasak bölge, araç gitmiyor"))
    assert result.category is FailureCategory.REGULASYON


def test_para_iadesi_teknik_odeme_degil():
    """'para iadesi istiyorum' bozuk araç şikâyetidir → TEKNIK (ODEME DEĞİL)."""
    result = classify_ride(_ride(), comment_text="para iadesi istiyorum, araç çalışmadı")
    assert result.category is FailureCategory.TEKNIK
    assert result.category is not FailureCategory.ODEME


def test_alan_disi_regulasyon():
    result = classify_ride(_ride(end_message="alan dışı, sürüş sonlandırıldı"))
    assert result.category is FailureCategory.REGULASYON
