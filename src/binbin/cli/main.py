"""Projenin CLI (Command Line Interface) giriş noktası — Imperative Shell.

Burada iş mantığı (core logic) bulunmaz; sadece terminalden gelen komutları 
argparse ile yakalar, ilgili argümanları parse eder ve alttaki modüllere paslarız.

Kullanım örnekleri (PYTHONPATH=src klasöründe):
    python -m binbin.cli ingest   [--data-dir data_raw] [--country ...] [--city ...] [--all]
    python -m binbin.cli classify [--batch-size 10000]  [--country ...] [--city ...] [--all]
    python -m binbin.cli assess   [--country ...] [--city ...] [--all]
    python -m binbin.cli classify [--refresh] ...
    python -m binbin.cli analyze  [--detay] [--derin] [--false-fault] [--sinyal-denetimi]
                                  [--charts DIR] [--wi-duration SN --wi-distance M]

`ingest`, data_dir'deki CSV'lerin türünü (sürüş / araç durum-değişim defteri)
başlık satırından otomatik ayırır ve her türü kendi transformer'ına yönlendirir
(`--file` verilirse yalnız o dosya). Bkz. `binbin.data.ingest.detect_csv_kind`.

`analyze` iki senaryolu çalışır: Mevcut Kural her zaman; --wi-duration/--wi-distance
BİRLİKTE verilirse ek olarak Özel Kural senaryosu ve aralarındaki geçiş karşılaştırması
hesaplanır. Motor `core/scenario_analysis` (tek geçiş, streaming timeline).

Scope (Kapsam) Mantığı:
    Hiçbir bayrak verilmezse   -> config.DEFAULT_SCOPE (Türkiye + İstanbul Avrupa/Anadolu)
    --country/--city verilirse -> Girilen lokasyonlar çekilir (config'i ezer)
    --all flag'i verilirse     -> Filtre kalkar, DB'deki tüm data işlenir
    Not: --all ile lokasyon bayrakları aynı anda kullanılamaz, hata patlatır.
"""

import argparse
import math
import sys
from pathlib import Path

from binbin.config import DEFAULT_SCOPE, UNRESTRICTED_SCOPE, Scope
from binbin.reporting.format import (
    fmt_threshold as _fmt_thr,
    signed_int as _signed_int,
    tr_dec as _tr_dec,
    tr_int as _tr_int,
    tr_pct as _tr_pct,
)


def _force_utf8_stdout() -> None:
    """Windows konsolu (cp1254) Türkçe/işaret karakterlerinde patlamasın diye UTF-8'e geçer."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _add_scope_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--country", action="append", metavar="AD", help="Kapsam ülke adı (tekrarlanabilir)")
    p.add_argument("--city", action="append", metavar="AD", help="Kapsam şehir adı (tekrarlanabilir)")
    p.add_argument("--all", action="store_true", help="Filtre yok (tüm veri)")


def _scope_from_args(args: argparse.Namespace) -> Scope:
    """Kapsam bayraklarından Scope üretir; çelişkili kombinasyonlarda hata verir."""
    if args.all and (args.country or args.city):
        raise SystemExit("Hata: --all ile --country/--city birlikte verilemez.")
    if args.all:
        return UNRESTRICTED_SCOPE
    if args.country or args.city:
        return Scope(
            countries=tuple(args.country or ()),
            cities=tuple(args.city or ()),
        )
    return DEFAULT_SCOPE


def build_parser() -> argparse.ArgumentParser:
    """CLI argüman ayrıştırıcısını kurar (subcommand'lar + kapsam bayrakları)."""
    parser = argparse.ArgumentParser(prog="binbin", description="Binbin başarısız sürüş analizi")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="CSV → Postgres")
    p_ing.add_argument("--data-dir", type=Path, default=Path("data_raw"), help="Ham CSV klasörü")
    p_ing.add_argument("--file", type=Path, metavar="PATH", help="İşlenecek CSV (çok dosya varsa seç)")
    p_ing.add_argument("--force", action="store_true", help="Zaten yüklenmiş dosyayı yeniden yükle")
    _add_scope_args(p_ing)

    p_cls = sub.add_parser("classify", help="Başarısız sürüşleri sınıflandır")
    p_cls.add_argument("--batch-size", type=int, default=10000)
    p_cls.add_argument(
        "--refresh", action="store_true",
        help="Tüm başarısız sürüşleri yeniden sınıflandır (varsayılan: yalnız damgalanmamışlar)",
    )
    _add_scope_args(p_cls)

    p_asy = sub.add_parser("assess", help="Sahte arıza değerlendirmesi")
    p_asy.add_argument(
        "--refresh", action="store_true",
        help="Tüm başarısız sürüşleri yeniden hesapla (varsayılan: yalnız yeni/DEGERLENDIRILEMEDI)",
    )
    _add_scope_args(p_asy)

    sub.add_parser("loads", help="Yüklenen CSV'lerin denetim kaydını listele")

    p_an = sub.add_parser("analyze", help="Analiz özeti (+ opsiyonel PNG)")
    p_an.add_argument("--detay", action="store_true", help="Araç + alt bölge kırılımları")
    p_an.add_argument("--derin", action="store_true", help="Saatlik yerel kırılım")
    p_an.add_argument(
        "--false-fault", action="store_true",
        help="Sahte arıza özeti + regülasyon matrisi + teknik arıza kırılımı",
    )
    p_an.add_argument(
        "--sinyal-denetimi", action="store_true", dest="sinyal_denetimi",
        help="Kural kitabındaki her kodun ayırt ediciliğini (lift) ölçüp raporla",
    )
    p_an.add_argument("--charts", type=Path, metavar="DIR", help="PNG'leri bu klasöre üret")
    p_an.add_argument(
        "--wi-duration", type=float, metavar="SN",
        help="Özel senaryo süre eşiği (sn). --wi-distance ile BİRLİKTE verilir.",
    )
    p_an.add_argument(
        "--wi-distance", type=float, metavar="M",
        help="Özel senaryo mesafe eşiği (m). --wi-duration ile BİRLİKTE verilir.",
    )
    _add_scope_args(p_an)
    return parser


