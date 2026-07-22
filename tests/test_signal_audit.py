"""Sinyal ayırt ediciliği (lift) çekirdek testleri — DB'siz, saf fonksiyon."""

from binbin.core.signal_audit import (
    MIN_AUDIT_VOLUME,
    WEAK_LIFT_THRESHOLD,
    summarize_signal_discrimination,
)


def _row(reason_id, fail_rides, ok_rides, *, signal=True, desc="X", verified=False):
    return {
        "reason_id": reason_id,
        "description": desc,
        "is_fault_signal": signal,
        "verified": verified,
        "fail_rides": fail_rides,
        "ok_rides": ok_rides,
        "n_fail": 1000,
        "n_ok": 10000,
    }


def test_lift_guclu_sinyal():
    """Başarısızda %10, başarılıda %0,1 → 100x."""
    (row,) = summarize_signal_discrimination([_row(33, fail_rides=100, ok_rides=10)])
    assert row["fail_rate_pct"] == 10.0
    assert row["ok_rate_pct"] == 0.1
    assert row["lift"] == 100.0
    assert row["weak"] is False


def test_lift_ayirt_etmeyen_kod_zayif_isaretlenir():
    """Gerçek vaka (Batarya az): başarılıda başarısızdan DAHA sık → lift < 1."""
    (row,) = summarize_signal_discrimination([_row(8, fail_rides=93, ok_rides=1235)])
    assert row["lift"] < 1.0
    assert row["weak"] is True


def test_lift_paydasi_sifir_ise_none_sonsuz():
    """Başarılıda hiç görülmeyen kod: 0'a bölme yok, lift None (rapor '∞' basar)."""
    (row,) = summarize_signal_discrimination([_row(39, fail_rides=9, ok_rides=0)])
    assert row["lift"] is None
    assert row["weak"] is False  # sonsuz lift zayıf değildir


def test_hic_gorulmeyen_kod_dusurulmez():
    """Kural kitabında durup fiilen ölü olan kodlar da dönmeli (şeffaflık)."""
    (row,) = summarize_signal_discrimination([_row(7, fail_rides=0, ok_rides=0)])
    assert row["lift"] == 0.0
    assert row["weak"] is False       # hiç görülmedi ≠ zayıf sinyal
    assert row["low_volume"] is True  # ...ve hakkında hüküm de verilemez


def test_sifir_payda_bolme_hatasi_vermez():
    """n_fail/n_ok sıfırken (boş kapsam) patlamamalı."""
    rows = summarize_signal_discrimination(
        [{**_row(46, 0, 0), "n_fail": 0, "n_ok": 0}]
    )
    assert rows[0]["fail_rate_pct"] == 0.0
    assert rows[0]["ok_rate_pct"] == 0.0


def test_zayif_esigi_sinirda():
    """Tam eşikteki lift zayıf SAYILMAZ (kesin '<' karşılaştırması)."""
    n_fail, n_ok = 1000, 1000
    fail_rides = int(WEAK_LIFT_THRESHOLD * 100)
    row = {**_row(30, fail_rides, 100), "n_fail": n_fail, "n_ok": n_ok}
    (result,) = summarize_signal_discrimination([row])
    assert result["lift"] == WEAK_LIFT_THRESHOLD
    assert result["weak"] is False


# --- Hacim guard'ı: lift küçük sayıda ANLAMSIZDIR --------------------------
# Gerçek çıktıda 'Son kontrol'/'Bakımda' gibi 1-2 sürüşlük kodlar sırf lift'leri
# sonsuz çıktı diye "aday olabilir" işaretleniyordu. Rapor gürültüyü sinyale terfi
# ettirmeye teşvik etmemeli.
def test_dusuk_hacim_isaretlenir():
    (row,) = summarize_signal_discrimination(
        [_row(5, fail_rides=MIN_AUDIT_VOLUME - 1, ok_rides=0, signal=False)]
    )
    assert row["low_volume"] is True
    assert row["lift"] is None  # sonsuz — ama hüküm verilmemeli


def test_yeterli_hacim_isaretlenmez():
    (row,) = summarize_signal_discrimination(
        [_row(33, fail_rides=MIN_AUDIT_VOLUME, ok_rides=1)]
    )
    assert row["low_volume"] is False


def test_siralama_basarisiz_surus_sayisina_gore():
    rows = summarize_signal_discrimination(
        [_row(1, 5, 5), _row(2, 100, 5), _row(3, 50, 5)]
    )
    assert [r["reason_id"] for r in rows] == [2, 3, 1]
