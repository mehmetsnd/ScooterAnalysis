"""Sınıflandırma anahtar kelime kümeleri (frozenset) — pure, I/O yok.

Diller: TR + EN + Balkan (Boşnakça/Makedonca). Eşleşmeler case-insensitive,
aksan-duyarsız ve kesme-işareti-duyarsızdır (bkz. normalize).

DİKKAT: "para iadesi", "param gitti" ODEME değil TEKNIK kümesindedir — bunlar genelde
bozuk araç kiralayıp parasını kaptıran müşterinin şikayetidir.

═══ KURAL KİTABI DENETİMİ (2026-08-05, `analyze --all --kelime-denetimi`) ═══
Bu kümeler, `fleet_status_reason` gibi ampirik olarak DENETLENİR: bir kelimeyi
kümeye koymak "bu ifade arıza bildirir" İDDİASIDIR ve ölçülebilir —
    lift = P(kelime | başarısız) / P(kelime | başarılı)
Korpus: metni olan 32.078 sürüş (5.004'ü başarısız). Payda metinli sürüşlerdir;
sessizleri katmak her kelimeye sahte lift kazandırırdı.

Neden burada ölçüm ŞART: `TECHNICAL_KEYWORDS` yalnız kategori atamaz, aynı zamanda
`false_fault._has_fault_text` → `fault_reported` → `wasted_missions` zincirini VE
eşik taramasının F1'ini besler.

Lift "kelime başarısızlığı ÖNGÖRÜR mü" sorusunu ölçer; `_has_fault_text` ise "arıza
BİLDİRİLMİŞ mi" sorar. Düşük lift, ifadenin arıza bildirmediği anlamına GELMEZ —
başarılı bir sürüşte de gerçek arıza bildirilmiş olabilir. Bu ayrım gözden kaçarsa
gerçek kanıt elenir (bkz. fren, aşağıda).

Benimseme kuralı — ÜÇ yol vardır:
  (A) başarısızda ≥5 VE lift ≥2,0 · VEYA
  (B) başarılıda HİÇ yok (uzun kuyruk: hacim düşükken lift ölçülemez ama
      "hiç kirlenmemiş" yanlışlanabilir bir iddiadır) · VEYA
  (C) **İŞ KARARI** — ölçüm zayıf ama kelime tek başına çok sayıda başarısız
      sürüşün YEGÂNE kanıtıdır ve daha dar bir biçim onu kapsamıyor. Bu yol
      `fleet_status_reason` kural kitabındaki ile aynıdır (CLAUDE.md: "düşük lift
      otomatik eleme DEĞİLDİR — iş gerekçesi ölçümü geçersiz kılabilir, ama gerekçe
      YAZILIR"). (C) ile tutulan her kelime aşağıda `(İŞ KARARI)` etiketi ve
      gerekçesiyle işaretlidir; etiketsiz zayıf kelime BIRAKILMAZ.

Elenen her kelime de ölçülen sayısıyla birlikte aşağıda gerekçesiyle kayıtlıdır —
`db/07_signal_rulebook_revision.sql`'in kelime tarafındaki karşılığı.

NOT: aşağıdaki `tekil` değerleri REVİZYON SONRASI kümeye göredir (bir kelimenin
tekil katkısı, kümedeki diğer kelimelere bağlıdır); `fail/ok/lift` ise kelimeye
özgüdür ve kümeden bağımsızdır.
"""

import unicodedata

REGULATION_KEYWORDS: frozenset[str] = frozenset(
    {
        # TR
        "yasak bolge",
        "surus disi",
        "geofence",
        # EN
        "no ride zone",
        "no-ride zone",
        "restricted area",
        "forbidden zone",
        "out of zone",
        "out of area",
        # Balkan
        "zabranjena zona",
        "zabraneta zona",
        # ELENDİ (2026-08-05 ölçümü) — hepsi başarılı sürüşlerde daha sık:
        #   alan disi     5 / 79   → 0,3x     yasakli      5 / 94  → 0,3x
        #   6 km          1 / 23   → 0,2x     kirmizi alan 1 / 11  → 0,5x
        #   park yasak    0 / 30   → 0,0x     yasak alan   0 / 19  → 0,0x
        #   hiz limiti    0 / 15   → 0,0x     no parking   0 /  4  → 0,0x
        #   red zone      0 /  4   · bolge disi 0 / 3 · park edilemez 0 / 2
        #   zabranjeno    0 /  1
        # `6 km` ÖZELLİKLE tehlikeliydi: REGULASYON en yüksek öncelikli kümedir,
        # "sadece 6 km gidebildim" yorumu sürüşü Regülasyon'a yazıyordu.
        # Bu kelimeler tamamlanmış sürüşlerin şikayetidir (yasak bölge ÇOK, park
        # zor vb.) — başarısızlığın NEDENİ değil.
    }
)

