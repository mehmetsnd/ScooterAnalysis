"""Eşik Taraması — Özel Kural'ın süre/mesafe eşiklerini F1 ile tarar (saf, DB'siz).

Eşik değiştirmek sürüşü başarılı/başarısız YAPMAZ, yalnız "başarısız" damgasını değiştirir.
Bu yüzden hedef, kuralın eşikten BAĞIMSIZ arıza kanıtını (`_report_evidence`: metin /
durum defteri / 1 yıldız / end_reason) ne kadar isabetle yakaladığıdır.
Kanıt tanımının kendisi de ölçülür: yıldız eşiğinin neden 1'de kaldığı `false_fault.
assess_ride`'da, kelime kümelerinin ayırt ediciliği `core/keyword_audit.py`'de
(`analyze --kelime-denetimi`) sayılarla kayıtlıdır.

Satır başına BİR KEZ `assess_ride` çağrılır (kopyalanmaz), ızgara hücreleri arasında
tekrar edilmez: `fault_reported` ve `healthy_proof` eşikten bağımsızdır — ızgara max
mesafesi (80 m) < healthy_proof eşiği (200 m) olduğu için yapısal garanti, kod ASSERT eder.
Hücreye göre değişen tek şey `flagged = duration < d AND distance < m`.

Metrikler (aday havuzu = ızgara maksimumlarının altındaki sürüşler): precision/recall/f1.
`suspect_false`/`wasted_missions` F1'e GİRMEZ, ayrı raporlanır. Recall'un paydası yalnız
havuzdur; havuz dışı sürüşler hiçbir hücrede işaretlenemez — dar bandın yapısal sonucu.

İKİ SENARYO: precision ızgarada neredeyse sabit (~%19,7–%20,4), yani hiçbir yönde "daha
akıllı" nokta yok — yalnız hacim/kapsam değişiyor. Tek optimum yerine iki karşıt hedef:
    recommended  : F1 en yüksek hücre — "kaçırılan kanıtlı şikayeti en aza indir".
    conservative : recommended'ın baseline'a göre AYNALANMIŞ karşılığı — "işaretlenen
                   hacmi en aza indir". Ayrı optimizasyonla SEÇİLMEDİ (öyle bir optimum
                   yok); ızgara baseline etrafında simetrik olduğundan hep içinde kalır.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from binbin.core.false_fault import FALSE_ALARM_WASTED_MISSIONS, assess_ride
from binbin.core.ratios import enum_or_none as _enum_or_none, pct as _pct
from binbin.domain.enums import FaultVerdict, RideOutcome
from binbin.domain.models import Ride

DURATION_GRID: tuple[float, ...] = (90.0, 105.0, 120.0, 135.0, 150.0)
DISTANCE_GRID: tuple[float, ...] = (40.0, 50.0, 60.0, 70.0, 80.0)
BASELINE: tuple[float, float] = (120.0, 60.0)  # scenario_analysis.CURRENT_* ile aynı
_HEALTHY_MIN_DISTANCE_M = 200.0  # assess_ride varsayılanı; ızgara max mesafesinden BÜYÜK olmalı

assert BASELINE[0] in DURATION_GRID and BASELINE[1] in DISTANCE_GRID, (
    "Baseline (120/60) ızgarada bulunmalı — karşılaştırma referans satırı gerektirir."
)
assert max(DISTANCE_GRID) < _HEALTHY_MIN_DISTANCE_M, (
    "Işgara max mesafesi healthy_proof eşiğinin (200 m) altında kalmalı — aksi hâlde "
    "healthy_proof hücreye göre değişir ve tek-geçiş varsayımı bozulur."
)


def grid_bounds(
    duration_grid: tuple[float, ...] = DURATION_GRID,
    distance_grid: tuple[float, ...] = DISTANCE_GRID,
) -> tuple[float, float]:
    """Sinyal-join'in çalışması GEREKEN üst sınır — `scenario_analysis.candidate_bounds`
    ile aynı sözleşme (satırın hiçbir ızgara hücresinde işaretlenemeyeceği eşik)."""
    return (max(duration_grid), max(distance_grid))


def _f1(precision_pct: float, recall_pct: float) -> float:
    if precision_pct <= 0 and recall_pct <= 0:
        return 0.0
    return round(2 * precision_pct * recall_pct / (precision_pct + recall_pct), 1)


def _row_ride(row: Mapping) -> Ride:
    return Ride(
        ride_id=int(row["ride_id"]),
        source_ref=str(row.get("source_ref") or ""),
        vehicle_id=int(row.get("vehicle_id") or 0),
        city_id=int(row.get("city_id") or 0),
        user_ref=str(row.get("user_ref") or ""),
        start_time=row["start_time"],
        outcome=RideOutcome.BASARISIZ_HARD,
        end_time=row.get("end_time"),
        duration_sec=float(row["duration_sec"]),
        distance_m=float(row["distance_m"]),
        end_reason_id=row.get("end_reason_id"),
        end_message=row.get("end_message"),
    )


def _row_next_ride(row: Mapping) -> Optional[Ride]:
    next_ride_id = row.get("next_ride_id")
    if next_ride_id is None:
        return None
    next_outcome = _enum_or_none(RideOutcome, row.get("next_outcome"))
    return Ride(
        ride_id=int(next_ride_id),
        source_ref="",
        vehicle_id=int(row.get("vehicle_id") or 0),
        city_id=int(row.get("city_id") or 0),
        user_ref="",
        start_time=row["next_start_time"],
        outcome=next_outcome or RideOutcome.BASARISIZ_HARD,
        distance_m=row.get("next_distance_m"),
    )


@dataclass
class _CellAccumulator:
    flagged: int = 0
    flagged_reported: int = 0
    suspect_false: int = 0
    real_fault: int = 0
    wasted_missions: int = 0


class ThresholdScanAccumulator:
    """Tek geçişte ızgaranın tamamını biriktirir. `feed(row)` satır satır çağrılır
    (ör. `scenario_analysis.analyze_scenarios`'un kendi döngüsünden, timeline'ı ikinci
    kez tüketmeden); `finalize()` bir kez, sonda çağrılır.
    """

    def __init__(
        self,
        duration_grid: tuple[float, ...] = DURATION_GRID,
        distance_grid: tuple[float, ...] = DISTANCE_GRID,
    ) -> None:
        self.duration_grid = duration_grid
        self.distance_grid = distance_grid
        self.pool_dur, self.pool_dist = grid_bounds(duration_grid, distance_grid)
        self.evaluated = 0
        self.pool_size = 0
        self.reported_in_pool = 0
        self._cells: dict[tuple[float, float], _CellAccumulator] = {
            (d, m): _CellAccumulator() for d in duration_grid for m in distance_grid
        }

    def feed(self, row: Mapping) -> None:
        duration = row.get("duration_sec")
        distance = row.get("distance_m")
        if duration is None or distance is None:
            return
        self.evaluated += 1
        duration = float(duration)
        distance = float(distance)
        if not (duration < self.pool_dur and distance < self.pool_dist):
            return
        self.pool_size += 1

        ride = _row_ride(row)
        assessment = assess_ride(
            ride,
            _row_next_ride(row),
            comment_text=row.get("comment_text"),
            rating=row.get("rating"),
            field_fault=row.get("field_signal_reason_id") is not None,
            healthy_min_distance_m=_HEALTHY_MIN_DISTANCE_M,
        )
        reported = assessment.fault_reported
        if reported:
            self.reported_in_pool += 1
        suspect_false = assessment.verdict is FaultVerdict.SAHTE_ALARM_SUPHESI
        real_fault = assessment.verdict is FaultVerdict.GERCEK_ARIZA_SUPHESI

        for d in self.duration_grid:
            if duration >= d:
                continue
            for m in self.distance_grid:
                if distance >= m:
                    continue
                cell = self._cells[(d, m)]
                cell.flagged += 1
                if reported:
                    cell.flagged_reported += 1
                if suspect_false:
                    cell.suspect_false += 1
                    cell.wasted_missions += FALSE_ALARM_WASTED_MISSIONS
                if real_fault:
                    cell.real_fault += 1

    def finalize(self) -> dict:
        rows_out = []
        for (d, m), cell in self._cells.items():
            precision = _pct(cell.flagged_reported, cell.flagged)
            recall = _pct(cell.flagged_reported, self.reported_in_pool)
            rows_out.append(
                {
                    "duration_threshold": d,
                    "distance_threshold": m,
                    "is_baseline": (d, m) == BASELINE,
                    "flagged": cell.flagged,
                    "failure_rate_pct": _pct(cell.flagged, self.evaluated),
                    "reported": cell.flagged_reported,
                    "real_fault": cell.real_fault,
                    "suspect_false": cell.suspect_false,
                    "wasted_missions": cell.wasted_missions,
                    "precision_pct": precision,
                    "recall_pct": recall,
                    "f1_pct": _f1(precision, recall),
                }
            )
        rows_out.sort(key=lambda r: (r["duration_threshold"], r["distance_threshold"]))

        baseline_row = next(r for r in rows_out if r["is_baseline"])
        for r in rows_out:
            r["f1_pp_delta"] = round(r["f1_pct"] - baseline_row["f1_pct"], 1)
            r["flagged_delta"] = r["flagged"] - baseline_row["flagged"]
            r["wasted_delta"] = r["wasted_missions"] - baseline_row["wasted_missions"]

        def _tie_break(r: dict) -> tuple:
            dist_to_baseline = (
                (r["duration_threshold"] - BASELINE[0]) ** 2
                + (r["distance_threshold"] - BASELINE[1]) ** 2
            ) ** 0.5
            return (-r["f1_pct"], r["wasted_missions"], dist_to_baseline)

        recommended = min(rows_out, key=_tie_break)

        # Simetrik ayna (bkz. modül dokstringi "İKİ SENARYO") — ızgara baseline etrafında
        # simetrik olduğundan bu her zaman bir satıra denk gelir.
        mirror_key = (
            2 * BASELINE[0] - recommended["duration_threshold"],
            2 * BASELINE[1] - recommended["distance_threshold"],
        )
        by_key = {(r["duration_threshold"], r["distance_threshold"]): r for r in rows_out}
        conservative = by_key.get(mirror_key, baseline_row)

        return {
            "duration_grid": list(self.duration_grid),
            "distance_grid": list(self.distance_grid),
            "baseline": {"duration": BASELINE[0], "distance": BASELINE[1]},
            "evaluated": self.evaluated,
            "pool_bounds": {"duration": self.pool_dur, "distance": self.pool_dist},
            "pool_size": self.pool_size,
            "reported_in_pool": self.reported_in_pool,
            "rows": rows_out,
            "baseline_row": baseline_row,
            "recommended": recommended,
            "conservative": conservative,
        }


def scan_thresholds(
    rows: Iterable[Mapping],
    duration_grid: tuple[float, ...] = DURATION_GRID,
    distance_grid: tuple[float, ...] = DISTANCE_GRID,
) -> dict:
    """Tüm ızgarayı tek veri geçişinde tarar (bağımsız kullanım — testler ve tek-başına
    CLI çağrıları için). `rows`, `analysis_timeline`'ın aynı akışıdır (stream generator)
    — burada tükenir, tekrar tüketilemez.
    """
    acc = ThresholdScanAccumulator(duration_grid, distance_grid)
    for row in rows:
        acc.feed(row)
    return acc.finalize()
