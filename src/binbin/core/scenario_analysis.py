"""İki senaryolu, yazmasız başarısız sürüş analizi.

Repository'den araç/zaman sıralı bir timeline okunur. Her sürüş aynı geçişte
Mevcut Kural ve Özel Kural için değerlendirilir.
Classifier/assessor kuralları kopyalanmaz; mevcut saf core fonksiyonları yeniden
kullanılır.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Iterable, Mapping

from binbin.core.classifier import classify_ride
from binbin.core.false_fault import assess_ride
from binbin.domain.enums import (
    ClassificationSource,
    FailureCategory,
    FailureReason,
    FalseFaultHypothesis,
    FaultVerdict,
    PaymentStatus,
    RideOutcome,
)
from binbin.domain.models import Ride


CURRENT_DURATION_SEC = 120.0
CURRENT_DISTANCE_M = 60.0

# Sıcak nokta eşikleri — istatistiksel anlamlılık için asgari kütle.
MIN_SUBREGION_RIDES = 2000    # alt bölge için asgari toplam sürüş
MIN_VEHICLE_FAILURES = 10     # araç için asgari başarısızlık


class ScenarioStatus(str, Enum):
    FAILED = "FAILED"
    SUCCESS = "SUCCESS"
    UNEVALUATED = "UNEVALUATED"


@dataclass(frozen=True)
class FailureScenario:
    key: str
    label: str
    duration_threshold: float
    distance_threshold: float
    include_source_failure: bool

    def status(self, outcome, duration_sec, distance_m) -> ScenarioStatus:
        """Sürüşün bu senaryodaki durumunu döndürür.

        Mevcut kuralda kaynak BASARISIZ_HARD her zaman başarısızdır.
        Özel kuralda eksik ölçüm başarı değil, değerlendirilemedi sonucudur.
        """
        source_failed = _outcome_value(outcome) == RideOutcome.BASARISIZ_HARD.value
        if self.include_source_failure and source_failed:
            return ScenarioStatus.FAILED
        if duration_sec is None or distance_m is None:
            return (
                ScenarioStatus.SUCCESS
                if self.include_source_failure
                else ScenarioStatus.UNEVALUATED
            )
        failed = (
            float(duration_sec) < self.duration_threshold
            and float(distance_m) < self.distance_threshold
        )
        return ScenarioStatus.FAILED if failed else ScenarioStatus.SUCCESS


def build_scenarios(
    custom: tuple[float, float] | None,
) -> tuple[FailureScenario, ...]:
    scenarios = [
        FailureScenario(
            key="current_rule",
            label="Mevcut Kural",
            duration_threshold=CURRENT_DURATION_SEC,
            distance_threshold=CURRENT_DISTANCE_M,
            include_source_failure=True,
        )
    ]
    if custom is not None:
        duration, distance = map(float, custom)
        if not isfinite(duration) or not isfinite(distance) or duration <= 0 or distance <= 0:
            raise ValueError("Süre ve mesafe eşikleri sonlu ve sıfırdan büyük olmalı.")
        scenarios.append(
            FailureScenario(
                key="custom_rule",
                label="Özel Kural",
                duration_threshold=duration,
                distance_threshold=distance,
                include_source_failure=False,
            )
        )
    return tuple(scenarios)


def candidate_bounds(scenarios: tuple[FailureScenario, ...]) -> tuple[float, float]:
    """Sinyal-join'in çalışması GEREKEN sürüşleri sınırlayan (süre, mesafe) üst sınırı.

    NEDEN: sinyal alanları yalnız BAŞARISIZ sürüşlerde okunur (`analyze_scenarios`
    içinde `acc["failed"]` bloğu). Başarısız kümesi tüm sürüşlerin ~%6'sı olduğu için
    sinyali 1M satırın hepsi için hesaplamak boşa iştir — repository katmanı bu sınırı
    kullanıp LATERAL'i aday olmayan satırlarda hiç çalıştırmaz.

    ÜSTKÜME GARANTİSİ: bir sürüş herhangi bir senaryoda başarısızsa ya kaynak-başarısızdır
    (guard'ın ilk dalı) ya da o senaryonun eşiklerinin ALTINDADIR; eşiklerin MAKSİMUMUNU
    almak bu kümeyi kapsar. Eşikler senaryolardan türetilir, sabit yazılmaz — yeni bir
    senaryo eklenirse sınır kendiliğinden genişler.
    """
    return (
        max(s.duration_threshold for s in scenarios),
        max(s.distance_threshold for s in scenarios),
    )


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _outcome_value(value) -> str:
    return value.value if isinstance(value, RideOutcome) else str(value)


def _enum_or_none(enum_cls, value):
    if value is None or isinstance(value, enum_cls):
        return value
    return enum_cls(value)


def _scenario_ride(row: Mapping, *, prefix: str = "") -> Ride | None:
    ride_id = row.get(f"{prefix}ride_id")
    if ride_id is None:
        return None
    distance = row.get(f"{prefix}distance_m")
    duration = row.get(f"{prefix}duration_sec")
    return Ride(
        ride_id=int(ride_id),
        source_ref=str(row.get(f"{prefix}source_ref") or ""),
        vehicle_id=int(row.get("vehicle_id") or 0),
        city_id=int(row.get("city_id") or 0),
        user_ref=str(row.get(f"{prefix}user_ref") or ""),
        start_time=row[f"{prefix}start_time"],
        outcome=RideOutcome.BASARISIZ_HARD,
        end_time=row.get(f"{prefix}end_time"),
        duration_sec=float(duration) if duration is not None else None,
        distance_m=float(distance) if distance is not None else None,
        triggered_regulation_id=row.get(f"{prefix}triggered_regulation_id"),
        end_reason_id=row.get(f"{prefix}end_reason_id"),
        end_message=row.get(f"{prefix}end_message"),
        unlock_ack=row.get(f"{prefix}unlock_ack"),
        start_battery_pct=row.get(f"{prefix}start_battery_pct"),
        connection_lost=row.get(f"{prefix}connection_lost"),
        motor_error_code=row.get(f"{prefix}motor_error_code"),
        bms_error_code=row.get(f"{prefix}bms_error_code"),
        user_cancelled=row.get(f"{prefix}user_cancelled"),
        payment_status=_enum_or_none(PaymentStatus, row.get(f"{prefix}payment_status")),
    )


def _next_ride(row: Mapping, scenario: FailureScenario) -> Ride | None:
    ride = _scenario_ride(row, prefix="next_")
    if ride is None:
        return None
    status = scenario.status(
        row.get("next_outcome"),
        row.get("next_duration_sec"),
        row.get("next_distance_m"),
    )
    # Değerlendirilemeyen sonraki sürüş sağlamlık kanıtı üretemez.
    ride.outcome = (
        RideOutcome.BASARILI
        if status is ScenarioStatus.SUCCESS
        else RideOutcome.BASARISIZ_HARD
    )
    return ride


def _new_accumulator(scenario: FailureScenario) -> dict:
    return {
        "key": scenario.key,
        "label": scenario.label,
        "duration_threshold": scenario.duration_threshold,
        "distance_threshold": scenario.distance_threshold,
        "include_source_failure": scenario.include_source_failure,
        "total": 0,
        "failed": 0,
        "success": 0,
        "unevaluated": 0,
        "source_failed": 0,
        "threshold_failed": 0,
        "source_failed_meeting_threshold": 0,
        "hidden_failed": 0,
        "opened_never_moved": 0,
        "categories": Counter(),
        "vehicles": Counter(),
        "control": defaultdict(lambda: {"total": 0, "healthy": 0}),
        "false_primary": {
            "GECICI_TEKNIK": {"events": 0, "vehicles": set(), "wasted_missions": 0},
            "REGULASYON": {"events": 0, "vehicles": set(), "wasted_missions": 0},
        },
        "verdicts": Counter(),
        "subregions": defaultdict(lambda: {"failed": 0, "false_alarm": 0}),
        "hourly": Counter(),
        # Regülasyon Matrisi: kategori (TEKNIK/REGULASYON/.../SINYALSIZ) x verdict.
        "regulation_matrix": defaultdict(Counter),
        # TEKNİK ARIZA KIRILIMI: (kaynak, etiket) -> sürüş/verdict/boşa görev.
        # Etiket DB'den (kural kitabı açıklaması) akar; core 58 kodu HARDCODE ETMEZ.
        "technical_detail": defaultdict(
            lambda: {"rides": 0, "verdicts": Counter(), "wasted_missions": 0}
        ),
    }


def _threshold_matches(scenario: FailureScenario, duration, distance) -> bool:
    return (
        duration is not None
        and distance is not None
        and float(duration) < scenario.duration_threshold
        and float(distance) < scenario.distance_threshold
    )


def _update_control(acc: dict, assessment) -> None:
    if assessment.verdict is FaultVerdict.DEGERLENDIRILEMEDI:
        return
    groups = []
    if assessment.report_evidence in {
        ClassificationSource.TEXT_MESSAGE,
        ClassificationSource.TEXT_COMMENT,
    }:
        groups.append("ariza_metinli")
    groups.append("herhangi_bildirimli" if assessment.fault_reported else "bildirimsiz")
    for group in groups:
        acc["control"][group]["total"] += 1
        if assessment.healthy_proof:
            acc["control"][group]["healthy"] += 1


def _update_false_fault(acc: dict, assessment, vehicle_id: int) -> None:
    acc["verdicts"][assessment.verdict.value] += 1
    if assessment.verdict is not FaultVerdict.SAHTE_ALARM_SUPHESI:
        return
    key = (
        "REGULASYON"
        if assessment.hypothesis is FalseFaultHypothesis.REGULASYON_SUPHESI
        else "GECICI_TEKNIK"
    )
    target = acc["false_primary"][key]
    target["events"] += 1
    target["vehicles"].add(vehicle_id)
    target["wasted_missions"] += assessment.wasted_missions


def _update_regulation_matrix(acc: dict, category: str, verdict: FaultVerdict) -> None:
    """Regülasyon Matrisi: her başarısız sürüşü (kategori, verdict) hücresine ekler.

    SINYALSIZ dahil TÜM kategoriler yazılır (uydurma yok, şeffaf raporlama) —
    araç durum-sinyali sayesinde bu kütlenin küçülmesi beklenir, gizlenmez.
    """
    acc["regulation_matrix"][category][verdict.value] += 1


# Sınıflandırma kaynağının rapordaki insan-okur adı. Hangi kanıt TEKNIK kategoriyi
# doğurdu: araç durum defteri mi, sürüş telemetrisi mi, serbest metin mi?
_TECHNICAL_SOURCE_LABELS = {
    ClassificationSource.REASON_CODE.value: "Durum defteri",
    ClassificationSource.FIELD_SIGNAL.value: "Telemetri",
    ClassificationSource.TEXT_MESSAGE.value: "Metin (mesaj)",
    ClassificationSource.TEXT_COMMENT.value: "Metin (yorum)",
}


def _update_technical_detail(acc: dict, classification, assessment, signal_desc) -> None:
    """TEKNİK ARIZA KIRILIMI: teknik başarısızlığı KANITINA göre ayrıştırır.

    Durum defterinden gelen sinyallerde etiket, kural kitabındaki açıklamadır
    (`fleet_status_reason.description`) — böylece 58 kodun tamamı rapora açılır ve
    core hiçbir kod adını bilmek zorunda kalmaz. Diğer kaynaklarda etiket kaba
    `FailureReason` enum'udur (metinden bundan fazlası çıkarılamaz).
    """
    source = classification.source.value
    if source == ClassificationSource.REASON_CODE.value and signal_desc:
        label = str(signal_desc)
    elif classification.reason is not None:
        label = classification.reason.value
    else:
        label = "BELİRTİLMEMİŞ"
    entry = acc["technical_detail"][(_TECHNICAL_SOURCE_LABELS.get(source, source), label)]
    entry["rides"] += 1
    entry["verdicts"][assessment.verdict.value] += 1
    if assessment.verdict is FaultVerdict.SAHTE_ALARM_SUPHESI:
        entry["wasted_missions"] += assessment.wasted_missions


_REGULATION_MATRIX_VERDICT_ORDER = (
    FaultVerdict.GERCEK_ARIZA_SUPHESI.value,
    FaultVerdict.SAHTE_ALARM_SUPHESI.value,
    FaultVerdict.BILDIRIM_YOK.value,
    FaultVerdict.DEGERLENDIRILEMEDI.value,
)


def _finalize_scenario(acc: dict, common: dict, cost_rows: list[dict]) -> dict:
    evaluated = acc["failed"] + acc["success"]
    total_failed = acc["failed"]
    categories = [
        {"category": key, "count": count, "pct": _pct(count, total_failed)}
        for key, count in acc["categories"].items()
        if key != "SINYALSIZ"
    ]
    categories.sort(key=lambda r: r["count"], reverse=True)
    signalless_count = acc["categories"].get("SINYALSIZ", 0)

    matrix_rows = []
    for category, counts in acc["regulation_matrix"].items():
        row_total = sum(counts.values())
        # "Bildirimli" = birinin arıza iddiası olduğu verdict'ler. NEDEN DAĞILIMI bunu
        # kullanır: sinyalsiz kütlesinin çoğunda ortada bir iddia YOKTUR, o yüzden tek
        # bir "sinyalsiz %" yanıltıcıdır. Yüzdeyi core üretir, CLI yalnız biçimlendirir.
        reported = sum(
            counts.get(v, 0)
            for v in (
                FaultVerdict.GERCEK_ARIZA_SUPHESI.value,
                FaultVerdict.SAHTE_ALARM_SUPHESI.value,
            )
        )
        matrix_rows.append(
            {
                "category": category,
                "counts": {v: counts.get(v, 0) for v in _REGULATION_MATRIX_VERDICT_ORDER},
                "total": row_total,
                "reported": reported,
                "reported_pct": _pct(reported, row_total),
                "reported_share_of_failed_pct": _pct(reported, total_failed),
                "no_report": counts.get(FaultVerdict.BILDIRIM_YOK.value, 0),
                "unevaluated": counts.get(FaultVerdict.DEGERLENDIRILEMEDI.value, 0),
            }
        )
    matrix_rows.sort(key=lambda r: r["total"], reverse=True)

    technical_rows = []
    for (source, label), values in acc["technical_detail"].items():
        counts = values["verdicts"]
        technical_rows.append(
            {
                "source": source,
                "label": label,
                "rides": values["rides"],
                "real_fault": counts.get(FaultVerdict.GERCEK_ARIZA_SUPHESI.value, 0),
                "false_alarm": counts.get(FaultVerdict.SAHTE_ALARM_SUPHESI.value, 0),
                "no_report": counts.get(FaultVerdict.BILDIRIM_YOK.value, 0),
                "unevaluated": counts.get(FaultVerdict.DEGERLENDIRILEMEDI.value, 0),
                "wasted_missions": values["wasted_missions"],
            }
        )
    technical_rows.sort(key=lambda r: r["rides"], reverse=True)

    group_order = ("ariza_metinli", "herhangi_bildirimli", "bildirimsiz")
    groups = []
    for group in group_order:
        values = acc["control"][group]
        groups.append(
            {
                "group": group,
                "total": values["total"],
                "healthy": values["healthy"],
                "healthy_rate_pct": _pct(values["healthy"], values["total"]),
            }
        )

    unit_costs = [
        float(r.get("labor_cost") or 0) + float(r.get("fuel_cost") or 0)
        for r in cost_rows
    ]
    avg_cost = sum(unit_costs) / len(unit_costs) if unit_costs else None
    currency = cost_rows[0].get("currency") if cost_rows else None
    primary = []
    for key, label in (("GECICI_TEKNIK", "Geçici Teknik"), ("REGULASYON", "Regülasyon")):
        values = acc["false_primary"][key]
        missions = values["wasted_missions"]
        primary.append(
            {
                "key": key,
                "label": label,
                "events": values["events"],
                "vehicles": len(values["vehicles"]),
                "wasted_missions": missions,
                "cost": round(avg_cost * missions, 2) if avg_cost is not None else None,
                "currency": currency,
            }
        )

    verdict_labels = {
        FaultVerdict.GERCEK_ARIZA_SUPHESI.value: "Gerçek arıza şüphesi",
        FaultVerdict.BILDIRIM_YOK.value: "Bildirim olmayan",
        FaultVerdict.DEGERLENDIRILEMEDI.value: "Değerlendirilemeyen",
    }
    details = [
        {"key": key, "label": label, "events": acc["verdicts"].get(key, 0)}
        for key, label in verdict_labels.items()
    ]

    vehicles = [
        {"vehicle_id": key[0], "external_code": key[1], "failures": count}
        for key, count in acc["vehicles"].most_common()
    ]

    sub_regions = []
    for key, totals in common["subregions"].items():
        if totals["total"] < MIN_SUBREGION_RIDES:
            continue
        scenario_values = acc["subregions"][key]
        sub_regions.append(
            {
                "city": key[0],
                "sub_region_code": key[1],
                "sub_region_name": key[2],
                "total_rides": totals["total"],
                "failed": scenario_values["failed"],
                "failure_rate_pct": _pct(scenario_values["failed"], totals["total"]),
                "false_alarm_per_1000": round(
                    1000.0 * scenario_values["false_alarm"] / totals["total"], 2
                ),
            }
        )
    sub_regions.sort(key=lambda r: r["failed"], reverse=True)

    hourly = []
    for key, total in sorted(common["hourly"].items()):
        failed = acc["hourly"].get(key, 0)
        hourly.append(
            {
                "city": key[0], "hour": key[1], "total": total,
                "failed": failed, "failure_rate_pct": _pct(failed, total),
            }
        )

    return {
        "key": acc["key"],
        "label": acc["label"],
        "duration_threshold": acc["duration_threshold"],
        "distance_threshold": acc["distance_threshold"],
        "include_source_failure": acc["include_source_failure"],
        "overview": {
            "total": acc["total"],
            "evaluated": evaluated,
            "failed": acc["failed"],
            "success": acc["success"],
            "unevaluated": acc["unevaluated"],
            "failure_rate_pct": _pct(acc["failed"], evaluated),
        },
        "criteria": {
            "source_failed": acc["source_failed"],
            "threshold_failed": acc["threshold_failed"],
            "source_failed_meeting_threshold": acc["source_failed_meeting_threshold"],
            "hidden_failed": acc["hidden_failed"],
            "combined_failed": acc["failed"],
            "opened_never_moved": acc["opened_never_moved"],
        },
        "cause": {
            "total_failed": total_failed,
            "categories": categories,
            "signalless": {
                "count": signalless_count,
                "pct": _pct(signalless_count, total_failed),
            },
        },
        "control": {"groups": groups},
        "false_fault": {"primary": primary, "details": details},
        "regulation_matrix": {
            "verdict_order": list(_REGULATION_MATRIX_VERDICT_ORDER),
            "rows": matrix_rows,
        },
        # Satır toplamı DAİMA categories["TEKNIK"]'e eşittir (test bunu korur).
        "technical_detail": {
            "total": acc["categories"].get("TEKNIK", 0),
            "rows": technical_rows,
        },
        "vehicle": {"min_failures": MIN_VEHICLE_FAILURES, "vehicles": vehicles},
        "subregion": {"min_rides": MIN_SUBREGION_RIDES, "sub_regions": sub_regions},
        "hourly": {"buckets": hourly},
    }


def _comparison(a: dict, b: dict, transitions: Counter) -> dict:
    ao, bo = a["overview"], b["overview"]
    pp = round(bo["failure_rate_pct"] - ao["failure_rate_pct"], 1)
    delta = bo["failed"] - ao["failed"]
    return {
        "from_key": a["key"],
        "from_label": a["label"],
        "to_key": b["key"],
        "to_label": b["label"],
        "both_failed": transitions["both_failed"],
        "failed_to_success": transitions["failed_to_success"],
        "success_to_failed": transitions["success_to_failed"],
        "both_success": transitions["both_success"],
        "failed_to_unevaluated": transitions["failed_to_unevaluated"],
        "success_to_unevaluated": transitions["success_to_unevaluated"],
        "unevaluated_to_failed": transitions["unevaluated_to_failed"],
        "unevaluated_to_success": transitions["unevaluated_to_success"],
        "both_unevaluated": transitions["both_unevaluated"],
        "unevaluated": sum(
            transitions[key]
            for key in (
                "failed_to_unevaluated", "success_to_unevaluated",
                "unevaluated_to_failed", "unevaluated_to_success", "both_unevaluated",
            )
        ),
        "failed_to_success_pct": _pct(transitions["failed_to_success"], ao["failed"]),
        "success_to_failed_pct": _pct(transitions["success_to_failed"], ao["success"]),
        "failed_count_delta": delta,
        "failure_rate_pp_delta": pp,
        "relative_failed_pct": _pct(delta, ao["failed"]) if delta >= 0 else -_pct(-delta, ao["failed"]),
    }


def analyze_scenarios(
    rows: Iterable[Mapping],
    custom: tuple[float, float] | None = None,
    cost_rows: list[dict] | None = None,
    ooc_counts: dict | None = None,
) -> dict:
    scenarios = build_scenarios(custom)
    accs = {s.key: _new_accumulator(s) for s in scenarios}
    common = {
        "total": 0,
        "source_failed": 0,
        "distance_null": 0,
        "subregions": defaultdict(lambda: {"total": 0}),
        "hourly": Counter(),
    }
    pair_keys = [
        (scenarios[i].key, scenarios[j].key)
        for i in range(len(scenarios))
        for j in range(i + 1, len(scenarios))
    ]
    transition_counts = {key: Counter() for key in pair_keys}

    for row in rows:
        common["total"] += 1
        source_failed = _outcome_value(row.get("outcome")) == RideOutcome.BASARISIZ_HARD.value
        if source_failed:
            common["source_failed"] += 1
        distance = row.get("distance_m")
        duration = row.get("duration_sec")
        if distance is None:
            common["distance_null"] += 1

        sub_key = None
        if row.get("sub_region_code") is not None:
            sub_key = (row.get("city"), row.get("sub_region_code"), row.get("sub_region_name"))
            common["subregions"][sub_key]["total"] += 1
        hour_key = (row.get("city"), int(row.get("local_hour") or 0))
        common["hourly"][hour_key] += 1

        statuses = {
            s.key: s.status(row.get("outcome"), duration, distance) for s in scenarios
        }
        for left, right in pair_keys:
            a_status, b_status = statuses[left], statuses[right]
            counter = transition_counts[(left, right)]
            if a_status is ScenarioStatus.UNEVALUATED and b_status is ScenarioStatus.UNEVALUATED:
                counter["both_unevaluated"] += 1
            elif a_status is ScenarioStatus.UNEVALUATED:
                counter[
                    "unevaluated_to_failed"
                    if b_status is ScenarioStatus.FAILED
                    else "unevaluated_to_success"
                ] += 1
            elif b_status is ScenarioStatus.UNEVALUATED:
                counter[
                    "failed_to_unevaluated"
                    if a_status is ScenarioStatus.FAILED
                    else "success_to_unevaluated"
                ] += 1
            elif a_status is ScenarioStatus.FAILED and b_status is ScenarioStatus.FAILED:
                counter["both_failed"] += 1
            elif a_status is ScenarioStatus.FAILED:
                counter["failed_to_success"] += 1
            elif b_status is ScenarioStatus.FAILED:
                counter["success_to_failed"] += 1
            else:
                counter["both_success"] += 1

        base_ride = None
        classification = None
        for scenario in scenarios:
            acc = accs[scenario.key]
            status = statuses[scenario.key]
            acc["total"] += 1
            if source_failed:
                acc["source_failed"] += 1
            matches = _threshold_matches(scenario, duration, distance)
            if matches:
                acc["threshold_failed"] += 1
                if source_failed:
                    acc["source_failed_meeting_threshold"] += 1
                else:
                    acc["hidden_failed"] += 1
            if distance is not None and duration is not None:
                if float(distance) < 1 and float(duration) > scenario.duration_threshold:
                    acc["opened_never_moved"] += 1

            if status is ScenarioStatus.UNEVALUATED:
                acc["unevaluated"] += 1
                continue
            if status is ScenarioStatus.SUCCESS:
                acc["success"] += 1
                continue

            acc["failed"] += 1
            if base_ride is None:
                base_ride = _scenario_ride(row)
                classification = classify_ride(
                    base_ride,
                    row.get("comment_text"),
                    field_category=_enum_or_none(FailureCategory, row.get("field_category")),
                    field_reason=_enum_or_none(FailureReason, row.get("field_reason")),
                )
            category = classification.category.value if classification.category else "SINYALSIZ"
            acc["categories"][category] += 1
            vehicle_key = (int(row.get("vehicle_id") or 0), row.get("external_code"))
            acc["vehicles"][vehicle_key] += 1
            if sub_key is not None:
                acc["subregions"][sub_key]["failed"] += 1
            acc["hourly"][hour_key] += 1

            assessment = assess_ride(
                base_ride,
                _next_ride(row, scenario),
                comment_text=row.get("comment_text"),
                rating=row.get("rating"),
                field_fault=row.get("field_signal_reason_id") is not None,
            )
            _update_control(acc, assessment)
            _update_false_fault(acc, assessment, int(row.get("vehicle_id") or 0))
            _update_regulation_matrix(acc, category, assessment.verdict)
            if category == FailureCategory.TEKNIK.value:
                _update_technical_detail(
                    acc, classification, assessment, row.get("field_signal_desc")
                )
            if sub_key is not None and assessment.verdict is FaultVerdict.SAHTE_ALARM_SUPHESI:
                acc["subregions"][sub_key]["false_alarm"] += 1

    finalized = {
        key: _finalize_scenario(acc, common, cost_rows or []) for key, acc in accs.items()
    }
    comparisons = []
    for left, right in pair_keys:
        comparisons.append(
            _comparison(finalized[left], finalized[right], transition_counts[(left, right)])
        )
    return {
        "distance_source": "mongo_distance_meters",
        "scenarios": finalized,
        "scenario_order": [s.key for s in scenarios],
        "comparisons": comparisons,
        "data_quality": {
            "total": common["total"],
            "source_failed": common["source_failed"],
            "distance_null": common["distance_null"],
            "out_of_content": ooc_counts or {"total": 0, "by_distance": 0, "by_duration": 0},
        },
    }
