"""Anahtar kelime ayırt ediciliği (lift) — saf çekirdek, I/O yok.

`signal_audit.py` durum-defteri kodlarını nasıl denetliyorsa bu modül de kelime
kural kitabını öyle denetler: bir kelimeyi kümeye koymak "bu ifade arıza bildirir"
İDDİASIDIR ve ölçülebilir → lift = P(kelime|başarısız) / P(kelime|başarılı).

NEDEN ÖZELLİKLE BURADA GEREKLİ: `TECHNICAL_KEYWORDS` iki tüketiciyi besler ve
ikincisi projenin ANA ÇIKTISIDIR —
  `_has_fault_text` → `fault_reported` → `verdict` → `wasted_missions`,
  ve `fault_reported` aynı zamanda eşik taramasının precision/recall'unun
  GROUND TRUTH'udur.
Yani yanlış-pozitif tek bir kelime hem "boşa görev" sayısını şişirir hem eşik
önerisinin dayanağını bozar. Ölçüm bu yüzden isteğe bağlı bir ek değil, kuralın
kendisidir.

PAYDA METİNLİ SÜRÜŞLERDİR, tüm sürüşler değil: yorum bırakmak başlı başına
başarısızlıkla korelasyonludur; paydaya sessiz sürüşleri katmak her kelimeye
sahte bir lift kazandırırdı.

BENİMSEME KURALI (karar bu modülde DEĞİL, burada üretilen sayılara dayanılarak
`keywords.py` yorumlarına gerekçesiyle yazılır):
    (A) `fail_hits >= MIN_KEYWORD_VOLUME` VE `lift >= WEAK_LIFT_THRESHOLD`, VEYA
    (B) `ok_hits == 0` — başarılı sürüş korpusunda bir kez bile geçmemiş.
(B) uzun kuyruk içindir: 3 kez geçen kelimenin lift'i anlamsızdır, ama "hiç
kirlenmemiş" ölçülebilir ve yanlışlanabilir bir iddiadır.

Bu modül KARAR VERMEZ, sayı üretir — `signal_audit` ile aynı sözleşme.
"""

from typing import Iterable, Mapping

from binbin.core.keywords import normalize
from binbin.core.signal_audit import WEAK_LIFT_THRESHOLD

# Kelime hacimleri durum-kodlarından çok daha küçüktür (bir kod 4,17M olayda
# geçerken bir kelime birkaç bin yorumda geçer); bu yüzden eşik ayrı ve düşüktür.
MIN_KEYWORD_VOLUME = 5


def _row_text(row: Mapping) -> str:
    """Denetlenecek metin: yorum + sürüş mesajı.

    `_has_fault_text` ikisine AYRI bakar ama "herhangi biri eşleşiyor mu"
    sorusu için birleştirmek eşdeğerdir.
    """
    return normalize(f"{row.get('comment_text') or ''} {row.get('end_message') or ''}")


