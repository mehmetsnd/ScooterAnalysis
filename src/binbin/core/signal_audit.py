"""Sinyal ayırt ediciliği (lift) — SAF çekirdek, I/O yok.

NEDEN VAR: kural kitabındaki (`fleet_status_reason`) bir kodun `is_fault_signal=true`
yapılması bir İDDİADIR: "bu olay başarısızlığı açıklar". Bu iddia ölçülebilir —
kod, başarısız sürüşlerin penceresinde başarılı sürüşlere göre ne kadar sık düşüyor?

    lift = P(kod | başarısız) / P(kod | başarılı)

    lift ≈ 1  → kod başarısızlığı AÇIKLAMIYOR, gürültü.
    lift >> 1 → kod gerçekten başarısızlıkla ilişkili.

Bu ölçüm bir kez gerçek veride koşulduğunda `Batarya az` kodunun lift'inin 0,8x olduğu
(yani başarılı sürüşlerde DAHA SIK düştüğü) görüldü ve kural kitabından çıkarıldı —
bkz. db/07_signal_rulebook_revision.sql.

ALTIN KURAL bağlantısı: düşük lift tek başına "bu kod arıza değil" demez, "bu kod
sürüşün başarısızlığını açıklamıyor" der. İş gerekçesi (ör. batarya bitmesi gerçek bir
saha görevi doğurur) ölçümü geçersiz kılabilir; o yüzden bu modül KARAR VERMEZ, yalnız
sayıyı üretir. Karar `fleet_status_reason` tablosunda, gerekçesiyle birlikte yaşar.
"""

from typing import Iterable

# Bu eşiğin altındaki kodlar raporda "ayırt etmiyor" diye işaretlenir. Kesin bir
# istatistik testi değil, gözle tarama için pratik bir sınır.
WEAK_LIFT_THRESHOLD = 2.0

# Lift, küçük hacimde ANLAMSIZDIR: 2 başarısız sürüşte görülüp hiç başarılıda
# görülmeyen bir kodun lift'i sonsuzdur ama hiçbir şey kanıtlamaz. Bu eşiğin
# altındaki kodlar `low_volume` işaretlenir ve rapor onları "aday" diye ÖNERMEZ —
# aksi hâlde denetim raporu, gürültüyü sinyale terfi ettirmeye teşvik ederdi.
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
