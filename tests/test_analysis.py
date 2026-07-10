"""core/analysis testleri — bellek-içi sahte repo ile, DB'siz."""

from binbin.core import analysis


class _FakeRepo:
    """AnalysisRepository davranışının test dublörü (yalnız gereken metotlar)."""

    def failure_criteria_counts(self, scope=None) -> dict:
        return {
            "failed_total": 1000,
            "success_total": 9000,
            "criteria_and_failed": 800,
            "criteria_and_success": 90,
            "zero_distance_long_duration": 12,
        }

    def control_group_stats(self, scope=None) -> list[dict]:
        return [
            {"group": "ariza_metinli", "total": 200, "healthy": 59},         # %29.5
            {"group": "herhangi_bildirimli", "total": 400, "healthy": 140},  # %35.0
            {"group": "bildirimsiz", "total": 1000, "healthy": 422},         # %42.2
        ]


def test_control_group_comparison_oranlar():
    d = analysis.control_group_comparison(_FakeRepo())
    rates = {g["group"]: g["healthy_rate_pct"] for g in d["groups"]}
    assert rates["ariza_metinli"] == 29.5
    assert rates["herhangi_bildirimli"] == 35.0
    assert rates["bildirimsiz"] == 42.2
    # Bildirimli gruplar bildirimsizden DAHA AZ toparlanıyor
    assert rates["ariza_metinli"] < rates["bildirimsiz"]


def test_failure_criteria_check_kuyruklar():
    d = analysis.failure_criteria_check(_FakeRepo())
    assert d["failed_meeting_criterion"]["count"] == 800
    assert d["failed_meeting_criterion"]["pct_of_failed"] == 80.0
    assert d["hidden_failed"]["count"] == 90
    assert d["hidden_failed"]["pct_of_success"] == 1.0
    assert d["opened_never_moved"]["count"] == 12


def test_bos_repo_ile_dict_doner():
    class _Empty:
        def failure_category_counts(self, scope=None):
            return []

    d = analysis.cause_distribution(_Empty())
    assert isinstance(d, dict)
    assert d["total_failed"] == 0