def select_csv(files: list[Path], explicit: Path | None, prompt_fn=None) -> Path:
    """İşlenecek CSV'yi seçer (saf ve test edilebilir: I/O yalnız prompt_fn ile).

    - explicit verilmişse doğrula ve döndür.
    - tek dosya → otomatik.
    - çok dosya + prompt_fn → numaralı menü.
    - çok dosya + prompt_fn yok (pipe/CI) → dosyaları listeleyip hata ver.
    - hiç dosya yok → hata.
    """
    if explicit is not None:
        if not explicit.is_file():
            raise SystemExit(f"Hata: dosya yok: {explicit}")
        return explicit
    if not files:
        raise SystemExit("Hata: data_raw/ içinde .csv yok.")
    if len(files) == 1:
        return files[0]
    listing = "\n".join(f"  - {p.name}" for p in files)
    if prompt_fn is None:
        raise SystemExit(f"Birden fazla CSV var; --file ile birini seç:\n{listing}")
    menu = "\n".join(f"  [{i}] {p.name}" for i, p in enumerate(files, 1))
    while True:
        try:
            choice = prompt_fn(f"İşlenecek CSV:\n{menu}\nNumara: ").strip()
        except (EOFError, KeyboardInterrupt):
            # İnteraktif giriş yoksa (pipe/CI) temiz hataya düş.
            raise SystemExit(f"\nSeçim yapılmadı; --file ile birini seç:\n{listing}")
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return files[int(choice) - 1]
        print(f"Geçersiz seçim: {choice!r}")


# --------------------------------------------------------------------- komutlar
def _print_rides_ingest_report(report) -> None:
    print(
        f"[{report.status}] data_load={report.data_load_id}\n"
        f"  okunan   : {report.rows_read:,}\n"
        f"  uygun    : {report.rows_eligible:,}\n"
        f"  yazılan  : {report.rows_inserted:,}\n"
        f"  atlanan  : {report.rows_skipped:,}\n"
        f"  flag'li  : {report.rows_flagged:,}\n"
        f"  şehir/altbölge/end_reason: {report.cities}/{report.sub_regions}/{report.end_reasons}"
    )
    for w in report.warnings:
        print(f"  UYARI: {w}")


def _print_status_ingest_report(report) -> None:
    print(
        f"[{report.status}] data_load={report.data_load_id}\n"
        f"  okunan    : {report.rows_read:,}\n"
        f"  yazılan   : {report.rows_inserted:,}\n"
        f"  atlanan   : {report.rows_skipped:,}\n"
        f"  yeni araç : {report.vehicles_created:,}"
    )
    for w in report.warnings:
        print(f"  UYARI: {w}")


def _ingest_one(kind: str, csv_path: Path, scope: Scope, force: bool) -> None:
    """Türüne göre doğru transformer'a yönlendirir (rides → ride, status → fleet_status_event)."""
    from binbin.data.ingest import run_ingest
    from binbin.data.ingest_status import run_status_ingest

    print(f"\nCSV [{kind}]: {csv_path} ({csv_path.stat().st_size / 1_048_576:.1f} MB)")
    if kind == "rides":
        _print_rides_ingest_report(run_ingest(csv_path, scope, force=force))
    else:
        _print_status_ingest_report(run_status_ingest(csv_path, scope, force=force))


