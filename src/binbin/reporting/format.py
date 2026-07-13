"""Türkçe sayı/eşik biçimleyicileri — sunum katmanı ortak yardımcıları.

CLI yazdırıcıları (`cli/main`) ve grafik modülü (`reporting/charts`) aynı biçimi
paylaşsın diye TEK kaynak. Saf fonksiyonlar; I/O yok. (Bağlama özel etiket sözlükleri
— ör. grup/kategori adları — bilinçli olarak kendi modüllerinde kalır; onlar biçim
değil sunum kararıdır.)
"""


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
