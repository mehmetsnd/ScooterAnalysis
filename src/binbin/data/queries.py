"""Okuma tarafı (read-side) analitik sorgular — serbest fonksiyonlar.

Her fonksiyon bir `Engine` + `scope` alır ve `list[dict]`/`dict`/generator döner.
Scope enjeksiyonu `engine._scope_clause` ile üretilen WHERE parçası bind-param'larla
yapılır (ham değer SQL'e gömülmez).

Not: Bu modül `PostgresRideRepository` tarafından delege edilerek çağrılır; ayrıca
doğrudan da kullanılabilir (repo nesnesi olmadan engine ile).
"""

from difflib import get_close_matches
from typing import Iterable, Optional

from sqlalchemy import Engine, text

from binbin.config import FIELD_SIGNAL_WINDOW_POST_MIN, Scope
from binbin.data.engine import _as_dicts, _scope_clause, field_signal_join_sql
from binbin.data.repository import AnalysisScope, UnknownScopeName


def _resolve_scope_names(
    requested: list[str], rows: list
) -> tuple[list[int], list[str], list[str]]:
    """(id'ler, çözülemeyen adlar, analiz-dışı test adları) — SAF, DB'siz.

    Test şehirleri eksiklerden AYRI döner: `Test` bölgesi gerçekten vardır, yalnız
    analiz dışıdır (`city.is_test`). Onu "yazım hatası" diye raporlamak yanıltıcı
    olurdu, bu yüzden çağıran ayrı bir mesaj verebilsin diye ayrıştırılır.
    """
    # Sehir adlari YALNIZ ulke icinde benzersizdir (db/01: uq_city_name
    # UNIQUE(country_id, name)). Iki ulkede ayni adli sehir varsa sozluk sessizce
    # birini secer ve analiz yanlis/yarim veriyle kosardi - tam da bu guard'in
    # onlemek icin var oldugu hata sinifi. Cakismayi tespit edip HATA veriyoruz.
    found: dict = {}
    collisions: list[str] = []
    for r in rows:
        if r["name"] in found and r["name"] in requested:
            collisions.append(r["name"])
        found[r["name"]] = r
    if collisions:
        raise UnknownScopeName(
            "Hata: " + ", ".join(sorted(set(collisions)))
            + " adi birden fazla ulkede var; hangisi oldugu belirsiz. "
            "--country ile birlikte verin."
        )
    ids: list[int] = []
    missing: list[str] = []
    test_only: list[str] = []
    for name in requested:
        row = found.get(name)
        if row is None:
            missing.append(name)
        elif row["is_test"]:
            test_only.append(name)
        else:
            ids.append(int(row["id"]))
    return ids, missing, test_only


def _unknown_scope_message(kind: str, missing: list[str], valid: list[str]) -> str:
    """Çözülemeyen ad için anlaşılır hata metni — SAF, DB'siz.

    En olası hata Türkçe `İ` yerine ASCII `I` yazmaktır (`Istanbul Avrupa`);
    `difflib` yakın adı bulup gösterir, kullanıcı farkı gözle göremese bile.
    """
    lines = []
    for name in missing:
        lines.append(f"Hata: bilinmeyen {kind} adı: {name!r}.")
        close = get_close_matches(name, valid, n=1, cutoff=0.6)
        if close:
            lines.append(f"  Bunu mu demek istediniz: {close[0]!r}?")
    if valid:
        lines.append(f"Geçerli {kind} adları: {', '.join(sorted(valid))}")
    lines.append(f"İpucu: adlar Türkçe karakterleriyle ve TAM eşleşmeli yazılır.")
    return "\n".join(lines)


# Kapsam türü → (tablo, id kolonu, is_test ifadesi). SQL'e giden TEK identifier
# kaynağı; hepsi sabit literal (SQL güvenlik sözleşmesi).
# DİKKAT: `is_test` YALNIZ `city` tablosunda vardır (bkz. db/01_reset_ve_kurulum.sql);
# `country` için sabit `false` seçilir. Aksi hâlde ülke kapsamı UndefinedColumn ile çöker.
_SCOPE_TABLES = {
    "ülke": ("country", "country_id", "false"),
    "şehir": ("city", "city_id", "is_test"),
}


