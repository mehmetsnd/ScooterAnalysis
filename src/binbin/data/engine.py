"""Veri katmanı bağlantı/plumbing katmanı — Engine + scope + sorgu yürütücü.

Bu modül, hem sorgu (queries.py) hem yazma (classify.py, assess.py) hem de ETL
(ingest.py) tarafının paylaştığı düşük seviyeli altyapıdır: tek havuzlu Engine,
scope→WHERE derleyici ve scope-enjeksiyonlu sorgu yürütücü.

SQL GÜVENLİK SÖZLEŞMESİ (ihlal etme):
  * DEĞERLER daima bind-parametre (`:param`) ile geçer, asla string'e gömülmez.
  * IDENTIFIER'lar (tablo/kolon/alias) yalnız SABİT LİTERAL; dışarıdan gelen string
    interpolate edilmez.
Sözleşme CLI'da da geçerlidir: kapsam adları (--country/--city) ve dosya adları
kullanıcı girdisidir; bind-param disiplini onları da kapsar.
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
    """Süreç başına TEK SQLAlchemy Engine (lru_cache ile tekil).

    Tekil olması CLI için de gerekli: ingest/classify/assess/analyze aynı süreçte
    ardışık çalıştığında her biri yeni engine kurmaz, bağlantı yeniden kullanılır.
    """
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


# Sinyal-join alias'ı — sabit LİTERAL. `analysis_timeline` (queries.py), `classify_all`
# (classify.py) ve `assess_all` (assess.py) hepsi ride'a bu alias'la (`r`) referans verir;
# fonksiyon bu yüzden alias'ı parametrize ETMEZ (istekten gelen string asla akmaz).
def field_signal_join_sql() -> str:
    """`fleet_status_event`'ten sürüşe en yüksek öncelikli arıza-sinyalini bağlayan
    LEFT JOIN LATERAL parçası. `ride r` alias'ının bulunduğu bir FROM'a eklenir.

    Pencere sözleşmesi:
        [ r.start_time , MIN(end_time + FIELD_SIGNAL_WINDOW_POST_MIN dk,
                             aynı aracın SONRAKİ sürüşünün start_time'ı) )

    Bu pencerede `fleet_status_reason.is_fault_signal=true` olan olaylardan `priority`
    en yüksek olanı seçer (aynı öncelikte en erken olay kazanır). Üç tüketici de (canlı
    analiz, kalıcı classify, false-fault assess) AYNI parçayı kullanır — sinyal mantığı
    SQL'de tekrarlanmaz, tek yerde yaşar.

    SONRAKİ SÜRÜŞTE KESME (neden var): araç sürüş bitiminden birkaç dakika sonra tekrar
    kiralanabiliyor. Kırpma olmadan, o yeni sürüş sırasında düşen arıza olayı ÖNCEKİ
    sürüşe de atanıyordu — ölçüldü: 6.423 atamanın 1.292'si (%20,1) böyleydi. Bu, var
    olmayan bir kanıtı kategoriye çevirmek demek (ALTIN KURAL ihlali). Üst sınır artık
    yarı-açık: olay tam olarak sonraki sürüşün başlangıcında düşerse o sürüşe aittir.

    Döndürdüğü sütunlar: field_signal_reason_id, field_category, field_reason,
    field_signal_desc (kural kitabındaki insan-okur açıklama; TEKNİK ARIZA KIRILIMI
    raporu bunu kullanır — core 58 kodu hardcode etmesin diye etiket DB'den akar).
    """
    return f"""
    LEFT JOIN LATERAL (
        SELECT e.status_reason_id AS field_signal_reason_id,
               fsr.category_hint  AS field_category,
               fsr.reason_hint    AS field_reason,
               fsr.description    AS field_signal_desc
        FROM fleet_status_event e
        JOIN fleet_status_reason fsr ON fsr.reason_id = e.status_reason_id
        WHERE e.vehicle_id = r.vehicle_id
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