def summarize_keyword_discrimination(
    rows: Iterable[Mapping], keyword_sets: Mapping[str, frozenset[str]]
) -> list[dict]:
    """Ham metin satırlarını kelime başına lift tablosuna çevirir.

    Beklenen satır alanları: `outcome`, `comment_text`, `end_message`.
    Metin satır başına BİR KEZ normalize edilir; kelime sayısı arttıkça maliyet
    metin normalizasyonunda değil yalnız substring aramasında büyür.
    """
    keyword_owner = {kw: name for name, kws in keyword_sets.items() for kw in kws}
    all_keywords = sorted(keyword_owner)
    # Marjinal katkı KÜME İÇİNDE ölçülür. Nedeni: bir kelimeyi silmenin sonucu
    # kendi kümesinin sorusuna göre tanımlıdır — TEKNIK için "bu sürüş
    # `fault_reported` kanıtını kaybeder mi" (`_has_fault_text` YALNIZ
    # TECHNICAL_KEYWORDS okur), SISTEM için "kategorisini kaybeder mi". Kümeler
    # arası sayılsaydı, aynı yorumda hem `gaz` (TEKNIK) hem `iptal` (KULLANICI)
    # geçen bir sürüş `gaz`ın katkısını sıfır gösterirdi — oysa `gaz` silinince
    # o sürüşün kanıtı gerçekten yok olur.
    by_set: dict[str, list[str]] = {}
    for kw, owner in keyword_owner.items():
        by_set.setdefault(owner, []).append(kw)

    n_fail = n_ok = 0
    fail_hits: dict[str, int] = {kw: 0 for kw in all_keywords}
    ok_hits: dict[str, int] = {kw: 0 for kw in all_keywords}
    marginal: dict[str, int] = {kw: 0 for kw in all_keywords}

    for row in rows:
        failed = str(row.get("outcome")) == "BASARISIZ_HARD"
        if failed:
            n_fail += 1
        else:
            n_ok += 1
        text = _row_text(row)
        target = fail_hits if failed else ok_hits
        for keywords_in_set in by_set.values():
            matched = [kw for kw in keywords_in_set if kw in text]
            for kw in matched:
                target[kw] += 1
            # Kümesinde tek başına yakalayan kelime: silinirse o sürüş bu kümenin
            # verdiği sonucu (kanıt ya da kategori) TAMAMEN kaybeder.
            if failed and len(matched) == 1:
                marginal[matched[0]] += 1

    summary = []
    for kw in all_keywords:
        fail_rate = (100.0 * fail_hits[kw] / n_fail) if n_fail else 0.0
        ok_rate = (100.0 * ok_hits[kw] / n_ok) if n_ok else 0.0
        if ok_rate > 0:
            lift = round(fail_rate / ok_rate, 1)
        elif fail_rate > 0:
            lift = None  # payda sıfır ama pay var → sonsuz
        else:
            lift = 0.0  # hiç görülmedi
        summary.append(
            {
                "set_name": keyword_owner[kw],
                "keyword": kw,
                "fail_hits": fail_hits[kw],
                "ok_hits": ok_hits[kw],
                "marginal_hits": marginal[kw],
                "fail_rate_pct": round(fail_rate, 3),
                "ok_rate_pct": round(ok_rate, 3),
                "lift": lift,
                # `fail_hits > 0` guard'i signal_audit ile AYNI: hic gorulmeyen kelime
                # "zayif" degil "olu"dur; ikisini karistirmak yanlis hukum verdirir.
                "weak": (
                    lift is not None
                    and lift < WEAK_LIFT_THRESHOLD
                    and fail_hits[kw] > 0
                ),
                "low_volume": fail_hits[kw] < MIN_KEYWORD_VOLUME,
                "uncontaminated": ok_hits[kw] == 0,
                "dead": fail_hits[kw] == 0 and ok_hits[kw] == 0,
            }
        )
    summary.sort(key=lambda r: (-r["fail_hits"], r["keyword"]))
    return summary


def coverage_summary(rows: Iterable[Mapping], keyword_sets: Mapping[str, frozenset[str]]) -> dict:
    """Küme DÜZEYİNDE kapsam: başarısız yorumların yüzde kaçı eşleşiyor.

    Kelime başına lift, tek tek kararlar içindir; asıl "genişletme işe yaradı mı"
    sorusunu bu cevaplar. Revizyon öncesi/sonrası bu fonksiyonla ölçülür; ölçülen
    değerler `keywords.py` başlığında ve raporun §7.3'ünde kayıtlıdır.
    """
    all_keywords = [kw for kws in keyword_sets.values() for kw in kws]
    total = matched = 0
    for row in rows:
        if str(row.get("outcome")) != "BASARISIZ_HARD":
            continue
        text = _row_text(row)
        if not text.strip():
            continue
        total += 1
        if any(kw in text for kw in all_keywords):
            matched += 1
    return {
        "failed_with_text": total,
        "matched": matched,
        "unmatched": total - matched,
        "matched_pct": round(100.0 * matched / total, 1) if total else 0.0,
    }