def _lookup_scope_names(conn, kind: str) -> list[dict]:
    """`kind` için tablodaki TÜM (ad, id, is_test) satırları.

    Adla filtrelenmez, hepsi çekilir: hata mesajının "geçerli adlar" listesini ve
    `difflib` yakın-ad önerisini üretmek için tam kümeye ihtiyaç var. Tablolar
    küçüktür (3 ülke, ~17 şehir).

    `is_test` filtresi sorguya GÖMÜLMEZ: filtrelenirse test şehri "bulunamadı"
    görünür ve yazım hatasından ayırt edilemez. Ayrım Python'da yapılır.
    """
    table, id_col, is_test_expr = _SCOPE_TABLES[kind]
    return [
        dict(m)
        for m in conn.execute(
            text(f"SELECT name, {id_col} AS id, {is_test_expr} AS is_test FROM {table}"),
        ).mappings().all()
    ]


def resolve_scope(engine: Engine, scope: Scope) -> AnalysisScope:
    """Ülke/şehir adlarını id listelerine çözer. is_test şehirler daima dışlanır.

    Çözülemeyen ad `UnknownScopeName` yükseltir — eskiden `[]` dönüp `ANY('{}')`'e
    çevriliyor ve tüm pipeline 0 satırla, exit 0 ile "başarıyla" bitiyordu.
    """
    if scope.is_unrestricted:
        return AnalysisScope(None, None)
    country_ids: Optional[list[int]] = None
    city_ids: Optional[list[int]] = None
    with engine.connect() as conn:
        if scope.countries:
            rows = _lookup_scope_names(conn, "ülke")
            country_ids = _assert_all_resolved("ülke", list(scope.countries), rows)
        if scope.cities:
            rows = _lookup_scope_names(conn, "şehir")
            city_ids = _assert_all_resolved("şehir", list(scope.cities), rows)
    return AnalysisScope(country_ids, city_ids)


def _assert_all_resolved(kind: str, requested: list[str], rows: list) -> list[int]:
    """Hepsi çözülmediyse hata. KISMÎ eşleşme de hatadır: iki şehirden biri
    tutmazsa veri sessizce yarıya iner ve rapor bunu hiçbir yerde söylemez."""
    ids, missing, test_only = _resolve_scope_names(requested, rows)
    if test_only:
        raise UnknownScopeName(
            f"Hata: {', '.join(repr(n) for n in test_only)} analiz dışıdır "
            f"(city.is_test = true) — gerçek sürüş verisi değildir."
        )
    if missing:
        raise UnknownScopeName(
            _unknown_scope_message(kind, missing, [r["name"] for r in rows if not r["is_test"]])
        )
    return ids