USER_KEYWORDS: frozenset[str] = frozenset(
    {
        # TR
        "vazgec",
        "iptal",
        "yanlis arac",
        "yanlislikla",
        "istemeden",
        "fikrimi degistirdim",
        # EN
        "cancel",
        "changed my mind",
        "wrong vehicle",
        "wrong scooter",
        "by mistake",
        # Balkan
        "otkazi",
        "otkazano",
        # ELENDİ: `kullanici talebi` 8 / 84 → 0,5x. Operatör/sistem dili; müşteri
        # iptali değil, kayıt notu. Başarılı sürüşlerde iki kat sık.
    }
)

# "para iadesi / param gitti" burada: bozuk araç şikâyeti → TEKNIK.
TECHNICAL_KEYWORDS: frozenset[str] = frozenset(
    {
        # --- TR genel (ölçülmüş: kök biçim uzun biçimi de kapsar) ------------
        "bozuk",          # 964 /1.958 → 2,7x · tekil 713
        "calismiyo",      # 751 /1.290 → 3,1x · tekil 494  (`calismiyor`u kapsar)
        "calismad",       # 345 /  580 → 3,2x · tekil 287  (`calismadi`yi kapsar)
        "ariza",          # 374 /  980 → 2,1x · tekil 291
        "gitmiyor",       # 269 /  662 → 2,2x · tekil 151
        "arzali",         #  14 /   27 → 2,8x  (yaygın yazım hatası)
        "calismio",       #  10 /    1 → 54,1x
        "suremedi",       #  93 /  193 → 2,6x · tekil 42
        "baslamadi",      #  24 /   13 → 10,0x
        "baslamiyo",      #  12 /    5 → 13,0x
        "surulmuyor",     #  11 /   24 → 2,5x
        "binbin gitmiyo", #   6 /    8 → 4,1x
        "hareket etmiyor",#  38 /   64 → 3,2x
        "hareket etmedi", #  25 /   60 → 2,3x
        "direksiyon donmuyo",  # 7 / 17 → 2,2x
        # --- gaz / motor -----------------------------------------------------
        # `gaz` lift 1,4x ile ZAYIF ve bilinen bir çarpışması var: normalize
        # edilmiş "magaza"/"gazi mahallesi" metinlerini de yakalar. Buna rağmen
        # İŞ KARARIYLA tutuldu: tek başına 169 başarısız sürüşün YEGÂNE kanıtı
        # (dar biçim `gaz vermiyor`un tekil katkısı 0 — hepsini `gaz` zaten
        # yakalıyor). Daha iyi ayrım için gaz kolu telemetrisi gerekir.
        "gaz",            # 571 /2.219 → 1,4x · tekil 169  (İŞ KARARI)
        "motor",          #  36 /  136 → 1,4x · tekil 11   (İŞ KARARI, aynı gerekçe)
        "hizlanmiyor",    #  29 /  130 → 1,2x · tekil 21   (İŞ KARARI)
        "throttle",       #   3 /    5 → 3,2x (hacim düşük — İŞ KARARI: gaz kolu terimi)
        # --- kilit / unlock --------------------------------------------------
        "acilmadi",       # 132 /   51 → 14,0x · tekil 96   ← en güçlü sinyal
        "acilmiyor",      # 103 /   99 → 5,6x  · tekil 68
        "kilit",          #  89 /  249 → 1,9x  · tekil 34   (İŞ KARARI, sınırda)
        "not unlock",     #  11 /    1 → 59,5x
        "unlock olmadi",  # bu ayda hiç görülmedi; veri gelirse çalışsın diye durur
        # --- batarya / şarj ---------------------------------------------------
        "sarji yok",      #  18 /   38 → 2,6x
        "sarj yok",       #   7 /    5 → 7,6x
        # --- fiziksel hasar ---------------------------------------------------
        "kirik",          #  75 /  196 → 2,1x
        "hasar",          #  40 /   98 → 2,2x
        "patlak",         #  21 /   63 → 1,8x (İŞ KARARI, sınırda)
        "lastik",         #  18 /   64 → 1,5x (İŞ KARARI, sınırda)
        # --- ödemeyle sonuçlanan bozuk araç şikâyeti → TEKNIK ------------------
        "para iadesi",    #  62 /  187 → 1,8x (İŞ KARARI: bozuk araç şikayeti)
        "parami geri",    #  26 /  116 → 1,2x (İŞ KARARI, aynı gerekçe)
        "param gitti",    #  22 /   74 → 1,6x (İŞ KARARI, aynı gerekçe)
        "refund",         #   8 /   33 → 1,3x (İŞ KARARI, aynı gerekçe)
        "ucret aldi ama", # bu ayda hiç görülmedi
        # --- EN ---------------------------------------------------------------
        "not working",    #  52 /  111 → 2,5x
        "doesnt work",    #  43 /   61 → 3,8x  ← kesme işareti düzeltmesiyle açıldı
        "didnt work",     #  16 /   31 → 2,8x
        "does not work",  #   9 /   22 → 2,2x
        "broken",         #  63 /  127 → 2,7x
        "wouldnt turn on",#   1 /    0 → ∞ (kural B: başarılıda hiç yok)
        "wont move",      # bu ayda hiç görülmedi
        # --- Balkan -----------------------------------------------------------
        "ne radi",        #  47 /   94 → 2,7x
        "pokvaren",       #  11 /   34 → 1,7x (İŞ KARARI, sınırda)
        "ne raboti",      #   1 /    1 → 5,4x (hacim düşük — İŞ KARARI: Makedonca karşılık)
        "rasipan",        # bu ayda hiç görülmedi
        # --- fren (İŞ KARARI) --------------------------------------------------
        # Lift düşük (fren 179/1.965 → 0,5x · brake 4/92 → 0,2x) çünkü fren arızası
        # sürüşü engellemez; kullanıcı tamamlar. Ama `_has_fault_text` "neden
        # başarısız oldu" değil "arıza bildirilmiş mi" sorar — `fren kopuk` açık bir
        # bildirimdir. 2026-08-05'te elenmiş, 2026-08-07'de geri alındı: elenmesi
        # 88 başarısız sürüşün YEGÂNE kanıtını siliyordu. Alt-sebep yok → IOT_FAULT.
        "fren",           # `frenler`/`freni`/`fren tutmuyor` hepsini kapsar
        "brake",
        # ═══ ELENDİ — başarısızda HİÇ görülmeyen, yalnız gürültü üretenler ════
        # damaged 0 / 14 · money back 0 / 22 · flat tire 0 / 2 · does not move 0 / 1
    }
)

