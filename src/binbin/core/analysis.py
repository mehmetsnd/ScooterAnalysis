"""Analiz fonksiyonları — functional core: SAF, I/O yok.

`repo` bir RideQueryRepository (data/repository.py) davranışını sağlayan nesnedir;
core→data import bağımlılığı doğmasın diye burada **tipsiz** bırakıldı. Aynı
sebeple `scope` da tipsizdir (AnalysisScope | None) — repo'ya olduğu gibi iletilir,
`None` = filtre yok. Varsayılan kapsam (DEFAULT_SCOPE) imperative shell'de (cli)
uygulanır; core burada politika bilmez.

Ağır agregasyon repo/SQL'dedir; buradaki fonksiyonlar yalnızca yüzde/oran ekleyip
raporlama dict'ine dönüştürür.
"""


def _pct(part: int, whole: int) -> float:
    """Yüzde (bir ondalık); payda 0 ise 0.0."""
    return round(100.0 * part / whole, 1) if whole else 0.0


def cause_distribution(repo, scope=None) -> dict:
    """Başarısız sürüşlerin kategori dağılımı + sinyalsiz oranı AYRI satır olarak.

    Sinyalsiz (failure_category IS NULL) kütle uydurulmaz; kendi satırında raporlanır.
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
    """`duration_sec < 120 AND distance_m < 60` kriterinin outcome ile uyumu.

    İki kuyruk ayrı sayılır:
      (a) gizli başarısız = BASARILI ama kritere uyan,
      (b) araç açıldı hiç gitmedi = distance_m ≈ 0 AND duration_sec > 120.
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
    """En çok başarısızlık üreten araçlar (eşik: min_failures)."""
    rows = repo.vehicle_failure_counts(scope, min_failures)
    return {"min_failures": min_failures, "vehicles": rows}


def control_group_comparison(repo, scope=None) -> dict:
    """Üç grubun healthy_proof oranını yan yana verir (ZORUNLU rapor).

    Gruplar: arıza-metinli / herhangi-bildirimli / bildirimsiz (KONTROL GRUBU).
    Kontrol grubu olmadan sahte-alarm sayısı yanıltıcıdır: bildirimli araçlar
    bildirimsizlere göre DAHA AZ toparlanıyorsa, bildirimler gerçek sinyal taşır.
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
    """verdict × hypothesis kırılımı, olay/farklı-araç sayısı, boşa görev toplamı.

    Maliyet: ops_cost_model boşsa "N boşa görev" raporlanır, TL raporlanmaz.
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
    """ops_cost_model doluysa boşa görev maliyetini hesaplar (para birimiyle).

    Basit v1: sahte-alarm başına 3 görev (PICKUP+WORKSHOP+REDEPLOY) maliyeti toplanır.
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
    """Alt bölge (daima (şehir, kod) çifti) başına başarısızlık + sahte-alarm yoğunluğu.

    Yoğunluk 1000 sürüş başınadır. Regülasyon hipotezinin mekânsal kanıtı.
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
    """Yerel saat × şehir kırılımında başarısızlık oranı.

    Saatler YEREL saattir (repo `AT TIME ZONE country.timezone` uygular). K.Makedonya'da
    gece yarısı zirvesi saat dilimi hatası DEĞİLDİR (yalnızca 23:00'e kayar); gerçek
    davranışsal bulgudur ve böyle raporlanır.
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
