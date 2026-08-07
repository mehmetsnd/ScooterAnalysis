"""Anahtar kelime ayırt ediciliği (lift) testleri — saf, DB'siz.

`signal_audit` durum-defteri kodlarını nasıl denetliyorsa bu da kelime kural
kitabını öyle denetler. Karar VERMEZ, sayı üretir.
"""

import pytest

from binbin.core.keyword_audit import (
    MIN_KEYWORD_VOLUME,
    summarize_keyword_discrimination,
)


def _row(outcome, comment="", message=""):
    return {"outcome": outcome, "comment_text": comment, "end_message": message}


def _by_keyword(summary):
    return {r["keyword"]: r for r in summary}


def test_lift_hesabi_orana_dayanir_ham_sayiya_degil():
    """Lift = P(kelime|başarısız) / P(kelime|başarılı).

    Ham sayı yanıltıcıdır: başarılı sürüşler çok daha kalabalık olduğu için
    bir kelime orada mutlak olarak daha çok görünebilir ama ORAN olarak nadir olur.
    """
    rows = [_row("BASARISIZ_HARD", "bozuk"), _row("BASARISIZ_HARD", "iyiydi")] + [
        _row("BASARILI", "bozuk")
    ] + [_row("BASARILI", "iyiydi") for _ in range(9)]
    summary = _by_keyword(
        summarize_keyword_discrimination(rows, {"TEST": frozenset({"bozuk"})})
    )
    row = summary["bozuk"]
    assert row["fail_hits"] == 1 and row["ok_hits"] == 1
    assert row["fail_rate_pct"] == 50.0      # 1/2
    assert row["ok_rate_pct"] == 10.0        # 1/10
    assert row["lift"] == 5.0


def test_basarisizda_gorulen_dusuk_lift_zayif_isaretlenir():
    """Başarısızda görülüyor ama başarılıda daha sık → zayıf (ör. `fren` 0,5x)."""
    rows = [_row("BASARISIZ_HARD", "fren"), _row("BASARISIZ_HARD", "iyi")]
    rows += [_row("BASARILI", "fren") for _ in range(4)]
    rows += [_row("BASARILI", "iyi") for _ in range(4)]
    summary = _by_keyword(
        summarize_keyword_discrimination(rows, {"TEST": frozenset({"fren"})})
    )
    assert summary["fren"]["lift"] < 2.0
    assert summary["fren"]["weak"] is True


def test_basarisizda_hic_gorulmeyen_kelime_zayif_degil_ters():
    """Başarısızda HİÇ geçmeyip yalnız başarılıda geçen kelime "zayıf" DEĞİLDİR.

    `signal_audit` ile aynı sözleşme: `weak` yalnız `fail_hits > 0` iken anlamlıdır
    — "zayıf sinyal" ile "hiç sinyal değil" farklı hükümlerdir. Bu kelime (ör.
    ölçümde `kamera` 0/97) tamamen yanlış-pozitif üretir; CLI onu lift<1 VE
    ok_hits>0 olduğu için "TERS" diye işaretler, "zayıf" diye değil.
    """
    rows = [_row("BASARISIZ_HARD", "iyi")] + [_row("BASARILI", "kamera") for _ in range(5)]
    summary = _by_keyword(
        summarize_keyword_discrimination(rows, {"TEST": frozenset({"kamera"})})
    )
    assert summary["kamera"]["fail_hits"] == 0
    assert summary["kamera"]["lift"] == 0.0
    assert summary["kamera"]["weak"] is False   # zayıf değil
    assert summary["kamera"]["dead"] is False   # ölü de değil — görülüyor
    assert summary["kamera"]["uncontaminated"] is False


def test_hic_kirletilmemis_kelime_isaretlenir():
    """`ok_hits == 0`: hacim düşük olsa da benimseme kuralının (B) şıkkını karşılar.

    3 kez geçen bir kelimenin lift'i ölçülemez; ama "başarılı sürüşlerin
    HİÇBİRİNDE geçmemiş" ölçülebilir ve yanlışlanabilir bir iddiadır.
    """
    rows = [_row("BASARISIZ_HARD", "kilidi yok"), _row("BASARILI", "harikaydi")]
    summary = _by_keyword(
        summarize_keyword_discrimination(rows, {"TEST": frozenset({"kilidi"})})
    )
    assert summary["kilidi"]["uncontaminated"] is True
    assert summary["kilidi"]["ok_hits"] == 0


