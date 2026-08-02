"""`scenario_analysis` ve `threshold_scan`'in paylaştığı saf yardımcılar (I/O yok).
Kopyalanırsa payda-sıfır davranışı iki motorda ayrışır — sessiz rapor tutarsızlığı.
"""


def pct(part: float, whole: float) -> float:
    """Yüzde; payda sıfırsa 0.0 (analizde 'veri yok' ile 'oran sıfır' aynı raporlanır)."""
    return round(100.0 * part / whole, 1) if whole else 0.0


def enum_or_none(enum_cls, value):
    """DB string'ini enum'a çevirir; None ve zaten-enum değerleri olduğu gibi geçirir."""
    if value is None or isinstance(value, enum_cls):
        return value
    return enum_cls(value)
