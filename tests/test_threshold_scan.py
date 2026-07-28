"""Eşik Taraması (threshold-scan) testleri — DB'siz, saf hesap."""

from datetime import datetime, timedelta

from binbin.core.threshold_scan import (
    BASELINE,
    DISTANCE_GRID,
    DURATION_GRID,
    grid_bounds,
    scan_thresholds,
)

_T0 = datetime(2026, 6, 1, 12, 0)


def _row(
    ride_id,
    duration,
    distance,
    *,
    end_reason_id=None,
    end_message=None,
    comment_text=None,
    rating=None,
    field_signal_reason_id=None,
    next_distance=None,
    next_gap_min=10,
    next_outcome="BASARILI",
):
    row = {
        "ride_id": ride_id,
        "vehicle_id": 1,
        "city_id": 1,
        "start_time": _T0,
        "end_time": _T0 + timedelta(seconds=duration),
        "duration_sec": duration,
        "distance_m": distance,
        "end_reason_id": end_reason_id,
        "end_message": end_message,
        "comment_text": comment_text,
        "rating": rating,
        "field_signal_reason_id": field_signal_reason_id,
    }
    if next_distance is not None:
        row["next_ride_id"] = ride_id + 1000
        row["next_start_time"] = row["end_time"] + timedelta(minutes=next_gap_min)
        row["next_distance_m"] = next_distance
        row["next_outcome"] = next_outcome
    return row


def test_grid_bounds_default_matches_dar_bant():
    assert grid_bounds() == (150.0, 80.0)


def test_baseline_is_a_grid_member():
    assert BASELINE == (120.0, 60.0)
    assert BASELINE[0] in DURATION_GRID
    assert BASELINE[1] in DISTANCE_GRID


def test_scan_produces_full_grid():
    scan = scan_thresholds([])
    assert len(scan["rows"]) == len(DURATION_GRID) * len(DISTANCE_GRID)


def test_empty_stream_has_no_zero_division_and_recommends_baseline():
    """Boş akışta paydalar sıfır — recall/precision/F1 0.0 dönmeli, patlamamalı.
    Tüm hücreler eşitse (f1=0, wasted=0) eşitlik kırıcı BASELINE'ı seçmeli
    (mesafe sıfır -> en yakın)."""
    scan = scan_thresholds([])
    assert scan["evaluated"] == 0
    assert scan["pool_size"] == 0
    assert scan["reported_in_pool"] == 0
    for row in scan["rows"]:
        assert row["precision_pct"] == 0.0
        assert row["recall_pct"] == 0.0
        assert row["f1_pct"] == 0.0
    assert scan["recommended"]["duration_threshold"] == BASELINE[0]
    assert scan["recommended"]["distance_threshold"] == BASELINE[1]
    # recommended baseline'a eşitse, aynası da baseline'ın kendisi olmalı.
    assert scan["conservative"]["duration_threshold"] == BASELINE[0]
    assert scan["conservative"]["distance_threshold"] == BASELINE[1]


def test_baseline_cell_matches_hand_calculation():
    """3 satır: biri bağımsız kanıtlı+sağlıklı-sonraki-sürüş (şüpheli sahte alarm),
    biri kanıtsız (bildirim yok), biri havuz dışı (süre >= 150). Baseline (120/60)
    hücresinde elle: flagged=2, reported=1, precision=%50, recall=%100, F1=%66,7."""
    rows = [
        _row(1, 50, 10, end_reason_id=33, next_distance=250, next_gap_min=10),
        _row(2, 50, 10),  # hiçbir kanıt yok -> BILDIRIM_YOK
        _row(3, 200, 10, end_reason_id=33, next_distance=250, next_gap_min=10),  # havuz dışı
    ]
    scan = scan_thresholds(rows)
    assert scan["evaluated"] == 3
    assert scan["pool_size"] == 2  # yalnız 1 ve 2
    assert scan["reported_in_pool"] == 1  # yalnız satır 1

    baseline = scan["baseline_row"]
    assert baseline["flagged"] == 2
    assert baseline["reported"] == 1
    assert baseline["precision_pct"] == 50.0
    assert baseline["recall_pct"] == 100.0
    assert baseline["f1_pct"] == 66.7
    assert baseline["suspect_false"] == 1
    assert baseline["wasted_missions"] == 3
    assert baseline["real_fault"] == 0