def test_dusuk_hacim_isaretlenir_ve_hukum_verilmez():
    rows = [_row("BASARISIZ_HARD", "bozuk")] + [_row("BASARILI", "iyi") for _ in range(3)]
    summary = _by_keyword(
        summarize_keyword_discrimination(rows, {"TEST": frozenset({"bozuk"})})
    )
    assert summary["bozuk"]["fail_hits"] < MIN_KEYWORD_VOLUME
    assert summary["bozuk"]["low_volume"] is True


def test_marjinal_katki_baska_kelimenin_yakaladigini_saymaz():
    """`marginal_hits`: bu kelime SİLİNSE kaç sürüş TÜM kanıtını kaybederdi.

    Aynı yorumu iki kelime birden yakalıyorsa ikisine de tam kredi vermek,
    genişletmenin kazancını olduğundan büyük gösterir.
    """
    rows = [
        _row("BASARISIZ_HARD", "arac bozuk ve calismiyor"),  # iki kelime birden
        _row("BASARISIZ_HARD", "calismiyor"),                # yalnız biri
    ]
    summary = _by_keyword(
        summarize_keyword_discrimination(
            rows, {"TEST": frozenset({"bozuk", "calismiyor"})}
        )
    )
    assert summary["calismiyor"]["fail_hits"] == 2
    assert summary["calismiyor"]["marginal_hits"] == 1   # 1. satırı `bozuk` da yakalıyor
    assert summary["bozuk"]["fail_hits"] == 1
    assert summary["bozuk"]["marginal_hits"] == 0        # tek başına kimseyi kurtarmıyor


def test_marjinal_katki_kume_icinde_olculur():
    """Marjinal katkı KÜME İÇİNDE sayılır, kümeler arası değil.

    `_has_fault_text` yalnız TECHNICAL_KEYWORDS okur. Bir yorumda hem teknik hem
    kullanıcı kelimesi geçiyorsa, teknik kelimeyi silmek o sürüşün ARIZA KANITINI
    yok eder — başka kümede eşleşme olması bunu telafi etmez. Kümeler arası
    sayılsaydı katkı sıfır görünür ve tutulması gereken kelime elenirdi.
    """
    rows = [_row("BASARISIZ_HARD", "arac bozuk, iptal ettim")]
    summary = _by_keyword(
        summarize_keyword_discrimination(
            rows, {"TEKNIK": frozenset({"bozuk"}), "KULLANICI": frozenset({"iptal"})}
        )
    )
    assert summary["bozuk"]["marginal_hits"] == 1
    assert summary["iptal"]["marginal_hits"] == 1


def test_end_message_de_korpusa_dahildir():
    """`_has_fault_text` önce `end_message`'a bakar; denetim de bakmalı."""
    rows = [_row("BASARISIZ_HARD", "", "arac bozuk")]
    summary = _by_keyword(
        summarize_keyword_discrimination(rows, {"TEST": frozenset({"bozuk"})})
    )
    assert summary["bozuk"]["fail_hits"] == 1


def test_metin_normalize_edilerek_eslesir():
    rows = [_row("BASARISIZ_HARD", "ARAÇ ÇALIŞMIYOR")]
    summary = _by_keyword(
        summarize_keyword_discrimination(rows, {"TEST": frozenset({"calismiyor"})})
    )
    assert summary["bozuk" if False else "calismiyor"]["fail_hits"] == 1


def test_bos_korpus_sifira_bolmez():
    summary = summarize_keyword_discrimination([], {"TEST": frozenset({"bozuk"})})
    row = summary[0]
    assert row["fail_rate_pct"] == 0.0 and row["ok_rate_pct"] == 0.0
    assert row["lift"] == 0.0


def test_kume_adi_tasinir_ve_siralama_fail_hits_azalan():
    rows = [_row("BASARISIZ_HARD", "bozuk calismiyor"), _row("BASARISIZ_HARD", "calismiyor")]
    summary = summarize_keyword_discrimination(
        rows, {"TEKNIK": frozenset({"bozuk", "calismiyor"})}
    )
    assert summary[0]["keyword"] == "calismiyor"      # 2 hit, önce gelir
    assert all(r["set_name"] == "TEKNIK" for r in summary)