def cmd_ingest(args: argparse.Namespace) -> None:
    """`--file` verilmişse yalnız o dosyayı; verilmemişse data_dir'deki HER TÜRÜ
    (sürüş + araç durum-değişim CSV'si) başlığından otomatik ayırıp sırayla yükler.
    Bir türde birden fazla dosya varsa mevcut `select_csv` seçim/uyarı mantığı kullanılır.
    """
    from binbin.data.ingest import detect_csv_kind, list_source_csvs

    scope = _scope_from_args(args)
    files = list_source_csvs(args.data_dir)
    prompt_fn = input if sys.stdin is not None and sys.stdin.isatty() else None

    if args.file is not None:
        csv_path = select_csv([], explicit=args.file, prompt_fn=None)
        _ingest_one(detect_csv_kind(csv_path), csv_path, scope, args.force)
        return

    if not files:
        raise SystemExit("Hata: data_raw/ içinde .csv yok.")

    # SIRA YÜK TAŞIR: rides ÖNCE gelmeli. Sürüş ingest'i vehicle satırlarını plakayla
    # (external_code) birlikte açar; status ingest'i yalnız hiç sürülmemiş araçları
    # source_ref ile ekler. Ters sırada o araçlar plakasız oluşur ve rides'ın
    # ON CONFLICT DO NOTHING'i onları GÜNCELLEMEZ — plaka kalıcı olarak kaybolur.
    by_kind: dict[str, list[Path]] = {"rides": [], "status": []}
    for f in files:
        by_kind[detect_csv_kind(f)].append(f)

    for kind, kind_files in by_kind.items():
        if not kind_files:
            continue
        csv_path = select_csv(kind_files, explicit=None, prompt_fn=prompt_fn)
        _ingest_one(kind, csv_path, scope, args.force)


def cmd_classify(args: argparse.Namespace) -> None:
    from binbin.data.postgres_repo import PostgresRideRepository

    repo = PostgresRideRepository()
    ascope = repo.resolve_scope(_scope_from_args(args))
    result = repo.classify_all(ascope, batch_size=args.batch_size, refresh=args.refresh)
    mode = "tam yeniden sınıflandırma" if args.refresh else "artımlı"
    print(
        f"Sınıflandırma ({mode}): işlenen={result['processed']:,} "
        f"kategori-atanan={result['classified']:,}"
    )


def cmd_assess(args: argparse.Namespace) -> None:
    from binbin.data.postgres_repo import PostgresRideRepository

    repo = PostgresRideRepository()
    ascope = repo.resolve_scope(_scope_from_args(args))
    result = repo.assess_all(ascope, refresh=args.refresh)
    mode = "tam yeniden hesap" if args.refresh else "artımlı"
    print(
        f"Değerlendirme ({mode}): {result['assessed']:,} sürüş için "
        "false_fault_assessment yazıldı."
    )


def cmd_loads(args: argparse.Namespace) -> None:
    from binbin.data.postgres_repo import PostgresRideRepository

    rows = PostgresRideRepository().list_data_loads()
    if not rows:
        print("Henüz yükleme yok.")
        return
    print(f"{'id':>4}  {'dosya':<45} {'dönem':<23} {'yazılan':>9}  durum")
    for r in rows:
        period = f"{r['period_start']}→{r['period_end']}" if r["period_start"] else "-"
        print(
            f"{r['data_load_id']:>4}  {(r['file_name'] or '')[:45]:<45} "
            f"{period:<23} {(r['rows_inserted'] or 0):>9,}  {r['status']}"
        )


def _custom_rule_from_args(args: argparse.Namespace) -> tuple[float, float] | None:
    """Özel Kural eşiklerini (--wi-duration/--wi-distance) doğrular; ikisi de yoksa None.

    Yarım kural (yalnız biri verilmiş) anlamsızdır → net hata. (Bayrak adları geriye
    dönük uyumluluk için --wi-* kalır; kavram 'Özel Kural senaryosu'dur.)
    """
    dur, dist = args.wi_duration, args.wi_distance
    if (dur is None) != (dist is None):
        raise SystemExit(
            "Hata: --wi-duration ve --wi-distance BİRLİKTE verilmeli "
            "(Özel Kural senaryosu hem süre hem mesafe eşiği ister)."
        )
    if dur is not None and (
        not math.isfinite(dur) or not math.isfinite(dist) or dur <= 0 or dist <= 0
    ):
        raise SystemExit("Hata: özel süre ve mesafe eşikleri sonlu ve sıfırdan büyük olmalı.")
    return (dur, dist) if dur is not None else None


