"""CLI (`cli/main`) ve grafiklerin (`reporting/charts`) paylaştığı sunum sabitleri —
tek kaynak. Saf, I/O yok.
"""

# Hem terminalde hem PNG'de görünür. Kopyalanınca sessizce sapmıştı: bir yerde
# "(kontrol)", diğerinde "(kontrol grubu)" yazıyordu.
GROUP_LABELS = {
    "ariza_metinli": "Arıza metinli bildirim",
    "herhangi_bildirimli": "Herhangi bildirim",
    "bildirimsiz": "Bildirimsiz (kontrol grubu)",
}

# CLI ve charts AYNI etiketi kullanmalı; kopyalanınca terminal ile PNG sessizce sapar.
CAUSE_LABELS = {
    "TEKNIK": "Teknik",
    "REGULASYON": "Regülasyon",
    "KULLANICI": "Kullanıcı",
    "ODEME": "Ödeme",
    "SISTEM": "Sistem",
    "ARAC_TARAFI": "Araç tarafı",
    "KULLANICI_TARAFI": "Kullanıcı tarafı",
    "KANIT_YOK": "Kanıt Bulunmayan",
}


def tr_int(value) -> str:
    """12345 / 12345.4 → '12.345' (Türkçe binlik ayracı; float'ları yuvarlar)."""
    return f"{int(round(value)):,}".replace(",", ".")


def tr_pct(value) -> str:
    """91.5 → '%91,5'."""
    return f"%{float(value):.1f}".replace(".", ",")


def tr_dec(value, n: int = 2) -> str:
    """6.06 → '6,06'."""
    return f"{value:.{n}f}".replace(".", ",")


def signed_int(value) -> str:
    """1234 → '+1.234', -5 → '-5' (işaretli, Türkçe binlik ayracı)."""
    return f"{int(value):+,}".replace(",", ".")


def fmt_threshold(value) -> str:
    """Eşik gösterimi: tam sayıysa ondalıksız (120.0 → '120', 45.5 → '45.5')."""
    return str(int(value)) if float(value).is_integer() else str(value)
