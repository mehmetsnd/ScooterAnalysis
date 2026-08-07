"""Anahtar kelime kural kitabı testleri — saf, DB'siz.

NEDEN BU DOSYA VAR: `keywords.py` projenin kanıt üretiminin merkezindedir. İki ayrı
tüketicisi var ve ikincisi projenin ANA ÇIKTISINI besliyor:

  1. `classifier._classify_text` → başarısız sürüşe kategori atar,
  2. `false_fault._has_fault_text` → `fault_reported` → `verdict` → `wasted_missions`
     VE eşik taramasının F1'i (kanıt, precision/recall'un ground truth'udur).

Yani yanlış-pozitif tek bir anahtar, "boşa görev" sayısını şişirmekle kalmaz, eşik
önerisinin dayanağını da bozar. Bu dosya o yüzden POZİTİF eşleşmeler kadar
NEGATİF olanları da kilitler: masum metin hiçbir şeyle eşleşmemelidir.
"""

import pytest

from binbin.core import keywords as K

ALL_SETS = {
    "TECHNICAL_KEYWORDS": K.TECHNICAL_KEYWORDS,
    "REGULATION_KEYWORDS": K.REGULATION_KEYWORDS,
    "USER_KEYWORDS": K.USER_KEYWORDS,
    "SYSTEM_KEYWORDS": K.SYSTEM_KEYWORDS,
    "LOCK_KEYWORDS": K.LOCK_KEYWORDS,
    "MOTOR_KEYWORDS": K.MOTOR_KEYWORDS,
}


# --- normalize: Türkçe + Balkan + kesme işareti ----------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("İPTAL", "iptal"),
        ("Çalışmıyor", "calismiyor"),
        ("ARIZALI", "arizali"),
        ("Bozuk Ğ Ö Ü Ş", "bozuk g o u s"),
        ("loše kočnice", "lose kocnice"),   # Balkan aksanları NFKD ile çözülür
        ("Ne RADI", "ne radi"),
    ],
)
def test_normalize_aksan_ve_buyuk_harf(raw, expected):
    assert K.normalize(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("doesn't work", "doesnt work"),
        ("doesn’t work", "doesnt work"),      # U+2019 (akıllı kesme)
        ("won't move", "wont move"),
        ("BinBin'i alamadım", "binbini alamadim"),
    ],
)
def test_normalize_kesme_isaretini_siler(raw, expected):
    """Kesme işareti SİLİNİR, boşluğa çevrilmez.

    Çevrilseydi `don't work` → `don t work` olur ve `dont work` anahtarı yine
    tutmazdı. Ölçüldü: `doesnt work` metinli başarısız sürüşlerde 43 kez geçiyor
    ve `"doesn't work"` anahtarı bunların HİÇBİRİNİ yakalayamıyordu.
    """
    assert K.normalize(raw) == expected


def test_normalize_d_stroke_nfkd_ile_cozulmez():
    """BİLİNEN SINIR: `đ` (U+0111) NFKD ile ayrışmaz, `d`ye inmez.

    Belgelenmesinin sebebi: içinde `đ` geçen bir Boşnakça anahtar yazılırsa
    sessizce hiç eşleşmez. Anahtarlar ASCII biçiminde yazılmalıdır.
    """
    assert K.normalize("đ") == "đ"


# --- küme hijyeni ----------------------------------------------------------
@pytest.mark.parametrize("set_name", sorted(ALL_SETS))
def test_anahtarlar_normalize_edilmis_bicimde_yazilir(set_name):
    """`contains_any` yalnız METNİ normalize eder, anahtarları etmez.

    Anahtar `arızalı` diye yazılsaydı normalize edilmiş metinle asla eşleşmezdi
    — sessizce ölü bir kural olurdu.
    """
    for kw in ALL_SETS[set_name]:
        assert K.normalize(kw) == kw, f"{set_name}: {kw!r} normalize edilmemiş"