def cmd_analyze(args: argparse.Namespace) -> None:
    from binbin.data.postgres_repo import PostgresRideRepository
    from binbin.core import scenario_analysis

    custom_thr = _custom_rule_from_args(args)
    repo = PostgresRideRepository()
    ascope = repo.resolve_scope(_scope_from_args(args))

    # Bilinçli takas: analyze her çağrıda tüm timeline'ı (BASARILI+BASARISIZ_HARD)
    # STREAM eder ve agregasyonu Python'da yapar. Neden: iki senaryolu yeniden
    # sınıflandırma + classify/assess mantığını SQL'de tekrarlamamak için. Bellek
    # O(satır) değil O(farklı varlık); mevcut aylık ölçekte uygundur. Çok büyük
    # ölçekte (10M+ satır) sık başlıklar için SQL-tarafı agregasyon yeniden değerlendirilmeli.
    report = scenario_analysis.analyze_scenarios(
        repo.analysis_timeline(ascope),
        custom=custom_thr,
        cost_rows=repo.ops_cost_rows(ascope),
        ooc_counts=repo.out_of_content_counts(ascope),
    )
    _print_scenario_definitions(report)
    _print_scenario_overview(report)
    _print_scenario_comparisons(report)
    _print_scenario_causes(report)
    _print_scenario_criteria(report)
    _print_scenario_control(report)
    if args.false_fault:
        _print_scenario_false_fault(report)
        _print_regulation_matrix(report)
        _print_technical_detail(report)
    if args.sinyal_denetimi:
        _print_signal_audit(repo.signal_discrimination_rows(ascope))
    if args.detay:
        _print_scenario_vehicles(report)
        _print_scenario_subregions(report)
    if args.derin:
        _print_scenario_hourly(report)

    if args.charts:
        from binbin.reporting import charts

        paths = [
            charts.chart_scenario_overview(report, args.charts),
            charts.chart_scenario_causes(report, args.charts),
            charts.chart_scenario_control(report, args.charts),
        ]
        if report["comparisons"]:
            paths.append(charts.chart_scenario_transitions(report, args.charts))
        if args.false_fault:
            paths.append(charts.chart_scenario_false_fault(report, args.charts))
        if args.detay:
            paths.append(charts.chart_scenario_vehicles(report, args.charts))
            paths.append(charts.chart_scenario_subregions(report, args.charts))
        if args.derin:
            paths.append(charts.chart_scenario_hourly(report, args.charts))
        print("\nGrafikler:")
        for p in paths:
            print(f"  {p}")


# ---------------------------------------------------- iki senaryolu okunur çıktı
_CAUSE_LABELS = {
    "TEKNIK": "Teknik",
    "REGULASYON": "Regülasyon",
    "KULLANICI": "Kullanıcı",
    "ODEME": "Ödeme",
    "SISTEM": "Sistem",
    "SINYALSIZ": "Sinyalsiz",
}
_GROUP_LABELS = {
    "ariza_metinli": "Arıza metinli bildirim",
    "herhangi_bildirimli": "Herhangi bildirim",
    "bildirimsiz": "Bildirimsiz (kontrol)",
}


def _scenario_list(report: dict) -> list[dict]:
    return [report["scenarios"][key] for key in report["scenario_order"]]


def _rule_text(scenario: dict) -> str:
    dur = _fmt_thr(scenario["duration_threshold"])
    dist = _fmt_thr(scenario["distance_threshold"])
    threshold = f"süre < {dur} sn ve mesafe < {dist} m"
    if scenario["include_source_failure"]:
        return f"Kaynak başarısız veya {threshold}"
    return f"Kaynak etiketi kullanılmaz; yalnız {dur} sn/{dist} m uygulanır"


def _section(title: str) -> None:
    print(f"\n{title}")
    print("═" * min(100, max(68, len(title))))


def _print_scenario_definitions(report: dict) -> None:
    _section("SENARYOLAR")
    for scenario in _scenario_list(report):
        print(f"{scenario['label']:<24}: {_rule_text(scenario)}")
    print(f"{'Mesafe kaynağı':<24}: {report['distance_source']}")


def _print_scenario_overview(report: dict) -> None:
    _section("GENEL BAŞARISIZLIK ÖZETİ")
    print(f"{'Senaryo':<24}{'Başarısız':>13}{'Başarılı':>13}{'Değerlendirme dışı':>20}{'Oran':>10}")
    print("─" * 80)
    for scenario in _scenario_list(report):
        o = scenario["overview"]
        print(
            f"{scenario['label']:<24}{_tr_int(o['failed']):>13}{_tr_int(o['success']):>13}"
            f"{_tr_int(o['unevaluated']):>20}{_tr_pct(o['failure_rate_pct']):>10}"
        )
    quality = report["data_quality"]
    ooc = quality["out_of_content"]
    print(
        f"\nToplam analiz edilen: {_tr_int(quality['total'])} · "
        f"Kaynak başarısız: {_tr_int(quality['source_failed'])} · "
        f"Mongo mesafesi eksik: {_tr_int(quality['distance_null'])}"
    )
    print(
        f"Out-of-content (analiz dışı): {_tr_int(ooc['total'])} "
        f"(mesafe>20km {_tr_int(ooc['by_distance'])} · süre≥6sa {_tr_int(ooc['by_duration'])})"
    )


