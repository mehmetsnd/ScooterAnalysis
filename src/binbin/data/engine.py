"""Veri katmanı plumbing'i: tek havuzlu Engine, scope→WHERE derleyici, sorgu yürütücü.
queries/classify/assess/ingest hepsi bunu paylaşır.

SQL GÜVENLİK SÖZLEŞMESİ (ihlal etme): değerler daima bind-param (`:param`);
identifier'lar (tablo/kolon/alias) yalnız sabit literal. CLI'dan gelen kapsam ve
dosya adları da bu disipline tabidir.
"""

import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

from binbin.config import (
    DB_POOL_PRE_PING,
    DB_POOL_RECYCLE_SEC,
    FIELD_SIGNAL_WINDOW_POST_MIN,
)
from binbin.core.scenario_analysis import CURRENT_DISTANCE_M, CURRENT_DURATION_SEC
from binbin.data.repository import AnalysisScope


def _database_url() -> str:
    """`.env`/ortamdan DATABASE_URL okur; yoksa ham KeyError yerine anlaşılır hata."""
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL tanımlı değil. `.env.example`'ı `.env` olarak kopyalayıp "
            "DATABASE_URL değerini doldurun (örn. postgresql+psycopg://user:pass@host/db)."
        )
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Süreç başına TEK Engine — aynı süreçte ardışık çalışan komutlar havuzu paylaşır."""
    return create_engine(
        _database_url(),
        pool_pre_ping=DB_POOL_PRE_PING,
        pool_recycle=DB_POOL_RECYCLE_SEC,
    )


def _as_dicts(result) -> list[dict]:
    """SQLAlchemy sonucunu list[dict]'e çevirir."""
    return [dict(m) for m in result.mappings().all()]


# city alias'ı — sabit LİTERAL, asla dışarıdan gelmez (SQL güvenlik sözleşmesi).
_CITY_ALIAS = "ci"


# `r` alias'ı sabit LİTERAL — üç tüketici de ride'a bu adla referans verir, parametrize edilmez.
def field_signal_join_sql(candidate_guard: Optional[str] = None) -> str:
    """`fleet_status_event`'ten en yüksek `priority`'li arıza-sinyalini bağlayan LEFT JOIN
    LATERAL parçası; `ride r` alias'ının bulunduğu bir FROM'a eklenir. analysis_timeline,
    classify_all ve assess_all AYNI parçayı kullanır (sinyal mantığı tek yerde).

    Pencere (yarı açık): [r.start_time, MIN(end_time + FIELD_SIGNAL_WINDOW_POST_MIN dk,
    aynı aracın SONRAKİ sürüşünün start_time'ı)). Sonraki sürüşte kesme ŞART: kırpma
    olmadan 6.423 atamanın 1.292'si (%20,1) yanlış sürüşe gidiyordu.

    ADAY GUARD'I — sinyal yalnız başarısız sürüşlerde okunur (1,03M sürüşün ~%6'sı) ama
    LATERAL her satırda çalışır, yani %94 boşa iş (ölçüldü: join'siz 0,4 sn, join'li 37 sn).
    Guard yalnız `r` kolonlarına baktığı için satır başına sabit maliyettir:
      None         : guard yok, tüm satırlar (yavaş, daima doğru).
      "outcome"    : `r.outcome='BASARISIZ_HARD'`. assess.py field_fault'u yalnız bu
                     outcome'da okuduğu için TAM EŞLEŞME (üstküme değil). Ölçüldü:
                     LATERAL 1.028.402 → ~65.964 çağrı, assess_all 51,9 sn.
      "thresholds" : outcome VEYA (duration<:fsig_max_dur AND distance<:fsig_max_dist);
                     eşikler `scenario_analysis.candidate_bounds()`'tan bind-param olarak
                     gelir (SQL güvenlik sözleşmesi). Çok senaryolu `analyze` için
                     ÜSTKÜMEdir; DB'de doğrulandı: hiçbir aday sinyalini kaybetmiyor.

    Döndürdüğü sütunlar: field_signal_reason_id, field_category, field_reason,
    field_signal_desc (rapor etiketi DB'den akar; core kod adı bilmez).
    """
    if candidate_guard == "outcome":
        guard = "r.outcome = 'BASARISIZ_HARD' AND "
    elif candidate_guard == "thresholds":
        guard = (
            """(r.outcome = 'BASARISIZ_HARD'
               OR (r.duration_sec < :fsig_max_dur AND r.distance_m < :fsig_max_dist))
          AND """
        )
    elif candidate_guard is None:
        guard = ""
    else:
        raise ValueError(f"Bilinmeyen candidate_guard: {candidate_guard!r}")
    return f"""
    LEFT JOIN LATERAL (
        SELECT e.status_reason_id AS field_signal_reason_id,
               fsr.category_hint  AS field_category,
               fsr.reason_hint    AS field_reason,
               fsr.description    AS field_signal_desc
        FROM fleet_status_event e
        JOIN fleet_status_reason fsr ON fsr.reason_id = e.status_reason_id
        WHERE {guard}e.vehicle_id = r.vehicle_id
          AND fsr.is_fault_signal
          AND e.created_on >= r.start_time
          AND e.created_on < LEAST(
              COALESCE(r.end_time, r.start_time)
                  + make_interval(mins => {FIELD_SIGNAL_WINDOW_POST_MIN}),
              COALESCE(
                  (SELECT min(r2.start_time)
                     FROM ride r2
                    WHERE r2.vehicle_id = r.vehicle_id
                      AND r2.start_time > r.start_time),
                  'infinity'::timestamptz))
        ORDER BY fsr.priority DESC, e.created_on ASC
        LIMIT 1
    ) fsig ON true
    """