SYSTEM_KEYWORDS: frozenset[str] = frozenset(
    {
        # TR
        "sonlandirilamadi",  # 4 / 6 → 3,6x (hacim düşük — İŞ KARARI: kümenin tek geçerli sinyali)
        "sunucu",            # bu ayda hiç görülmedi
        # EN
        "server",            # bu ayda hiç görülmedi (`server error`u da kapsar)
        "cannot end ride",   # bu ayda hiç görülmedi
        "app error",         # bu ayda hiç görülmedi
        # ELENDİ (2026-08-05) — hepsi başarılı sürüşlerde daha sık ya da yalnız:
        #   kamera     0 / 97 → 0,0x   uygulama       23 / 331 → 0,4x
        #   camera     0 / 13 → 0,0x   uygulama hatasi 2 /  25 → 0,4x
        #   baglanti   0 / 18 → 0,0x   app             4 /  39 → 0,6x
        #   aplikacija 0 /  6 → 0,0x   surus bulunamadi 1 / 22 → 0,2x
        #   sonlandiramadim 0 / 9 · could not end 0 / 3
        # `kamera` ve `uygulama` uygulama DENEYİMİ hakkında yorumdur (fotoğraf
        # çektim, uygulama şöyle…), sürüşün başarısızlık nedeni değil.
        # NOT: `app` ayrıca "happy" gibi masum kelimelerin içinde de geçiyordu.
    }
)

# Öncelik: kilit → LOCK_JAM, gaz/motor → MOTOR_ERROR, diğer teknik → IOT_FAULT.
# `kilitli` (⊂ kilit) ve `unlock` (⊂ lock) ÇIKARILDI: kısa kök uzun biçimi zaten
# yakalar, uzun olan hiç tek başına eşleşmezdi.
LOCK_KEYWORDS: frozenset[str] = frozenset(
    {"kilit", "acilmadi", "acilmiyor", "lock"}
)
MOTOR_KEYWORDS: frozenset[str] = frozenset(
    {"gaz", "motor", "hizlanmiyor", "throttle", "direksiyon donmuyo"}
)


# Kesme işareti aileleri. SİLİNİR, boşluğa çevrilmez: çevrilseydi `don't work`
# → `don t work` olur ve `dont work` anahtarı yine tutmazdı.
_APOSTROPHES = ("'", "’", "ʼ", "`")


def normalize(text: str) -> str:
    """Metni küçük harfe indirger; aksanları, Türkçe harfleri ve kesme işaretini sadeleştirir.

    Böylece 'İPTAL', 'iptal', 'Iptal' aynı biçime iner ve anahtar kelimeler
    aksan-duyarsız eşleşir. Kümelerdeki tüm anahtarlar zaten bu biçimde yazılıdır
    (test_keywords.py bunu kilitler).

    KESME İŞARETİ: NFKD bunu çözmez ve eşleşmeyi sessizce bozuyordu — `"doesn't work"`
    anahtarı, başarısız sürüş yorumlarında 43 kez geçen `doesnt work` metnini ASLA
    yakalayamıyordu. Türkçe ek kesmesini de düzeltir: `BinBin'i` → `binbini`.

    BİLİNEN SINIR: `đ` (U+0111) NFKD ile ayrışmaz; Boşnakça anahtarlar ASCII yazılır.
    """
    lowered = text.casefold()
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    for mark in _APOSTROPHES:
        stripped = stripped.replace(mark, "")
    # Türkçe'ye özgü, NFKD ile ayrışmayan harfler
    return (
        stripped.replace("ı", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ç", "c")
        .replace("ö", "o")
        .replace("ü", "u")
    )


def contains_any(text: str, keywords: frozenset[str]) -> bool:
    """Normalize edilmiş `text`, kümedeki herhangi bir anahtarı içeriyorsa True."""
    norm = normalize(text)
    return any(kw in norm for kw in keywords)
