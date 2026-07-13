"""Analiz fonksiyonları — Functional Core (Pure fonksiyonlar, I/O veya Database call yok).

`repo` parametresi bir RideQueryRepository arayüzüdür. Clean Architecture gereği 
core -> data bağımlılığını (import) engellemek için burada duck typing kullanıyor ve 
type hint (ipucu) eklemiyoruz. Aynı şekilde `scope` da tipsizdir ve doğrudan repo'ya paslanır.
Scope kontrolü (policy) CLI katmanında yapılır; core katmanı "filtre nedir" bilmez.

Tüm ağır SQL aggregation işlemleri (toplama, sayma) database'de yapılır.
Buradaki fonksiyonlar sadece yüzdelik dilimleri (pct) ekleyip veriyi JSON/Dict haline getirir.
"""


def _pct(part: int, whole: int) -> float:
    """Yüzde (bir ondalık); payda 0 ise 0.0."""
    return round(100.0 * part / whole, 1) if whole else 0.0


def _num(v) -> str:
    """Eşik gösterimi: tam sayıysa ondalıksız (120.0 → '120', 45.5 → '45.5')."""
    return str(int(v)) if float(v).is_integer() else str(v)


def cause_distribution(repo, scope=None) -> dict:
    """Başarısız sürüşlerin arıza kategorilerine göre dağılımı.
    
    Önemli: 'Sinyalsiz' (failure_category IS NULL) veriyi kafamıza göre bir 
    kategoriye uydurmuyoruz; tabloda kendi satırı olarak şeffafça raporluyoruz.
    """
    rows = repo.failure_category_counts(scope)
    total = sum(r["count"] for r in rows)
    categories = []
    signalless = 0
    for r in rows:
        if r["category"] is None:
            signalless += r["count"]
            continue
        categories.append(
            {"category": r["category"], "count": r["count"], "pct": _pct(r["count"], total)}
        )
    categories.sort(key=lambda x: x["count"], reverse=True)
    return {
        "total_failed": total,
        "categories": categories,
        "signalless": {"count": signalless, "pct": _pct(signalless, total)},
    }


def failure_criteria_check(repo, scope=None, dur_thr: float = 120, dist_thr: float = 60) -> dict:
    """'Başarısız' kuralının (süre < dur_thr VE mesafe < dist_thr) outcome ile kıyası.

    Eşikler PARAMETRİKTİR: default 120sn/60m gerçek kuraldır, ama what-if senaryosu
    için farklı değerlerle çağrılabilir (bkz. failure_criteria_whatif).

    Burada 2 farklı edge-case (kuyruk) tespit ediyoruz:
      a) Gizli Başarısız: Veritabanında SUCCESS dönmüş ama kriterlere göre aslında başarısız.
      b) Hiç Gitmemiş: Mesafe 0 ama sürüş dur_thr saniyeden uzun sürmüş (açıp unutmuş olabilir).
    """
    c = repo.failure_criteria_counts(scope, dur_thr, dist_thr)
    failed_total = c["failed_total"]
    success_total = c["success_total"]
    return {
        "criterion": f"duration_sec < {_num(dur_thr)} AND distance_m < {_num(dist_thr)}",
        "duration_threshold": dur_thr,
        "distance_threshold": dist_thr,
        "failed_meeting_criterion": {
            "count": c["criteria_and_failed"],
            "pct_of_failed": _pct(c["criteria_and_failed"], failed_total),
        },
        "hidden_failed": {  # (a) BASARILI ama kritere uyan
            "count": c["criteria_and_success"],
            "pct_of_success": _pct(c["criteria_and_success"], success_total),
        },
        "opened_never_moved": {  # (b) dist≈0 & dur>dur_thr
            "count": c["zero_distance_long_duration"],
        },
        "failed_total": failed_total,
        "success_total": success_total,
    }


def failure_criteria_whatif(repo, scope=None, whatif=None, default=(120, 60)) -> dict:
    """Gerçek eşik (default) ile what-if eşiğinin DOĞRULAMA ORANI karşılaştırması.

    "Doğrulama oranı" = kaydedilmiş başarısızların (BASARISIZ_HARD) yüzde kaçı eşiğe
    (süre<x VE mesafe<y) uyuyor. İki senaryoyu yan yana koyup delta'yı döner:
      * confirm_pct_points : what-if oranı − gerçek oran (yüzde PUAN farkı)
      * confirm_count_delta: kritere uyan başarısız SAYI farkı
      * rel_pct            : göreli değişim (%) = puan farkı / gerçek oran

    `whatif` = (süre_sn, mesafe_m) çifti; None ise ValueError.
    """
    if whatif is None:
        raise ValueError("failure_criteria_whatif: whatif=(süre, mesafe) zorunlu.")
    real = failure_criteria_check(repo, scope, default[0], default[1])
    what = failure_criteria_check(repo, scope, whatif[0], whatif[1])
    real_pct = real["failed_meeting_criterion"]["pct_of_failed"]
    what_pct = what["failed_meeting_criterion"]["pct_of_failed"]
    real_cnt = real["failed_meeting_criterion"]["count"]
    what_cnt = what["failed_meeting_criterion"]["count"]
    return {
        "real": real,
        "whatif": what,
        "delta": {
            "confirm_pct_points": round(what_pct - real_pct, 1),
            "confirm_count_delta": what_cnt - real_cnt,
            "rel_pct": round(100.0 * (what_pct - real_pct) / real_pct, 1) if real_pct else 0.0,
        },
    }