def _print_scenario_comparisons(report: dict) -> None:
    if not report["comparisons"]:
        return
    _section("SENARYOLAR ARASI GEÇİŞLER")
    for c in report["comparisons"]:
        print(f"\n{c['from_label'].upper()} → {c['to_label'].upper()}")
        print("─" * 68)
        print(
            f"Başarısızdan başarılıya dönen  {_tr_int(c['failed_to_success']):>10}  "
            f"başlangıç başarısızlarının {_tr_pct(c['failed_to_success_pct'])}’i"
        )
        print(
            f"Başarıdan başarısıza dönen      {_tr_int(c['success_to_failed']):>10}  "
            f"başlangıç başarılılarının {_tr_pct(c['success_to_failed_pct'])}’i"
        )
        print(
            f"Net başarısız değişimi          {_signed_int(c['failed_count_delta']):>10}  "
            f"oran farkı {c['failure_rate_pp_delta']:+.1f} puan · "
            f"göreli {c['relative_failed_pct']:+.1f}%"
        )
        if c["failed_to_unevaluated"]:
            print(f"Başarısızdan değerlendirme dışına {_tr_int(c['failed_to_unevaluated']):>10}")
        if c["success_to_unevaluated"]:
            print(f"Başarıdan değerlendirme dışına    {_tr_int(c['success_to_unevaluated']):>10}")
        if c["unevaluated_to_failed"]:
            print(f"Değerlendirme dışından başarısıza {_tr_int(c['unevaluated_to_failed']):>10}")
        if c["unevaluated_to_success"]:
            print(f"Değerlendirme dışından başarıya   {_tr_int(c['unevaluated_to_success']):>10}")


def _print_scenario_causes(report: dict) -> None:
    """NEDEN DAĞILIMI — kategori × bildirim durumu.

    Neden bildirimli/bildirimsiz ayrılıyor: "Sinyalsiz" kütlesinin ezici çoğunluğunda
    ortada bir arıza İDDİASI YOKTUR (kimse bildirmemiş, sürüş kısa sürüp bitmiş).
    Olmayan bir bildirimin nedenini açıklayamamak bir eksiklik değildir. Tek bir
    "sinyalsiz %" rakamı bu ikisini birbirine karıştırıp tabloyu olduğundan kötü
    gösteriyordu; asıl takip edilmesi gereken sayı SON SATIRDA basılan
    "bildirim var ama kategori atanamadı" kütlesidir.
    """
    _section("NEDEN DAĞILIMI")
    for scenario in _scenario_list(report):
        rows = scenario["regulation_matrix"]["rows"]
        if not rows:
            continue
        print(f"\n{scenario['label']}")
        print(
            f"{'Kategori':<16}{'Toplam':>10}{'Bildirimli':>22}"
            f"{'Bildirimsiz':>14}{'Değ.dışı':>11}"
        )
        print("─" * 73)
        unexplained = next((r for r in rows if r["category"] == "SINYALSIZ"), None)
        for row in rows:
            share = f"{_tr_int(row['reported'])} · {_tr_pct(row['reported_pct'])}"
            print(
                f"{_CAUSE_LABELS.get(row['category'], row['category']):<16}"
                f"{_tr_int(row['total']):>10}{share:>22}"
                f"{_tr_int(row['no_report']):>14}{_tr_int(row['unevaluated']):>11}"
            )
        print("─" * 73)
        if unexplained is not None:
            print(
                f"Bildirim VAR ama kategori atanamayan: {_tr_int(unexplained['reported'])} · "
                f"başarısızların {_tr_pct(unexplained['reported_share_of_failed_pct'])}’i "
                "← asıl açıklanamayan kütle"
            )
    print(
        "\nNot: 'Bildirimsiz' = kimse arıza bildirmedi; bu sürüşler için açıklanacak bir "
        "arıza iddiası yoktur."
        "\nTablodaki 'Bildirimli' yüzdesi İLGİLİ KATEGORİNİN kendi toplamına göredir "
        "(satırlar arası toplanamaz).\nAlttaki özet satır ise senaryonun TÜM başarısız "
        "sayısına göredir."
    )


def _print_scenario_criteria(report: dict) -> None:
    _section("BAŞARISIZLIK KRİTERİ")
    scenarios = _scenario_list(report)
    metrics = (
        ("source_failed", "Kaynak BASARISIZ_HARD"),
        ("threshold_failed", "Eşik kuralına uyan"),
        ("source_failed_meeting_threshold", "Kaynak başarısız + eşik uyumlu"),
        ("hidden_failed", "Kaynak başarılı fakat eşik uyumlu"),
        ("combined_failed", "Senaryo toplam başarısız"),
        ("opened_never_moved", "Uzun süre açık/hareket yok"),
    )
    print(f"{'Metrik':<37}" + "".join(f"{s['label']:>22}" for s in scenarios))
    print("─" * (37 + 22 * len(scenarios)))
    for key, label in metrics:
        print(f"{label:<37}" + "".join(f"{_tr_int(s['criteria'][key]):>22}" for s in scenarios))
    print(
        "Not: uzun süre açık/hareket yok; mongo mesafesi < 1 m ve süre ilgili eşikten "
        "uzundur. Ana kısa-sürüş kriteriyle kesişmez ve toplama ayrıca eklenmez."
    )