@pytest.mark.parametrize("set_name", sorted(ALL_SETS))
def test_anahtarlarda_kesme_isareti_bulunmaz(set_name):
    """Regresyon kilidi: normalize kesme işaretini sildiği için anahtarda
    kesme işareti kalırsa o anahtar hiçbir metinle eşleşemez."""
    for kw in ALL_SETS[set_name]:
        assert "'" not in kw and "’" not in kw, f"{set_name}: {kw!r}"


@pytest.mark.parametrize("set_name", sorted(ALL_SETS))
def test_anahtarlar_bosluk_ve_uzunluk_sozlesmesi(set_name):
    for kw in ALL_SETS[set_name]:
        assert kw == kw.strip(), f"{set_name}: {kw!r} baş/son boşluk"
        assert len(kw) >= 3, f"{set_name}: {kw!r} çok kısa (yanlış-pozitif riski)"


# --- substring kapsaması (contains_any'in çalışma biçimi) ------------------
@pytest.mark.parametrize(
    "text,keyword",
    [
        ("çalışmıyo", "calismiyo"),      # kısa kök uzun biçimi de yakalar
        ("çalışmıyor", "calismiyo"),
        ("arızalı", "ariza"),
        ("hasarlı", "hasar"),
        ("iptal ettim", "iptal"),
    ],
)
def test_kisa_kok_uzun_bicimi_kapsar(text, keyword):
    """`contains_any` anahtarı metnin İÇİNDE arar; kısa kök uzun biçimi kapsar.

    Bu yüzden kümede hem `ariza` hem `arizali` tutmak fazlalıktır — uzun olan
    hiçbir zaman tek başına eşleşmez.
    """
    assert K.contains_any(text, frozenset({keyword}))


@pytest.mark.parametrize("set_name", sorted(ALL_SETS))
def test_ayni_kumede_kapsanan_anahtar_kalmaz(set_name):
    """Aynı kümede bir anahtar diğerini kapsıyorsa uzun olan ÖLÜDÜR.

    Ölü kural kümede durunca "bu ifade özel olarak ele alınıyor" yanılsaması
    yaratır ve bakımda yanlış yönlendirir.
    """
    keys = sorted(ALL_SETS[set_name])
    dead = [(b, a) for a in keys for b in keys if a != b and a in b]
    assert dead == [], f"{set_name}: kapsanan (ölü) anahtarlar: {dead}"


# --- ölçümle elenen kelimeler geri sızmasın --------------------------------
@pytest.mark.parametrize(
    "keyword,measured",
    [
        ("damaged", "0/14 — başarısızda hiç yok"),
        ("money back", "0/22 — başarısızda hiç yok"),
        ("flat tire", "0/2 — başarısızda hiç yok"),
    ],
)
def test_olculerek_elenen_teknik_kelimeler_geri_gelmez(keyword, measured):
    """Regresyon kilidi: başarısız sürüşlerde HİÇ görülmeyen kelimeler.

    Bunlar yalnız gürültü üretir — hangi soruyu sorarsak soralım kanıt değiller.
    """
    assert keyword not in K.TECHNICAL_KEYWORDS, f"{keyword}: {measured}"


def test_fren_is_karariyla_kanit_sayilir():
    """Lift düşük (0,5x) ama `fren` İŞ KARARIYLA tutulur.

    Gerekçe: `_has_fault_text` "arıza bildirilmiş mi" sorar, "başarısızlığı
    öngörür mü" değil. Elenmesi 85 başarısız sürüşün YEGÂNE kanıtını siliyordu.
    Bilinen bedel: `frenleri iyiydi` gibi övgü metni de eşleşir.
    """
    from binbin.core.false_fault import _has_fault_text

    assert _has_fault_text("sağ fren kopuk ve gaz arızalı") is True
    assert _has_fault_text("fren tutmuyor") is True
    assert _has_fault_text("brakes are broken") is True


