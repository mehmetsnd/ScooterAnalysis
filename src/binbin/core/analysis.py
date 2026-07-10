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


def failure_criteria_check(repo, scope=None) -> dict:
    """Sistemdeki 'Başarısız' kuralının (süre < 120sn VE mesafe < 60m) outcome ile kıyası.
    
    Burada 2 farklı edge-case (kuyruk) tespit ediyoruz:
      a) Gizli Başarısız: Veritabanında SUCCESS dönmüş ama kriterlere göre aslında başarısız.
      b) Hiç Gitmemiş: Mesafe 0 ama sürüş 120 saniyeden uzun sürmüş (açıp unutmuş olabilir).
    """
    c = repo.failure_criteria_counts(scope)
    failed_total = c["failed_total"]
    success_total = c["success_total"]
    return {
        "criterion": "duration_sec < 120 AND distance_m < 60",
        "failed_meeting_criterion": {
            "count": c["criteria_and_failed"],
            "pct_of_failed": _pct(c["criteria_and_failed"], failed_total),
        },
        "hidden_failed": {  # (a) BASARILI ama kritere uyan
            "count": c["criteria_and_success"],
            "pct_of_success": _pct(c["criteria_and_success"], success_total),
        },
        "opened_never_moved": {  # (b) dist≈0 & dur>120
            "count": c["zero_distance_long_duration"],
        },
        "failed_total": failed_total,
        "success_total": success_total,
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
    """Alt bölgeler (ilçe/mahalle) bazında arıza ve sahte-alarm yoğunluğunu (hotspot) verir.
    
    Rate: 1000 sürüş başınadır. Özellikle regülasyon kaynaklı sahte alarmları 
    harita üzerinde ispatlamak için harika bir veridir.
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
