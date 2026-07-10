"""Mock veri kaynağı — `--source mock` için DB'siz AnalysisRepository.

Data Source soyutlamasının ispatı: `analyze --source mock` DB'ye HİÇ bağlanmadan
çalışır. Döndürülen agregeler küçük ama gerçekçidir (Haziran 2026 İstanbul temel
çizgisine yakın); core/analysis onları aynı şekilde işler.
"""

from binbin.config import Scope
from binbin.data.repository import AnalysisScope


class MockRideRepository:
    """AnalysisRepository Protocol'ünün bellek-içi (sabit veri) implementasyonu."""

    def resolve_scope(self, scope: Scope) -> AnalysisScope:
        # Mock veri zaten tek şehir/dönemdir; kapsam çözümü no-op.
        return AnalysisScope(None, None)

    def failure_category_counts(self, scope) -> list[dict]:
        return [
            {"category": None, "count": 18990},  # sinyalsiz (~%89)
            {"category": "TEKNIK", "count": 1450},
            {"category": "REGULASYON", "count": 520},
            {"category": "KULLANICI", "count": 210},
            {"category": "SISTEM", "count": 110},
            {"category": "ODEME", "count": 33},
        ]

    def failure_criteria_counts(self, scope) -> dict:
        return {
            "failed_total": 21313,
            "success_total": 385501,
            "criteria_and_failed": 15980,
            "criteria_and_success": 640,
            "zero_distance_long_duration": 205,
        }

    def vehicle_failure_counts(self, scope, min_failures: int) -> list[dict]:
        rows = [
            {"vehicle_id": 3738, "external_code": "MPC2", "failures": 34},
            {"vehicle_id": 4636, "external_code": "DAYN", "failures": 28},
            {"vehicle_id": 5011, "external_code": "K9QT", "failures": 21},
            {"vehicle_id": 6120, "external_code": "ZR44", "failures": 12},
        ]
        return [r for r in rows if r["failures"] >= min_failures]

    def control_group_stats(self, scope) -> list[dict]:
        # İstanbul temel çizgisi: %29,5 / %35,0 / %42,2
        return [
            {"group": "ariza_metinli", "total": 1731, "healthy": 511},
            {"group": "herhangi_bildirimli", "total": 3120, "healthy": 1092},
            {"group": "bildirimsiz", "total": 18193, "healthy": 7677},
        ]

    def false_fault_counts(self, scope) -> list[dict]:
        return [
            {"verdict": "BILDIRIM_YOK", "hypothesis": "BELIRSIZ",
             "events": 18193, "vehicles": 9800, "wasted": 0},
            {"verdict": "GERCEK_ARIZA_SUPHESI", "hypothesis": "BELIRSIZ",
             "events": 1220, "vehicles": 1150, "wasted": 0},
            {"verdict": "SAHTE_ALARM_SUPHESI", "hypothesis": "GECICI_TEKNIK",
             "events": 292, "vehicles": 280, "wasted": 876},
            {"verdict": "SAHTE_ALARM_SUPHESI", "hypothesis": "REGULASYON_SUPHESI",
             "events": 219, "vehicles": 210, "wasted": 657},
        ]

    def subregion_stats(self, scope, min_rides: int) -> list[dict]:
        rows = [
            {"city": "İstanbul Avrupa", "sub_region_code": 599, "sub_region_name": None,
             "total_rides": 14514, "failed": 980, "false_alarm": 88},
            {"city": "İstanbul Anadolu", "sub_region_code": 605, "sub_region_name": None,
             "total_rides": 5200, "failed": 260, "false_alarm": 21},
        ]
        return [r for r in rows if r["total_rides"] >= min_rides]

    def hour_region_counts(self, scope) -> list[dict]:
        return [
            {"city": "İstanbul Avrupa", "hour": h, "total": 1000 + 40 * h,
             "failed": 40 + 3 * (h % 6)}
            for h in range(24)
        ]

    def ops_cost_rows(self, scope) -> list[dict]:
        # ops_cost_model gerçekte boştur → mock da boş döner (TL raporlanmaz).
        return []