def _print_scenario_control(report: dict) -> None:
    _section("KONTROL GRUBU KARŞILAŞTIRMASI")
    scenarios = _scenario_list(report)
    maps = [{g["group"]: g for g in s["control"]["groups"]} for s in scenarios]
    print(f"{'Grup':<25}" + "".join(f"{s['label']:>25}" for s in scenarios))
    print("─" * (25 + 25 * len(scenarios)))
    for key, label in _GROUP_LABELS.items():
        cells = [
            f"n={_tr_int(m[key]['total'])} · sağlam {_tr_pct(m[key]['healthy_rate_pct'])}"
            for m in maps
        ]
        print(f"{label:<25}" + "".join(f"{cell:>25}" for cell in cells))


_FF_COL_W = 10  # SAHTE ARIZA ÖZETİ alt-kolon genişliği (Olay / Araç / B.görev)


def _print_scenario_false_fault(report: dict) -> None:
    _section("SAHTE ARIZA ÖZETİ")
    scenarios = _scenario_list(report)
    maps = [{r["key"]: r for r in s["false_fault"]["primary"]} for s in scenarios]
    # Tek hücrede "olay N · araç N · görev N" 32'lik kolona sığmıyordu ve senaryolar
    # birbirine giriyordu; her metrik kendi alt-kolonunda.
    group_w = 3 * _FF_COL_W
    print(f"{'':<18}" + "".join(f"{s['label']:^{group_w}}" for s in scenarios))
    header = "".join(
        f"{'Olay':>{_FF_COL_W}}{'Araç':>{_FF_COL_W}}{'B.görev':>{_FF_COL_W}}"
        for _ in scenarios
    )
    print(f"{'Hipotez':<18}{header}")
    print("─" * (18 + group_w * len(scenarios)))
    for key, label in (("GECICI_TEKNIK", "Geçici Teknik"), ("REGULASYON", "Regülasyon")):
        cells = "".join(
            f"{_tr_int(m[key]['events']):>{_FF_COL_W}}"
            f"{_tr_int(m[key]['vehicles']):>{_FF_COL_W}}"
            f"{_tr_int(m[key]['wasted_missions']):>{_FF_COL_W}}"
            for m in maps
        )
        print(f"{label:<18}{cells}")
    if any(row["cost"] is not None for mapping in maps for row in mapping.values()):
        print("\nTahmini operasyon maliyeti")
        for scenario, mapping in zip(scenarios, maps):
            total = sum((row["cost"] or 0) for row in mapping.values())
            currency = next((row["currency"] for row in mapping.values() if row["currency"]), "")
            print(f"  {scenario['label']:<24} {_tr_int(round(total)):>12} {currency}")
    print("\nDeğerlendirme Detayı")
    detail_maps = [{r["key"]: r for r in s["false_fault"]["details"]} for s in scenarios]
    labels = scenarios[0]["false_fault"]["details"]
    for row in labels:
        print(
            f"{row['label']:<22}"
            + "".join(
                f"{_tr_int(m[row['key']]['events']):>{group_w}}" for m in detail_maps
            )
        )


_VERDICT_LABELS = {
    "GERCEK_ARIZA_SUPHESI": "Gerçek arıza",
    "SAHTE_ALARM_SUPHESI": "Sahte alarm",
    "BILDIRIM_YOK": "Bildirimsiz",
    "DEGERLENDIRILEMEDI": "Değ.dışı",
}