@pytest.mark.parametrize("keyword", ["kamera", "camera", "uygulama", "app", "baglanti"])
def test_olculerek_elenen_sistem_kelimeleri_geri_gelmez(keyword):
    assert keyword not in K.SYSTEM_KEYWORDS


@pytest.mark.parametrize("keyword", ["6 km", "park yasak", "yasak alan", "alan disi", "yasakli"])
def test_olculerek_elenen_regulasyon_kelimeleri_geri_gelmez(keyword):
    """`6 km` özellikle tehlikeliydi: REGULASYON en yüksek öncelikli kümedir,
    'sadece 6 km gidebildim' yorumu sürüşü Regülasyon'a yazıyordu."""
    assert keyword not in K.REGULATION_KEYWORDS


# --- öncelik sırası ve alt-sebep yönlendirmesi -----------------------------
@pytest.mark.parametrize(
    "comment,expected_category",
    [
        ("araç çalışmıyor", "TEKNIK"),
        ("yasak bölge olduğu için araç gitmiyor", "REGULASYON"),  # REGULASYON > TEKNIK
        ("yanlışlıkla kiraladım, iptal", "KULLANICI"),
        ("sürüşü sonlandırılamadı", "SISTEM"),
    ],
)
def test_kategori_onceligi(comment, expected_category):
    """Öncelik: REGULASYON > KULLANICI > SISTEM > TEKNIK.

    Yanlış kümeye konan bir cihaz-arızası kelimesi hem kategoriyi bozar hem de
    kanıt tabanından TAMAMEN düşer (`_has_fault_text` yalnız TEKNIK okur).
    """
    from binbin.core.classifier import _classify_text
    from binbin.domain.enums import ClassificationSource

    result = _classify_text(comment, ClassificationSource.TEXT_COMMENT)
    assert result is not None, f"{comment!r} hiçbir kümeyle eşleşmedi"
    assert result.category.value == expected_category


@pytest.mark.parametrize(
    "comment,expected_reason",
    [
        ("kilidi açılmadı", "LOCK_JAM"),
        ("gaz vermiyor", "MOTOR_ERROR"),
        ("direksiyon dönmüyo", "MOTOR_ERROR"),
        ("araç bozuk", "IOT_FAULT"),
        ("şarjı yok", "IOT_FAULT"),
    ],
)
def test_teknik_alt_sebep_yonlendirmesi(comment, expected_reason):
    from binbin.core.classifier import _technical_reason

    assert _technical_reason(comment).value == expected_reason


# --- NEGATİF: masum metin hiçbir şeyle eşleşmemeli -------------------------
@pytest.mark.parametrize(
    "benign",
    [
        "harika bir sürüştü, çok memnun kaldım",
        "kamera ile fotoğraf çektim",        # `kamera` elendi
        "uygulama çok kullanışlı",           # `uygulama` elendi
        "sadece 6 km gidebildim",            # `6 km` elendi (REGULASYON tuzağı)
        "park yasak alanlar çok fazla",      # `park yasak` elendi
        "happy with the ride",               # `app` elendi ("happy" içinde geçiyordu)
        "şarjı doluydu, gayet iyi gitti",
    ],
)
def test_masum_metin_hicbir_kategoriye_dusmez(benign):
    """En pahalı hata sınıfı: masum yorumu arıza kanıtı saymak.

    `fault_reported` → `wasted_missions` VE eşik taramasının F1'i buradan besleniyor;
    yanlış-pozitif hem boşa görev sayısını şişirir hem eşik önerisini bozar.
    """
    from binbin.core.classifier import _classify_text
    from binbin.core.false_fault import _has_fault_text
    from binbin.domain.enums import ClassificationSource

    assert _classify_text(benign, ClassificationSource.TEXT_COMMENT) is None
    assert _has_fault_text(benign) is False


def test_has_fault_text_bos_ve_none_guvenli():
    from binbin.core.false_fault import _has_fault_text

    assert _has_fault_text("") is False
    assert _has_fault_text(None) is False
