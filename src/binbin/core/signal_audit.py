"""Sinyal ayırt ediciliği (lift) — saf çekirdek, I/O yok.

Bir kodu `is_fault_signal=true` yapmak "bu olay başarısızlığı açıklar" İDDİASIDIR ve
ölçülebilir: lift = P(kod|başarısız) / P(kod|başarılı). lift ≈ 1 → gürültü.
Ölçümle `Batarya az` 0,8x çıkıp elendi (kural kitabı seed'i, db/01_setup.sql).

Düşük lift "bu kod arıza değil" demez, "bu sürüşün başarısızlığını açıklamıyor" der;
iş gerekçesi ölçümü geçersiz kılabilir. Bu modül KARAR VERMEZ, sayı üretir — karar
gerekçesiyle `fleet_status_reason`'da yaşar.
"""

from typing import Iterable

# Altındakiler "ayırt etmiyor" işaretlenir. İstatistik testi değil, pratik sınır.
WEAK_LIFT_THRESHOLD = 2.0

# Lift küçük hacimde anlamsızdır (2 sürüşte görülen kodun lift'i sonsuz olabilir).
# Altındakiler low_volume işaretlenir ve aday olarak ÖNERİLMEZ.
MIN_AUDIT_VOLUME = 50


def summarize_signal_discrimination(rows: Iterable[dict]) -> list[dict]:
    """Ham (kod, başarısızda kaç sürüş, başarılıda kaç sürüş) satırlarını lift'e çevirir.

    Beklenen satır alanları: reason_id, description, is_fault_signal, verified,
    fail_rides, ok_rides, n_fail, n_ok (son ikisi payda; her satırda aynı).

    `ok_rides = 0` iken lift tanımsızdır (0'a bölme) → `lift=None`, rapor "∞" basar.
    Hiç görülmeyen kodlar (fail_rides = ok_rides = 0) da döner; kural kitabında durup
    fiilen ölü olan kodların görünmesi bilinçlidir.
    """
    summary = []
    for row in rows:
        n_fail = row.get("n_fail") or 0
        n_ok = row.get("n_ok") or 0
        fail_rides = row.get("fail_rides") or 0
        ok_rides = row.get("ok_rides") or 0
        fail_rate = (100.0 * fail_rides / n_fail) if n_fail else 0.0
        ok_rate = (100.0 * ok_rides / n_ok) if n_ok else 0.0
        if ok_rate > 0:
            lift = round(fail_rate / ok_rate, 1)
        elif fail_rate > 0:
            lift = None  # payda sıfır ama pay var → sonsuz
        else:
            lift = 0.0  # hiç görülmedi
        summary.append(
            {
                "reason_id": row["reason_id"],
                "description": row["description"],
                "is_fault_signal": bool(row.get("is_fault_signal")),
                "verified": bool(row.get("verified")),
                "fail_rides": fail_rides,
                "ok_rides": ok_rides,
                "fail_rate_pct": round(fail_rate, 2),
                "ok_rate_pct": round(ok_rate, 3),
                "lift": lift,
                "weak": lift is not None and lift < WEAK_LIFT_THRESHOLD and fail_rides > 0,
                "low_volume": fail_rides < MIN_AUDIT_VOLUME,
            }
        )
    summary.sort(key=lambda r: r["fail_rides"], reverse=True)
    return summary