def analysis_timeline(
    engine: Engine,
    scope: Optional[AnalysisScope],
    candidate_bounds: Optional[tuple[float, float]] = None,
) -> Iterable[dict]:
    """İki senaryolu analiz için araç/zaman sıralı, stream edilen timeline (generator).
    LEAD alanları kapsam içindeki aynı aracın sonraki sürüşünü gösterir.

    `candidate_bounds` = (azami süre sn, azami mesafe m) — verilirse sinyal-join yalnız
    başarısız olabilecek sürüşlerde çalışır (bkz. `engine.field_signal_join_sql`).
    Verilmezse guard uygulanmaz: yavaş ama daima doğru (güvenli varsayılan).
    """
    clause, params = _scope_clause(scope)
    if candidate_bounds is not None:
        params = {
            **params,
            "fsig_max_dur": float(candidate_bounds[0]),
            "fsig_max_dist": float(candidate_bounds[1]),
        }
    sql = text(
        f"""
        WITH scoped AS (
            SELECT
                r.ride_id, r.source_ref, r.user_ref, r.start_time, r.end_time,
                r.duration_sec, r.distance_m, r.outcome::text AS outcome,
                r.vehicle_id, r.city_id, v.external_code,
                ci.name AS city,
                sr.source_sub_region_id AS sub_region_code,
                sr.name AS sub_region_name,
                EXTRACT(HOUR FROM (r.start_time AT TIME ZONE co.timezone))::int AS local_hour,
                r.triggered_regulation_id, r.end_reason_id, r.end_message,
                r.unlock_ack, r.start_battery_pct, r.connection_lost,
                r.motor_error_code, r.bms_error_code, r.user_cancelled,
                r.payment_status::text AS payment_status,
                r.data_quality_flags,
                f.rating, f.comment_text,
                fsig.field_signal_reason_id, fsig.field_category::text AS field_category,
                fsig.field_reason::text AS field_reason, fsig.field_signal_desc
            FROM ride r
            JOIN city ci ON ci.city_id = r.city_id
            JOIN country co ON co.country_id = ci.country_id
            JOIN vehicle v ON v.vehicle_id = r.vehicle_id
            LEFT JOIN sub_region sr ON sr.sub_region_id = r.sub_region_id
            LEFT JOIN feedback f
                   ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
            {field_signal_join_sql(
                candidate_guard="thresholds" if candidate_bounds is not None else None
            )}
            WHERE ci.is_test = false
              AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
              AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags))
              {clause}
        ), timeline AS (
            SELECT
                *,
                LEAD(ride_id) OVER w AS next_ride_id,
                LEAD(start_time) OVER w AS next_start_time,
                LEAD(duration_sec) OVER w AS next_duration_sec,
                LEAD(distance_m) OVER w AS next_distance_m,
                LEAD(outcome) OVER w AS next_outcome
            FROM scoped
            WINDOW w AS (PARTITION BY vehicle_id ORDER BY start_time, ride_id)
        )
        SELECT *
        FROM timeline
        ORDER BY vehicle_id, start_time, ride_id
        """
    )

    def _iter_rows():
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(sql, params)
            for row in result.mappings():
                yield dict(row)

    return _iter_rows()