def test_flagged_is_monotonic_non_decreasing_across_grid():
    """Eşik büyüdükçe (süre veya mesafe) işaretlenen sürüş sayısı asla AZALMAZ —
    flagged bir üst-küme ilişkisidir."""
    rows = [_row(i, duration, distance) for i, (duration, distance) in enumerate(
        [(85, 35), (95, 45), (110, 55), (125, 65), (140, 75), (149, 79)]
    )]
    scan = scan_thresholds(rows)
    by_cell = {(r["duration_threshold"], r["distance_threshold"]): r["flagged"] for r in scan["rows"]}
    for m in DISTANCE_GRID:
        prev = -1
        for d in sorted(DURATION_GRID):
            current = by_cell[(d, m)]
            assert current >= prev
            prev = current
    for d in DURATION_GRID:
        prev = -1
        for m in sorted(DISTANCE_GRID):
            current = by_cell[(d, m)]
            assert current >= prev
            prev = current


def test_row_outside_pool_never_flagged_in_any_cell():
    rows = [_row(1, 200, 200)]  # havuzun (150/80) tamamen dışında
    scan = scan_thresholds(rows)
    assert scan["pool_size"] == 0
    assert all(r["flagged"] == 0 for r in scan["rows"])


def test_wasted_missions_counted_only_for_suspect_false():
    """`suspect_false`/boşa görev yalnız SAHTE_ALARM_SUPHESI hükmünde sayılır; gerçek
    arıza şüphesi (healthy_proof yok) boşa göreve girmez."""
    rows = [
        # bağımsız kanıt var, sonraki sürüş sağlıklı DEĞİL (healthy_proof yok) -> gerçek arıza şüphesi
        _row(1, 50, 10, end_reason_id=33, next_distance=5, next_gap_min=10),
        # bağımsız kanıt var, sonraki sürüş sağlıklı -> şüpheli sahte alarm
        _row(2, 50, 10, end_reason_id=33, next_distance=250, next_gap_min=10),
    ]
    scan = scan_thresholds(rows)
    baseline = scan["baseline_row"]
    assert baseline["reported"] == 2
    assert baseline["real_fault"] == 1
    assert baseline["suspect_false"] == 1
    assert baseline["wasted_missions"] == 3


def test_recommended_prefers_baseline_on_full_tie():
    """Tüm hücreler istatistiksel olarak eşitse (aday havuzu boş) eşitlik kırıcı
    Mevcut Kural'a en yakın hücreyi seçmeli — kural gereksiz yere uzaklaşmamalı."""
    scan = scan_thresholds([_row(1, 200, 200)])  # havuz dışı, tüm hücreler f1=0
    rec = scan["recommended"]
    assert (rec["duration_threshold"], rec["distance_threshold"]) == BASELINE


def test_conservative_is_mirror_of_recommended_across_baseline():
    """`conservative`, `recommended`'ın Mevcut Kural'a göre ayna simetriği olmalı —
    ayrı bir optimizasyonla değil, geometrik türetmeyle (bkz. modül dokstringi:
    isabet ızgarada sabit olduğu için 'boşa görev azaltma' yönünde tek başına bir
    optimum yok)."""
    rows = [
        _row(1, 50, 10, end_reason_id=33, next_distance=250, next_gap_min=10),
        _row(2, 60, 20, comment_text="arizali motor calismiyor"),
        _row(3, 70, 30, end_reason_id=46, next_distance=5, next_gap_min=10),
    ]
    scan = scan_thresholds(rows)
    rec = scan["recommended"]
    cons = scan["conservative"]
    assert cons["duration_threshold"] == 2 * BASELINE[0] - rec["duration_threshold"]
    assert cons["distance_threshold"] == 2 * BASELINE[1] - rec["distance_threshold"]
    # Ayna her zaman ızgaranın İÇİNDE bir satıra denk gelmeli (nearest-neighbor değil).
    assert cons["duration_threshold"] in DURATION_GRID
    assert cons["distance_threshold"] in DISTANCE_GRID


def test_scan_deltas_are_relative_to_baseline():
    rows = [
        _row(1, 50, 10, end_reason_id=33, next_distance=250, next_gap_min=10),
        _row(2, 50, 10),
    ]
    scan = scan_thresholds(rows)
    baseline = scan["baseline_row"]
    assert baseline["f1_pp_delta"] == 0.0
    assert baseline["flagged_delta"] == 0
    assert baseline["wasted_delta"] == 0
    # Daha büyük bir hücre (150/80) en az bu kadar sürüşü işaretlemeli.
    widest = next(
        r for r in scan["rows"]
        if r["duration_threshold"] == 150.0 and r["distance_threshold"] == 80.0
    )
    assert widest["flagged"] >= baseline["flagged"]
    assert widest["flagged_delta"] >= 0