def vehicle_hotspots(repo, scope=None, min_failures: int = 10) -> dict:
    """En çok sorun/arıza çıkaran kronik scooter'ları listeler."""
    rows = repo.vehicle_failure_counts(scope, min_failures)
    return {"min_failures": min_failures, "vehicles": rows}


def control_group_comparison(repo, scope=None) -> dict:
    """A/B Testi mantığıyla 3 grubun (ariza-metinli, bildirimli, bildirimsiz) 'healthy' oranlarını kıyaslar.
    
    Bildirimsiz grup bizim "Control Group" (Kontrol Grubu) verimizdir. Eğer bildirim atan araçlar,
    hiç bildirim almayan araçlardan daha az toparlanabiliyorsa; bu bildirimlerin gerçek bir 
    arıza sinyali taşıdığını matematiksel olarak ispatlamış oluruz.
    """
    rows = repo.control_group_stats(scope)
    groups = [
        {
            "group": r["group"],
            "total": r["total"],
            "healthy": r["healthy"],
            "healthy_rate_pct": _pct(r["healthy"], r["total"]),
        }
        for r in rows
    ]
    return {"groups": groups}


def false_fault_summary(repo, scope=None) -> dict:
    """Sahte arızaları verdict x hypothesis bazında kırar; boşa giden görev (wasted mission) sayısını döner.
    
    Not: Ops (operasyon) maliyet modeli tanımlanmamışsa sadece boşa giden görev sayısını basar,
    TL (para) bazında zarar hesabı göstermez.
    """
    rows = repo.false_fault_counts(scope)
    breakdown = [
        {
            "verdict": r["verdict"],
            "hypothesis": r["hypothesis"],
            "events": r["events"],
            "vehicles": r["vehicles"],
            "wasted_missions": r["wasted"],
        }
        for r in rows
    ]
    total_wasted = sum(r["wasted"] for r in rows)
    cost_rows = repo.ops_cost_rows(scope)
    cost = _cost_from_missions(breakdown, cost_rows) if cost_rows else None
    return {
        "breakdown": breakdown,
        "total_wasted_missions": total_wasted,
        "cost": cost,  # None → maliyet modeli boş; yalnızca görev sayısı geçerli
    }


def _cost_from_missions(breakdown: list[dict], cost_rows: list[dict]) -> dict:
    """Boşa giden saha görevlerinin maliyetini para birimi cinsinden (TL/Euro vb.) hesaplar.
    
    V1 Mantığı: Her sahte alarm için ortalama 3 görev (PICKUP, WORKSHOP, REDEPLOY) maliyeti 
    yakıt ve işçilik üzerinden toplanarak hesaplanır.
    """
    per_mission = {r["mission_type"]: r for r in cost_rows}
    total = 0.0
    currency = cost_rows[0].get("currency")
    wasted = sum(r["wasted_missions"] for r in breakdown)
    # Görev başı ortalama maliyet (labor+fuel) — 3 görev tipinin ortalaması
    unit_costs = [
        (m.get("labor_cost") or 0) + (m.get("fuel_cost") or 0) for m in per_mission.values()
    ]
    avg_unit = sum(unit_costs) / len(unit_costs) if unit_costs else 0.0
    total = round(avg_unit * wasted, 2)
    return {"amount": total, "currency": currency, "wasted_missions": wasted}


def subregion_hotspots(repo, scope=None, min_rides: int = 2000) -> dict:
    """Alt bölge bazında arıza ve sahte-alarm yoğunluğu (1000 sürüş başına).

    Regülasyon kaynaklı sahte alarmların mekânsal izini çıkarmak için kullanılır.
    """
    rows = repo.subregion_stats(scope, min_rides)
    out = []
    for r in rows:
        total = r["total_rides"]
        out.append(
            {
                "city": r["city"],
                "sub_region_code": r["sub_region_code"],
                "sub_region_name": r.get("sub_region_name"),
                "total_rides": total,
                "failed": r["failed"],
                "failure_rate_pct": _pct(r["failed"], total),
                "false_alarm_per_1000": round(1000.0 * r["false_alarm"] / total, 2)
                if total
                else 0.0,
            }
        )
    return {"min_rides": min_rides, "sub_regions": out}


def hour_region_breakdown(repo, scope=None) -> dict:
    """Saat ve bölge bazlı kırılım (başarısızlık oranı).
    
    Dikkat: Saatler YEREL saattir (DB'de 'AT TIME ZONE' ile çözülür). Örneğin 
    Makedonya'daki gece yarısı pik'leri bir timezone bug'ı DEĞİLDİR; tamamen 
    kullanıcı davranışıyla alakalıdır ve raporda olduğu gibi gösterilmelidir.
    """
    rows = repo.hour_region_counts(scope)
    out = [
        {
            "city": r["city"],
            "hour": r["hour"],
            "total": r["total"],
            "failed": r["failed"],
            "failure_rate_pct": _pct(r["failed"], r["total"]),
        }
        for r in rows
    ]
    return {"buckets": out}
