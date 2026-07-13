"""core/analysis testleri — bellek-içi sahte repo ile, DB'siz."""

from binbin.core import analysis


class _FakeRepo:
    """AnalysisRepository davranışının test dublörü (yalnız gereken metotlar)."""

    def failure_criteria_counts(self, scope=None, dur_thr=120, dist_thr=60) -> dict:
        # Eşik daraldıkça (120/60'tan küçük) kritere uyan sayı azalır — deterministik.
        tight = dur_thr < 120 or dist_thr < 60
        return {
            "failed_total": 1000,
            "success_total": 9000,
            "criteria_and_failed": 600 if tight else 800,
            "criteria_and_success": 60 if tight else 90,
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


def test_failure_criteria_whatif_delta():
    # Gerçek (120/60): 800/1000 = %80. What-if (100/45): 600/1000 = %60.
    w = analysis.failure_criteria_whatif(_FakeRepo(), whatif=(100, 45))
    assert w["real"]["failed_meeting_criterion"]["pct_of_failed"] == 80.0
    assert w["whatif"]["failed_meeting_criterion"]["pct_of_failed"] == 60.0
    assert w["delta"]["confirm_pct_points"] == -20.0     # 60 - 80 puan
    assert w["delta"]["confirm_count_delta"] == -200      # 600 - 800 adet
    assert w["delta"]["rel_pct"] == -25.0                 # -20 / 80 * 100
    # Eşikler çıktıya yansımalı (gösterim için)
    assert w["whatif"]["duration_threshold"] == 100
    assert w["whatif"]["distance_threshold"] == 45


def test_failure_criteria_whatif_zorunlu_whatif():
    import pytest

    with pytest.raises(ValueError):
        analysis.failure_criteria_whatif(_FakeRepo(), whatif=None)


def test_bos_repo_ile_dict_doner():
    class _Empty:
        def failure_category_counts(self, scope=None):
            return []

    d = analysis.cause_distribution(_Empty())
    assert isinstance(d, dict)
    assert d["total_failed"] == 0
