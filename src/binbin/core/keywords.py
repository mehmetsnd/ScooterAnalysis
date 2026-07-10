"""Anahtar kelime kümeleri — SAF. Sınıflandırma kuralları koda gömülmez, buradan okunur.

TR + EN + Balkan dilleri (Boşnakça/Makedonca). Eşleştirme daima küçük harfe
indirgenmiş, aksan-duyarsız normalize edilmiş metin üzerinde yapılır (bkz.
`normalize`). Kümeler `frozenset` — değişmez.

> UYARI: "para iadesi istiyorum", "param gitti" gibi metinler ödeme *hatası*
> DEĞİLDİR — bozuk araca ödeme yapmış kullanıcının şikâyetidir → TEKNIK sayılır.
> Bu yüzden bu ifadeler TECHNICAL_KEYWORDS içindedir ve SYSTEM/ODEME kümelerine
> KONULMAZ; classifier'daki öncelik sırası da bunu garanti eder.
"""

import unicodedata

# --- Regülasyon / yasak bölge ---------------------------------------------
REGULATION_KEYWORDS: frozenset[str] = frozenset(
    {
        # TR
        "alan disi",
        "alan disinda",
        "yasak bolge",
        "yasakli",
        "yasak alan",
        "kirmizi alan",
        "surus disi bolge",
        "surus disi",
        "park yasak",
        "park edilemez",
        "geofence",
        "6 km",
        "bolge disi",
        "hiz limiti",
        "yavas bolge",
        # EN
        "no ride zone",
        "no-ride zone",
        "restricted area",
        "forbidden zone",
        "out of zone",
        "out of area",
        "red zone",
        "no parking",
        # Balkan
        "zabranjena zona",
        "zabranjeno",
        "zabraneta zona",
    }
)

# --- Kullanıcı kaynaklı -----------------------------------------------------
USER_KEYWORDS: frozenset[str] = frozenset(
    {
        # TR
        "kullanici talebi",
        "iptal",
        "iptal ettim",
        "vazgec",
        "vazgectim",
        "yanlis arac",
        "yanlislikla",
        "istemeden",
        "fikrimi degistirdim",
        # EN
        "cancel",
        "cancelled",
        "canceled",
        "changed my mind",
        "wrong vehicle",
        "wrong scooter",
        "by mistake",
        # Balkan
        "otkazi",
        "otkazano",
    }
)

# --- Teknik / arıza ---------------------------------------------------------
# "para iadesi / param gitti" burada: bozuk araç şikâyeti → TEKNIK.
TECHNICAL_KEYWORDS: frozenset[str] = frozenset(
    {
        # TR — genel
        "gitmiyor",
        "calismiyor",
        "calismadi",
        "bozuk",
        "ariza",
        "arizali",
        "hareket etmiyor",
        "hareket etmedi",
        # gaz / motor
        "gaz",
        "gaz vermiyor",
        "gaz calismiyor",
        "motor",
        "hizlanmiyor",
        # kilit / unlock
        "kilit",
        "kilitli",
        "acilmadi",
        "acilmiyor",
        "kilit acilmadi",
        "unlock olmadi",
        # fiziksel hasar
        "hasar",
        "hasarli",
        "kirik",
        "patlak",
        "lastik",
        "fren",
        "frenler",
        # ödemeyle sonuçlanan bozuk araç şikâyeti → TEKNIK
        "para iadesi",
        "param gitti",
        "parami geri",
        "ucret aldi ama",
        # EN
        "not working",
        "doesn't work",
        "does not work",
        "broken",
        "damaged",
        "won't move",
        "does not move",
        "throttle",
        "brake",
        "flat tire",
        "refund",
        "money back",
        # Balkan
        "ne radi",
        "pokvaren",
        "ne raboti",
        "rasipan",
    }
)

# --- Sistem / uygulama ------------------------------------------------------
SYSTEM_KEYWORDS: frozenset[str] = frozenset(
    {
        # TR
        "uygulama",
        "uygulama hatasi",
        "kamera",
        "kamera acilmadi",
        "surus bulunamadi",
        "sonlandirilamadi",
        "sonlandiramadim",
        "baglanti",
        "server",
        "sunucu",
        # EN
        "app",
        "app error",
        "camera",
        "could not end",
        "cannot end ride",
        "server error",
        # Balkan
        "aplikacija",
    }
)

# --- Alt sebep eşlemesi (teknik metinden FailureReason) --------------------
# Öncelik: kilit → LOCK_JAM, gaz/motor → MOTOR_ERROR, diğer teknik → IOT_FAULT.
LOCK_KEYWORDS: frozenset[str] = frozenset(
    {"kilit", "kilitli", "acilmadi", "acilmiyor", "unlock", "lock"}
)
MOTOR_KEYWORDS: frozenset[str] = frozenset(
    {"gaz", "motor", "hizlanmiyor", "throttle"}
)

# --- Sistem mesaj kalıpları (sürüş sonlandırma mesajından) -----------------
# "partial payment stop phase" → ödeme sistemi otomatik durdurma
# "hareketsiz|inactive|neaktiv" → 10 dk otomatik sonlandırma
SYSTEM_MESSAGE_PATTERNS: frozenset[str] = frozenset(
    {
        "partial payment stop phase",
        "hareketsiz",
        "inactive",
        "neaktiv",
    }
)


def normalize(text: str) -> str:
    """Metni küçük harfe indirger ve aksanları/Türkçe karakterleri sadeleştirir.

    Böylece 'İPTAL', 'iptal', 'Iptal' aynı biçime iner ve anahtar kelimeler
    aksan-duyarsız eşleşir. Kümelerdeki tüm anahtarlar zaten bu biçimde yazılıdır.
    """
    lowered = text.casefold()
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
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
