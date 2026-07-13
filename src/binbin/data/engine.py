"""Veri katmanı bağlantı/plumbing katmanı — Engine + scope + sorgu yürütücü.

Bu modül, hem sorgu (queries.py) hem yazma (classify.py, assess.py) hem de ETL
(ingest.py) tarafının paylaştığı düşük seviyeli altyapıdır: tek havuzlu Engine,
scope→WHERE derleyici ve scope-enjeksiyonlu sorgu yürütücü.

SQL GÜVENLİK SÖZLEŞMESİ (web'e taşırken kritik — ihlal etme):
  * DEĞERLER daima bind-parametre (`:param`) ile geçer, asla string'e gömülmez.
  * IDENTIFIER'lar (tablo/kolon/alias) yalnız SABİT LİTERAL; istekten gelen string
    interpolate edilmez.
"""

import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

from binbin.config import (
    DB_MAX_OVERFLOW,
    DB_POOL_PRE_PING,
    DB_POOL_RECYCLE_SEC,
    DB_POOL_SIZE,
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
    """Süreç başına TEK, havuzlu SQLAlchemy Engine (paylaşımlı, thread-safe).

    Web'de her istek yeni engine kurarsa bağlantı tükenir; burada tek engine'in
    connection pool'u yeniden kullanılır. CLI'da da zararsız (tek bağlantı yeter).
    """
    return create_engine(
        _database_url(),
        pool_pre_ping=DB_POOL_PRE_PING,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_recycle=DB_POOL_RECYCLE_SEC,
    )


def _as_dicts(result) -> list[dict]:
    """SQLAlchemy sonucunu list[dict]'e çevirir."""
    return [dict(m) for m in result.mappings().all()]


# city alias'ı — sabit LİTERAL, asla dışarıdan gelmez (SQL güvenlik sözleşmesi).
_CITY_ALIAS = "ci"


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