def out_of_content_counts(engine: Engine, scope: Optional[AnalysisScope]) -> dict:
    """Analiz dışı (out-of-content) sürüş sayıları.

    IoT/telemetri hatası: mesafe>20km VEYA süre>=6sa. Bu sürüşler `analysis_timeline`'da
    dışlanır; burada ayrı kova olarak (toplam + mesafe/süre kırılımı) sayılır.
    """
    clause, params = _scope_clause(scope)
    sql = text(
        f"""
        SELECT
            count(*) FILTER (WHERE r.distance_m > 20000)   AS by_distance,
            count(*) FILTER (WHERE r.duration_sec >= 21600) AS by_duration,
            count(*) FILTER (
                WHERE r.distance_m > 20000 OR r.duration_sec >= 21600
            ) AS total
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        WHERE ci.is_test = false
          AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
          {clause}
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, params).mappings().one()
    return {"total": row["total"], "by_distance": row["by_distance"],
            "by_duration": row["by_duration"]}


def ops_cost_rows(engine: Engine, scope: Optional[AnalysisScope]) -> list[dict]:
    # ops_cost_model bilerek boştur; boşsa [] döner ve analiz TL raporlamaz.
    # (scope parametrik değil — maliyet modeli küçük ve global okunur.)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT mission_type, labor_cost, fuel_cost, currency "
                "FROM ops_cost_model"
            )
        )
        return _as_dicts(result)


def signal_discrimination_rows(
    engine: Engine, scope: Optional[AnalysisScope]
) -> list[dict]:
    """Kural kitabındaki HER kod için: başarısız/başarılı sürüş penceresinde kaç kez düştü.

    Üretimdeki join'in aksine 58 kodun TAMAMINI tarar — aday kodlar ölçülmeden sinyal
    kararı verilemez. Pencere üretimdeki sinyal-join ile AYNI sözleşmeyi kullanır (yoksa
    denetim, denetlediğinden farklı bir şey ölçer); `next_start` LEAD'i out-of-content
    dahil TÜM sürüşlerden alınır, çünkü dışlanan sürüş de aracı meşgul eder.
    Sürüş × kod başına TEK sayım (DISTINCT); oranların paydası sürüş sayısıdır.
    """
    clause, params = _scope_clause(scope)
    sql = text(
        f"""
        WITH win AS (
            SELECT r.ride_id, r.start_time, r.vehicle_id, r.city_id, r.outcome,
                   r.data_quality_flags,
                   COALESCE(r.end_time, r.start_time) AS end_time,
                   LEAD(r.start_time) OVER (
                       PARTITION BY r.vehicle_id ORDER BY r.start_time) AS next_start
            FROM ride r
        ),
        scoped AS (
            SELECT w.* FROM win w
            JOIN city ci ON ci.city_id = w.city_id
            WHERE ci.is_test = false
              AND w.outcome IN ('BASARILI', 'BASARISIZ_HARD')
              AND NOT ('OUT_OF_CONTENT' = ANY(w.data_quality_flags))
              {clause}
        ),
        base AS (
            SELECT count(*) FILTER (WHERE outcome = 'BASARISIZ_HARD') AS n_fail,
                   count(*) FILTER (WHERE outcome = 'BASARILI')       AS n_ok
            FROM scoped
        ),
        hits AS (
            SELECT DISTINCT s.ride_id, s.outcome, e.status_reason_id AS reason_id
            FROM scoped s
            JOIN fleet_status_event e
              ON e.vehicle_id = s.vehicle_id
             AND e.created_on >= s.start_time
             AND e.created_on < LEAST(
                     s.end_time + make_interval(mins => :win_min),
                     COALESCE(s.next_start, 'infinity'::timestamptz))
            WHERE e.status_reason_id IS NOT NULL
        )
        SELECT fsr.reason_id, fsr.description, fsr.is_fault_signal, fsr.verified,
               count(h.ride_id) FILTER (WHERE h.outcome = 'BASARISIZ_HARD') AS fail_rides,
               count(h.ride_id) FILTER (WHERE h.outcome = 'BASARILI')       AS ok_rides,
               b.n_fail, b.n_ok
        FROM fleet_status_reason fsr
        LEFT JOIN hits h ON h.reason_id = fsr.reason_id
        CROSS JOIN base b
        GROUP BY fsr.reason_id, fsr.description, fsr.is_fault_signal, fsr.verified,
                 b.n_fail, b.n_ok
        ORDER BY fail_rides DESC
        """
    )
    with engine.connect() as conn:
        result = conn.execute(
            sql, {**params, "win_min": FIELD_SIGNAL_WINDOW_POST_MIN}
        )
        return _as_dicts(result)


def comment_corpus_rows(
    engine: Engine, scope: Optional[AnalysisScope]
) -> Iterable[dict]:
    """Kelime denetiminin korpusu: METNİ OLAN sürüşler (stream generator).

    PAYDA BİLİNÇLİ OLARAK DARDIR — yalnız yorum veya sürüş mesajı taşıyan sürüşler.
    Sessiz sürüşleri paydaya katmak her kelimeye sahte bir lift kazandırırdı, çünkü
    "yorum bırakmış olmak" başlı başına başarısızlıkla korelasyonludur.

    `signal_discrimination_rows`'un aksine pencere join'i YOKTUR: `feedback` sürüşle
    1:1'dir, olay penceresi kurmaya gerek kalmaz.
    """
    clause, params = _scope_clause(scope)
    sql = text(
        f"""
        SELECT r.outcome::text AS outcome,
               f.comment_text,
               r.end_message
        FROM ride r
        JOIN city ci ON ci.city_id = r.city_id
        LEFT JOIN feedback f
               ON f.ride_id = r.ride_id AND f.ride_start_time = r.start_time
        WHERE ci.is_test = false
          AND r.outcome IN ('BASARILI', 'BASARISIZ_HARD')
          AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags))
          AND (coalesce(f.comment_text, '') <> '' OR coalesce(r.end_message, '') <> '')
          {clause}
        """
    )

    def _iter_rows():
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(sql, params)
            for row in result.mappings():
                yield dict(row)

    return _iter_rows()


def list_data_loads(engine: Engine) -> list[dict]:
    """Yüklenen CSV'lerin denetim kaydı (en yeniden eskiye)."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT data_load_id, file_name, period_start, period_end,
                       rows_read, rows_inserted, rows_skipped, rows_flagged,
                       status, started_at, finished_at
                FROM data_load
                ORDER BY data_load_id DESC
                """
            )
        )
        return _as_dicts(result)