def _print_signal_audit(rows: list[dict]) -> None:
    """SİNYAL AYIRT EDİCİLİĞİ — kural kitabının ampirik denetimi.

    Bir kodu `is_fault_signal=true` yapmak "bu olay başarısızlığı açıklar" iddiasıdır.
    Bu tablo iddiayı ölçer: kod başarısız sürüşlerde başarılılara göre kaç kat sık
    düşüyor (lift). Saha ekibi bir eşlemeyi `verified=true` yapmadan ÖNCE buraya bakar.

    Karar bu raporda DEĞİL, `fleet_status_reason` tablosunda yaşar — düşük lift bir
    kodu otomatik elemez (bkz. reason 9 'Batarya bitti': lift düşük ama gerçek saha
    görevi doğurduğu için iş kararıyla tutuldu).
    """
    from binbin.core.signal_audit import summarize_signal_discrimination

    _section("SİNYAL AYIRT EDİCİLİĞİ")
    summary = summarize_signal_discrimination(rows)
    shown = [r for r in summary if r["fail_rides"] or r["ok_rides"]]
    print(
        f"{'Kod':>4}  {'Neden':<26}{'Başarısızda':>13}{'Başarılıda':>13}"
        f"{'Lift':>9}  {'Sinyal':<8}{'Not':<22}"
    )
    print("─" * 97)
    for row in shown:
        lift = "∞" if row["lift"] is None else f"{_tr_dec(row['lift'], 1)}x"
        flag = "✓" if row["is_fault_signal"] else "✗"
        note = ""
        if row["low_volume"]:
            # Hacim yetersiz → lift anlamsız. Ne "zayıf" ne "aday" denir; sayı
            # gösterilir ama hüküm verilmez (ŞÜPHELİ≠SAHTE disiplininin istatistik hâli).
            note = "hacim yetersiz"
        elif row["is_fault_signal"] and row["weak"]:
            note = "zayıf — gerekçeyi gözden geçir"
        elif not row["is_fault_signal"] and not row["weak"] and row["lift"] != 0.0:
            note = "aday olabilir"
        # _tr_pct tek ondalık basar; burada ayırt edicilik KÜÇÜK oranlarda saklı
        # (ör. başarılıda %0,03 → "%0,0" olurdu ve 34x lift anlamsız görünürdü).
        fail_rate = f"%{_tr_dec(row['fail_rate_pct'], 2)}"
        ok_rate = f"%{_tr_dec(row['ok_rate_pct'], 3)}"
        print(
            f"{row['reason_id']:>4}  {row['description'][:25]:<26}"
            f"{fail_rate:>13}{ok_rate:>13}"
            f"{lift:>9}  {flag:<8}{note:<22}"
        )
    dead = [r for r in summary if r["is_fault_signal"] and not r["fail_rides"] and not r["ok_rides"]]
    if dead:
        codes = ", ".join(f"{r['reason_id']} ({r['description']})" for r in dead)
        print(f"\nHiç görülmeyen sinyal kodları (kural kitabında duruyor, fiilen ölü): {codes}")
    print(
        "\nLift = P(kod | başarısız) / P(kod | başarılı). ≈1 ise kod başarısızlığı "
        "AÇIKLAMIYOR.\nDüşük lift tek başına eleme gerekçesi değildir; iş gerekçesi "
        "ölçümü geçersiz kılabilir\n(karar fleet_status_reason.notes'ta gerekçesiyle yaşar)."
    )


def _print_technical_detail(report: dict) -> None:
    """TEKNİK ARIZA KIRILIMI — teknik başarısızlığın NEDENİ, kanıt kaynağıyla birlikte.

    "Teknik" tek satır olduğunda hangi arızanın kaç boşa görev doğurduğu görünmüyordu.
    Araç durum-değişim defteri sayesinde artık kural kitabındaki 58 kodun her biri
    ayrı satır olabiliyor; etiketler DB'den akar (core kod adı bilmez).

    Sıralama boşa göreve göre DEĞİL sürüş sayısına göredir (motor öyle veriyor); boşa
    görev sütunu projenin asıl çıktısı olduğu için en sağda vurgulanır.
    """
    _section("TEKNİK ARIZA KIRILIMI")
    for scenario in _scenario_list(report):
        detail = scenario.get("technical_detail") or {}
        rows = detail.get("rows") or []
        if not rows:
            continue
        print(f"\n{scenario['label']}  (toplam teknik: {_tr_int(detail['total'])})")
        print(
            f"{'Kaynak':<16}{'Neden':<26}{'Sürüş':>9}{'Gerçek':>9}"
            f"{'Sahte':>9}{'Bildirimsiz':>13}{'Boşa görev':>13}"
        )
        print("─" * 95)
        for row in rows:
            print(
                f"{row['source']:<16}{row['label'][:25]:<26}"
                f"{_tr_int(row['rides']):>9}{_tr_int(row['real_fault']):>9}"
                f"{_tr_int(row['false_alarm']):>9}{_tr_int(row['no_report']):>13}"
                f"{_tr_int(row['wasted_missions']):>13}"
            )
    print(
        "\nNot: 'Durum defteri' satırları araç IoT durum-değişim kaydından (kural kitabı "
        "doğrulanabilir\nkanıt), 'Metin' satırları sürüş mesajı/kullanıcı yorumundan gelir."
    )