_CURRENT_RULE_ALIASES = ("r", "ride", "seq")


def current_rule_sql(alias: str = "r") -> str:
    """Mevcut Kural'ın SQL karşılığı; classify_all ve assess_all paylaşır.
    Canlı karşılığı `scenario_analysis.FailureScenario.status()`.

    Eşikler LİTERAL yazılır (bind-param DEĞİL): `idx_ride_unclassified` kısmi
    indeksinin predikatı da literaldir ve planlayıcı bind-param'la implikasyonu
    kanıtlayamayıp indeksi kullanmaz (ölçüldü: 17.653 → 53.273 maliyet, seq scan).
    Değerler kullanıcıdan değil core sabitinden gelir; SQL güvenlik sözleşmesi
    kullanıcı girdisi içindir.
    """
    if alias not in _CURRENT_RULE_ALIASES:
        raise ValueError(
            f"Bilinmeyen alias: {alias!r} (izin verilen: {_CURRENT_RULE_ALIASES})"
        )
    return (
        f"({alias}.outcome = 'BASARISIZ_HARD'"
        f" OR ({alias}.duration_sec IS NOT NULL AND {alias}.distance_m IS NOT NULL"
        f" AND {alias}.duration_sec < {float(CURRENT_DURATION_SEC)}"
        f" AND {alias}.distance_m < {float(CURRENT_DISTANCE_M)}))"
    )


def current_rule_params() -> dict:
    """`field_signal_join_sql(candidate_guard="thresholds")` guard'ının eşikleri.
    `current_rule_sql` ile aynı sabitten okunur; ayrışırlarsa guard tam eşleşme
    olmaktan çıkar."""
    return {
        "fsig_max_dur": float(CURRENT_DURATION_SEC),
        "fsig_max_dist": float(CURRENT_DISTANCE_M),
    }


def _scope_clause(scope: Optional[AnalysisScope]) -> tuple[str, dict]:
    """AnalysisScope'tan WHERE parçası + parametreler üretir (is_test filtresi ayrıdır).

    None alanlar filtrelenmez. Şehir/ülke id'leri DAİMA bind-param `ANY(:param)` ile
    bağlanır; alias sabit literaldir → interpolasyona kullanıcı girdisi akmaz.
    """
    if scope is None:
        return "", {}
    clause = ""
    params: dict = {}
    if scope.country_ids is not None:
        clause += f" AND {_CITY_ALIAS}.country_id = ANY(:sc_country_ids)"
        params["sc_country_ids"] = list(scope.country_ids)
    if scope.city_ids is not None:
        clause += f" AND {_CITY_ALIAS}.city_id = ANY(:sc_city_ids)"
        params["sc_city_ids"] = list(scope.city_ids)
    return clause, params