def _print_regulation_matrix(report: dict) -> None:
    """Kategori × verdict çapraz-tablosu. Kural kitabı (fleet_status_reason) DB'de
    yaşar; burada yalnız zaten hesaplanmış sayılar basılır (bkz. core/scenario_analysis).
    """
    _section("REGÜLASYON MATRİSİ")
    scenarios = _scenario_list(report)
    col_width = 16
    for scenario in scenarios:
        matrix = scenario["regulation_matrix"]
        rows = matrix["rows"]
        if not rows:
            continue
        print(f"\n{scenario['label']}")
        header = (
            f"{'Kategori':<16}"
            + "".join(f"{_VERDICT_LABELS[v]:>{col_width}}" for v in matrix["verdict_order"])
            + f"{'Toplam':>10}"
        )
        print(header)
        print("─" * (16 + col_width * len(matrix["verdict_order"]) + 10))
        for row in rows:
            cells = "".join(
                f"{_tr_int(row['counts'][v]):>{col_width}}" for v in matrix["verdict_order"]
            )
            label = _CAUSE_LABELS.get(row["category"], row["category"])
            print(f"{label:<16}{cells}{_tr_int(row['total']):>10}")
    if len(scenarios) > 1:
        print("\nNot: her senaryo kendi başarısız kümesine göre ayrı hesaplanır.")


_MAX_HOTSPOT_ROWS = 100  # araç sıcak nokta tablosunda gösterilecek azami satır


def _print_scenario_vehicles(report: dict) -> None:
    _section("ARAÇ SICAK NOKTALARI")
    scenarios = _scenario_list(report)
    maps = []
    labels = {}
    all_keys = set()
    for scenario in scenarios:
        values = {}
        for row in scenario["vehicle"]["vehicles"]:
            key = row["vehicle_id"]
            values[key] = row["failures"]
            labels[key] = str(row.get("external_code") or key)
            all_keys.add(key)
        maps.append(values)
    min_failures = scenarios[0]["vehicle"]["min_failures"]
    selected = [key for key in all_keys if max(m.get(key, 0) for m in maps) >= min_failures]
    selected.sort(key=lambda key: max(m.get(key, 0) for m in maps), reverse=True)
    print(f"{'Araç':<18}" + "".join(f"{s['label']:>20}" for s in scenarios))
    print("─" * (18 + 20 * len(scenarios)))
    for key in selected[:_MAX_HOTSPOT_ROWS]:
        print(f"{labels[key]:<18}" + "".join(f"{_tr_int(m.get(key, 0)):>20}" for m in maps))


def _print_scenario_subregions(report: dict) -> None:
    _section("ALT BÖLGE SICAK NOKTALARI")
    scenarios = _scenario_list(report)
    maps = []
    keys = set()
    for scenario in scenarios:
        values = {(r["city"], r["sub_region_code"]): r for r in scenario["subregion"]["sub_regions"]}
        maps.append(values)
        keys.update(values)
    ordered = sorted(keys, key=lambda key: max(m.get(key, {}).get("failed", 0) for m in maps), reverse=True)
    for key in ordered:
        totals = {
            int(values[key]["total_rides"])
            for values in maps
            if key in values
        }
        if len(totals) != 1:
            raise ValueError(f"Alt bölge senaryolarının toplam sürüş sayıları uyuşmuyor: {key}")
        total_rides = next(iter(totals))
        print(f"\n{key[0]} · Bölge {key[1]} · n={_tr_int(total_rides)}")
        for scenario, values in zip(scenarios, maps):
            row = values.get(key, {})
            print(
                f"  {scenario['label']:<22} başarısız {_tr_pct(row.get('failure_rate_pct', 0)):>7} · "
                f"şüpheli/1000 {row.get('false_alarm_per_1000', 0):.2f}"
            )


def _print_scenario_hourly(report: dict) -> None:
    _section("SAATLİK BAŞARISIZLIK ORANI (YEREL SAAT)")
    scenarios = _scenario_list(report)
    maps = [{(r["city"], r["hour"]): r for r in s["hourly"]["buckets"]} for s in scenarios]
    keys = sorted(set().union(*(m.keys() for m in maps)))
    print(f"{'Şehir / saat':<24}{'n':>12}" + "".join(f"{s['label']:>20}" for s in scenarios))
    print("─" * (36 + 20 * len(scenarios)))
    for key in keys:
        totals = {
            int(values[key]["total"])
            for values in maps
            if key in values
        }
        if len(totals) != 1:
            raise ValueError(f"Saatlik senaryoların toplam sürüş sayıları uyuşmuyor: {key}")
        total_rides = next(iter(totals))
        print(
            f"{key[0] + ' ' + format(key[1], '02d') + ':00':<24}"
            f"{_tr_int(total_rides):>12}"
            + "".join(f"{_tr_pct(m.get(key, {}).get('failure_rate_pct', 0)):>20}" for m in maps)
        )


_HANDLERS = {
    "ingest": cmd_ingest,
    "classify": cmd_classify,
    "assess": cmd_assess,
    "analyze": cmd_analyze,
    "loads": cmd_loads,
}


def main(argv: list[str] | None = None) -> None:
    """CLI: argümanları ayrıştırır ve ilgili komutu çalıştırır."""
    _force_utf8_stdout()
    args = build_parser().parse_args(argv)
    _HANDLERS[args.command](args)


if __name__ == "__main__":
    main()
