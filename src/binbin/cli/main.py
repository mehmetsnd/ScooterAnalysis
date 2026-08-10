"""CLI giriş noktası — imperative shell. İş mantığı yok; argparse ile komutu yakalar,
alttaki modüllere paslar.

    python -m binbin.cli ingest   [--data-dir data_raw] [--file F] [kapsam]
    python -m binbin.cli classify [--refresh] [--batch-size 10000] [kapsam]
    python -m binbin.cli assess   [--refresh] [kapsam]
    python -m binbin.cli analyze  [--detay] [--derin] [--false-fault] [--sinyal-denetimi]
                                  [--esik-taramasi] [--charts DIR]
                                  [--wi-duration SN --wi-distance M]

`ingest` CSV türünü başlık satırından ayırıp her türü kendi transformer'ına yönlendirir
(bkz. `data.ingest.detect_csv_kind`); `--file` verilirse yalnız o dosya yüklenir.
`analyze` Mevcut Kural'ı daima hesaplar; --wi-duration/--wi-distance BİRLİKTE verilirse
Özel Kural senaryosu ve geçiş karşılaştırması eklenir.

Kapsam: bayrak yoksa config.DEFAULT_SCOPE · --country/--city config'i ezer · --all filtreyi
kaldırır. --all ile lokasyon bayrakları birlikte kullanılamaz (hata).
"""

import argparse
import math
import sys
from pathlib import Path

from binbin.config import DEFAULT_SCOPE, UNRESTRICTED_SCOPE, Scope
from binbin.reporting.format import (
    GROUP_LABELS as _GROUP_LABELS,
    fmt_threshold as _fmt_thr,
    signed_int as _signed_int,
    tr_dec as _tr_dec,
    tr_int as _tr_int,
    tr_pct as _tr_pct,
)
from binbin.data.repository import RideRepository


def _repository() -> RideRepository:
    """Composition root: somut veri kaynağının seçildiği TEK yer.

    Komutlar `RideRepository` soyutlamasına bağlıdır; başka bir kaynağa geçmek yalnız
    burayı değiştirir (DIP). İçe aktarma gövdede: DB sürücüsü kurulu olmadan da
    `binbin.cli.main` import edilebilsin (testler bunu kullanır).
    """
    from binbin.data.postgres_repo import PostgresRideRepository

    return PostgresRideRepository()


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
        help="Sahte arıza özeti + kategori-sonuç matrisi + teknik arıza kırılımı",
    )
    p_an.add_argument(
        "--sinyal-denetimi", action="store_true", dest="sinyal_denetimi",
        help="Kural kitabındaki her kodun ayırt ediciliğini (lift) ölçüp raporla",
    )
    p_an.add_argument(
        "--esik-taramasi", action="store_true", dest="esik_taramasi",
        help="Özel Kural süre/mesafe ızgarasını F1 ile tarar, en isabetli eşiği önerir",
    )
    p_an.add_argument(
        "--kelime-denetimi", action="store_true", dest="kelime_denetimi",
        help="Anahtar kelime kural kitabının ayırt ediciliğini (lift) ölçüp raporlar",
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


def _run_ingest_for_kind(kind: str, csv_path: Path, scope: Scope, force: bool) -> None:
    from binbin.data.ingest import run_ingest
    from binbin.data.ingest_status import run_status_ingest

    # Doğrulama tüm dosyalar için önden koştuğu için künye satırı burada tekrarlanır;
    # yoksa çoklu yüklemede hangi raporun hangi dosyaya ait olduğu okunmuyor.
    print(f"\nYükleniyor [{kind}]: {csv_path.name}")
    if kind == "rides":
        _print_rides_ingest_report(run_ingest(csv_path, scope, force=force))
    else:
        _print_status_ingest_report(run_status_ingest(csv_path, scope, force=force))


def _check_schema(kind: str, csv_path: Path) -> None:
    """Şema sözleşmesini doğrular. Süreç kararı CLI'da kalır; `data/` yalnız fırlatır."""
    from binbin.data.ingest import SchemaContractError, validate_csv_header

    print(f"\nCSV [{kind}]: {csv_path} ({csv_path.stat().st_size / 1_048_576:.1f} MB)")
    try:
        validate_csv_header(csv_path, kind)
    except SchemaContractError as exc:
        raise SystemExit(f"Hata: {exc}")


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
        selected = [(detect_csv_kind(csv_path), csv_path)]
    else:
        if not files:
            raise SystemExit("Hata: data_raw/ içinde .csv yok.")
        # SIRA YÜK TAŞIR: rides ÖNCE. Sürüş ingest'i vehicle'ı plakayla açar; ters sırada
        # araçlar plakasız oluşur ve ON CONFLICT DO NOTHING onları güncellemez → plaka kaybolur.
        by_kind: dict[str, list[Path]] = {"rides": [], "status": []}
        for f in files:
            by_kind[detect_csv_kind(f)].append(f)
        selected = [
            (kind, select_csv(kind_files, explicit=None, prompt_fn=prompt_fn))
            for kind, kind_files in by_kind.items()
            if kind_files
        ]

    # Doğrulama HİÇBİR yükleme başlamadan önce biter: sıra rides→status olduğu için
    # bozuk bir status başlığı, aksi hâlde ~1M satırlık rides yüklemesinden sonra
    # fark edilirdi. Tek dosya yolu da aynı sözleşmeyi paylaşsın diye tek akış.
    for kind, csv_path in selected:
        _check_schema(kind, csv_path)
    for kind, csv_path in selected:
        _run_ingest_for_kind(kind, csv_path, scope, args.force)


def cmd_classify(args: argparse.Namespace) -> None:
    repo = _repository()
    ascope = repo.resolve_scope(_scope_from_args(args))
    result = repo.classify_all(ascope, batch_size=args.batch_size, refresh=args.refresh)
    mode = "tam yeniden sınıflandırma" if args.refresh else "artımlı"
    print(
        f"Sınıflandırma ({mode}): işlenen={result['processed']:,} "
        f"kategori-atanan={result['classified']:,}"
    )


def cmd_assess(args: argparse.Namespace) -> None:
    repo = _repository()
    ascope = repo.resolve_scope(_scope_from_args(args))
    result = repo.assess_all(ascope, refresh=args.refresh)
    mode = "tam yeniden hesap" if args.refresh else "artımlı"
    print(
        f"Değerlendirme ({mode}): {result['assessed']:,} sürüş için "
        "false_fault_assessment yazıldı."
    )


def cmd_loads(args: argparse.Namespace) -> None:
    rows = _repository().list_data_loads()
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
    from binbin.core import scenario_analysis, threshold_scan

    custom_thr = _custom_rule_from_args(args)
    repo = _repository()
    ascope = repo.resolve_scope(_scope_from_args(args))

    # Bilinçli takas: timeline STREAM edilir, agregasyon Python'da yapılır — classify/assess
    # mantığı SQL'de tekrarlanmasın diye. Bellek O(varlık), aylık ölçekte uygun; 10M+ satırda
    # yeniden değerlendir. Sinyal-join sınırı senaryo eşiklerinden türetilir (sabit yazılmaz,
    # yeni senaryo eklenirse kendiliğinden genişler); ölçüm: timeline 37,2 sn → guard'la düşer.
    scenario_bounds = scenario_analysis.candidate_bounds(
        scenario_analysis.build_scenarios(custom_thr)
    )
    scan_acc = threshold_scan.ThresholdScanAccumulator() if args.esik_taramasi else None
    bounds = scenario_bounds
    if scan_acc is not None:
        # Izgara kendi üst sınırını gerektirir: dışında kalan satırlarda sinyal-join çalışmaz,
        # field_fault NULL görünür ve isabet düşük ölçülür. Eleman bazında max = iki sınırın
        # birleşimi; hiçbirini daraltmaz.
        scan_bounds = threshold_scan.grid_bounds()
        bounds = (max(bounds[0], scan_bounds[0]), max(bounds[1], scan_bounds[1]))
    report = scenario_analysis.analyze_scenarios(
        repo.analysis_timeline(ascope, candidate_bounds=bounds),
        custom=custom_thr,
        cost_rows=repo.ops_cost_rows(ascope),
        ooc_counts=repo.out_of_content_counts(ascope),
        scan=scan_acc,
    )
    _print_scenario_definitions(report)
    _print_scenario_overview(report)
    _print_scenario_comparisons(report)
    _print_scenario_causes(report)
    _print_scenario_criteria(report)
    _print_scenario_control(report)
    if args.false_fault:
        _print_scenario_false_fault(report)
        _print_category_matrix(report)
        _print_technical_detail(report)
    if args.sinyal_denetimi:
        _print_signal_audit(repo.signal_discrimination_rows(ascope))
    if args.kelime_denetimi:
        _print_keyword_audit(repo.comment_corpus_rows(ascope))
    if args.detay:
        _print_scenario_vehicles(report)
        _print_scenario_subregions(report)
    if args.derin:
        _print_scenario_hourly(report)
    scan_report = scan_acc.finalize() if scan_acc is not None else None
    if scan_report is not None:
        _print_threshold_scan(scan_report)

    if args.charts:
        from binbin.reporting import charts

        paths = [
            charts.chart_scenario_overview(report, args.charts),
            charts.chart_scenario_causes(report, args.charts),
            charts.chart_scenario_control(report, args.charts),
        ]
        if args.false_fault:
            paths.append(charts.chart_scenario_false_fault(report, args.charts))
        if args.detay:
            paths.append(charts.chart_scenario_vehicles(report, args.charts))
            paths.append(charts.chart_scenario_subregions(report, args.charts))
        if args.derin:
            paths.append(charts.chart_scenario_hourly(report, args.charts))
        if scan_report is not None:
            paths.append(charts.chart_threshold_scan(scan_report, args.charts))
            paths.append(charts.chart_threshold_tradeoff(scan_report, args.charts))
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
            f"{'Başarısızdan başarılıya dönen':<35}{_tr_int(c['failed_to_success']):>10}  "
            f"başlangıç başarısızlarının {_tr_pct(c['failed_to_success_pct'])}’i"
        )
        print(
            f"{'Başarıdan başarısıza dönen':<35}{_tr_int(c['success_to_failed']):>10}  "
            f"başlangıç başarılılarının {_tr_pct(c['success_to_failed_pct'])}’i"
        )
        print(
            f"{'Net başarısız değişimi':<35}{_signed_int(c['failed_count_delta']):>10}  "
            f"oran farkı {c['failure_rate_pp_delta']:+.1f} puan · "
            f"göreli {c['relative_failed_pct']:+.1f}%"
        )
        if c["failed_to_unevaluated"]:
            print(f"{'Başarısızdan değerlendirme dışına':<35}{_tr_int(c['failed_to_unevaluated']):>10}")
        if c["success_to_unevaluated"]:
            print(f"{'Başarıdan değerlendirme dışına':<35}{_tr_int(c['success_to_unevaluated']):>10}")
        if c["unevaluated_to_failed"]:
            print(f"{'Değerlendirme dışından başarısıza':<35}{_tr_int(c['unevaluated_to_failed']):>10}")
        if c["unevaluated_to_success"]:
            print(f"{'Değerlendirme dışından başarıya':<35}{_tr_int(c['unevaluated_to_success']):>10}")


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
        rows = scenario["category_matrix"]["rows"]
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


# "(ş)" = şüphesi. Kesin hüküm veremeyiz (ALTIN KURAL) — dar kolonda bile niteleyici düşmez.
_VERDICT_LABELS = {
    "GERCEK_ARIZA_SUPHESI": "Gerçek arıza(ş)",
    "SAHTE_ALARM_SUPHESI": "Sahte alarm(ş)",
    "BILDIRIM_YOK": "Bildirimsiz",
    "DEGERLENDIRILEMEDI": "Değ.dışı",
}


def _print_signal_audit(rows: list[dict]) -> None:
    """SİNYAL AYIRT EDİCİLİĞİ — kural kitabının ampirik denetimi: kod başarısız sürüşlerde
    başarılılara göre kaç kat sık düşüyor (lift). Karar burada değil `fleet_status_reason`da
    yaşar; düşük lift otomatik elemez (reason 9 'Batarya bitti' iş kararıyla tutuldu).
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


def _print_keyword_audit(rows) -> None:
    """ANAHTAR KELİME AYIRT EDİCİLİĞİ — kural kitabının kelime tarafının denetimi.

    `--sinyal-denetimi`'nin kelime karşılığı. Kelime kümeleri kanıt üretiminin
    merkezindedir (`_has_fault_text` → `fault_reported` → boşa görev ve F1), bu
    yüzden hangi kelimenin gerçekten ayırt ettiği ÖLÇÜLÜR, varsayılmaz.
    """
    from binbin.core import keywords as kw_sets
    from binbin.core.keyword_audit import (
        MIN_KEYWORD_VOLUME,
        coverage_summary,
        summarize_keyword_discrimination,
    )

    sets = {
        "TEKNIK": kw_sets.TECHNICAL_KEYWORDS,
        "REGULASYON": kw_sets.REGULATION_KEYWORDS,
        "KULLANICI": kw_sets.USER_KEYWORDS,
        "SISTEM": kw_sets.SYSTEM_KEYWORDS,
    }
    # Korpus generator: iki kez tüketilemez, listeye alınır (metinli sürüşler
    # tüm kütlenin küçük bir dilimidir — ~32 bin satır, bellek sorunu değil).
    corpus = list(rows)
    summary = summarize_keyword_discrimination(corpus, sets)
    coverage = coverage_summary(corpus, sets)

    _section("ANAHTAR KELİME AYIRT EDİCİLİĞİ")
    print(
        f"Korpus: metni olan {_tr_int(len(corpus))} sürüş · "
        f"başarısız+metinli {_tr_int(coverage['failed_with_text'])}"
    )
    print(
        f"Kapsam: {_tr_int(coverage['matched'])} eşleşti "
        f"({_tr_pct(coverage['matched_pct'])}) · "
        f"{_tr_int(coverage['unmatched'])} eşleşmedi"
    )
    print(
        f"\n{'Set':<11}{'Anahtar':<26}{'Başarısızda':>12}{'Başarılıda':>12}"
        f"{'Lift':>8}{'Tekil':>7}  {'Not':<28}"
    )
    print("─" * 105)
    shown = [r for r in summary if not r["dead"]]
    for row in shown:
        lift = "∞" if row["lift"] is None else f"{_tr_dec(row['lift'], 1)}x"
        note = ""
        if row["lift"] is not None and row["lift"] < 1.0 and row["ok_hits"]:
            # Başarılıda daha sık. "Yanlış" demek DEĞİLDİR: başarılı bir sürüşte de
            # gerçek arıza bildirilmiş olabilir. Hüküm gerekçeyle keywords.py'de.
            note = "TERS — başarılıda daha sık"
        elif row["uncontaminated"]:
            note = "temiz (başarılıda hiç yok)"
        elif row["low_volume"]:
            note = "hacim yetersiz"
        elif row["weak"]:
            note = "zayıf — gerekçeyi gözden geçir"
        print(
            f"{row['set_name']:<11}{row['keyword'][:25]:<26}"
            f"{_tr_int(row['fail_hits']):>12}{_tr_int(row['ok_hits']):>12}"
            f"{lift:>8}{_tr_int(row['marginal_hits']):>7}  {note:<28}"
        )
    dead = [r for r in summary if r["dead"]]
    if dead:
        print(
            f"\nHiç eşleşmeyen (fiilen ölü) {len(dead)} anahtar: "
            + ", ".join(f"{r['keyword']}" for r in sorted(dead, key=lambda r: r["keyword"]))
        )
    print(
        "\nLift = P(kelime | başarısız) / P(kelime | başarılı). Payda METİNLİ sürüşlerdir;"
        "\nsessizleri katmak her kelimeye sahte lift kazandırırdı."
        f"\n'Tekil' = o kelime silinse TÜM kanıtını kaybedecek başarısız sürüş sayısı."
        f"\nBenimseme: (A) başarısızda ≥{MIN_KEYWORD_VOLUME} VE lift ≥2,0 · VEYA (B) başarılıda HİÇ yok."
        "\nBu çıktı KARAR VERMEZ; gerekçe keywords.py yorumlarına yazılır."
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
            f"{'Kaynak':<16}{'Neden':<26}{'Sürüş':>9}{'Gerçek(ş)':>10}"
            f"{'Sahte(ş)':>9}{'Bildirimsiz':>13}{'Boşa görev':>13}"
        )
        print("─" * 96)
        for row in rows:
            print(
                f"{row['source']:<16}{row['label'][:25]:<26}"
                f"{_tr_int(row['rides']):>9}{_tr_int(row['real_fault']):>10}"
                f"{_tr_int(row['false_alarm']):>9}{_tr_int(row['no_report']):>13}"
                f"{_tr_int(row['wasted_missions']):>13}"
            )
    print(
        "\nNot: 'Durum defteri' satırları araç IoT durum-değişim kaydından (kural kitabı "
        "doğrulanabilir\nkanıt), 'Metin' satırları sürüş mesajı/kullanıcı yorumundan gelir. "
        "(ş) = şüpheli hipotez, kesin hüküm değil."
    )


def _print_category_matrix(report: dict) -> None:
    """Kategori × verdict çapraz-tablosu. Kural kitabı (fleet_status_reason) DB'de
    yaşar; burada yalnız zaten hesaplanmış sayılar basılır (bkz. core/scenario_analysis).
    """
    _section("KATEGORİ-SONUÇ MATRİSİ")
    scenarios = _scenario_list(report)
    col_width = 16
    for scenario in scenarios:
        matrix = scenario["category_matrix"]
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


def _print_threshold_scan(scan: dict) -> None:
    """EŞİK TARAMASI çıktısı. Metodoloji için bkz. core/threshold_scan modül docstring'i.
    İki karşıt hedef ayrı basılır: Senaryo A gevşetme (kaçırılan kanıtlı şikayeti azaltır),
    Senaryo B sıkılaştırma (işaretlenen hacmi azaltır) — B, A'nın simetrik aynasıdır.
    """
    from binbin.core.threshold_scan import BASELINE

    _section("EŞİK TARAMASI")
    print(
        f"Aday havuzu: süre < {_fmt_thr(scan['pool_bounds']['duration'])} sn ve "
        f"mesafe < {_fmt_thr(scan['pool_bounds']['distance'])} m  "
        f"({_tr_int(scan['pool_size'])} sürüş, bağımsız kanıtlı {_tr_int(scan['reported_in_pool'])})"
    )
    print(
        f"{'Süre(sn)':>9}{'Mesafe(m)':>11}{'İşaretli':>11}{'İsabet':>9}"
        f"{'Kapsam':>9}{'F1':>8}{'Boşa görev':>13}  "
    )
    print("─" * 82)
    for row in sorted(scan["rows"], key=lambda r: (r["duration_threshold"], r["distance_threshold"])):
        tag = " ← Mevcut Kural" if row["is_baseline"] else ""
        if scan["recommended"] is row:
            tag += "  ★ Senaryo A (isabet/kapsam)"
        if scan["conservative"] is row and scan["conservative"] is not scan["baseline_row"]:
            tag += "  ◆ Senaryo B (boşa görev azaltma)"
        print(
            f"{_fmt_thr(row['duration_threshold']):>9}{_fmt_thr(row['distance_threshold']):>11}"
            f"{_tr_int(row['flagged']):>11}{_tr_pct(row['precision_pct']):>9}"
            f"{_tr_pct(row['recall_pct']):>9}{_tr_pct(row['f1_pct']):>8}"
            f"{_tr_int(row['wasted_missions']):>13}{tag}"
        )

    base, a, b = scan["baseline_row"], scan["recommended"], scan["conservative"]
    _print_threshold_scenario_comparison(base, a, b)

    # İki senaryo simetrik sunulur; çıktı birini önermez.
    missed_base = _tr_pct(100.0 - base["recall_pct"])
    missed_a = _tr_pct(100.0 - a["recall_pct"])
    missed_b = _tr_pct(100.0 - b["recall_pct"])

    print(
        f"\nSenaryo A — Kapsam Önceliği (süre<{_fmt_thr(a['duration_threshold'])} sn, "
        f"mesafe<{_fmt_thr(a['distance_threshold'])} m):"
    )
    print("  Amaç : incelemeye hiç girmeyen kanıtlı şikayeti azaltmak.")
    print(f"  Kazanç: kapsam {_tr_pct(base['recall_pct'])} → {_tr_pct(a['recall_pct'])} "
          f"(gözden kaçan ~{missed_base} → ~{missed_a}); "
          f"{_signed_int(a['real_fault'] - base['real_fault'])} gerçek arıza şüphesi ilk kez incelenir.")
    print(f"  Bedel : {_signed_int(a['flagged_delta'])} sürüş 'başarısız' damgası alır, "
          f"saha yükü {_signed_int(a['wasted_delta'])} boşa görev.")

    print(
        f"\nSenaryo B — Operasyonel Hacim Önceliği (süre<{_fmt_thr(b['duration_threshold'])} sn, "
        f"mesafe<{_fmt_thr(b['distance_threshold'])} m):"
    )
    print("  Amaç : işaretlenen hacmi ve saha yükünü azaltmak.")
    print(f"  Kazanç: işaretlenen sürüş {_signed_int(b['flagged_delta'])}, "
          f"boşa görev {_signed_int(b['wasted_delta'])} — serbest kalan kapasite "
          f"gerçek arızalara yönlendirilebilir.")
    print(f"  Bedel : kapsam {_tr_pct(base['recall_pct'])} → {_tr_pct(b['recall_pct'])} "
          f"(gözden kaçan ~{missed_base} → ~{missed_b}); "
          f"{_signed_int(b['real_fault'] - base['real_fault'])} gerçek arıza şüphesi artık incelenmez.")

    # Takas oranı = gerçek arıza şüphesi / şüpheli sahte alarm değişimi. İki senaryo
    # için AYRI hesaplanır; eşitlik ölçülmeden iddia edilmez (altın kural).
    def _tradeoff(row: dict) -> float | None:
        fake = abs(row["suspect_false"] - base["suspect_false"])
        return abs(row["real_fault"] - base["real_fault"]) / fake if fake else None

    ra, rb = _tradeoff(a), _tradeoff(b)
    if ra is None and rb is None:
        ratio_line = "Takas oranı hesaplanamadı (senaryolar Mevcut Kural'dan farklı sürüş getirmiyor)."
    elif ra is not None and rb is not None and abs(ra - rb) < 0.05:
        ratio_line = (
            f"Takas oranı iki yönde de aynı (~{_tr_dec(ra, 1)}): kümeye giren ya da çıkan\n"
            "her 1 şüpheli sahte alarmın yanında o kadar gerçek arıza şüphesi de gelir/gider."
        )
    else:
        parts = [f"A ~{_tr_dec(ra, 1)}" if ra is not None else "A hesaplanamadı",
                 f"B ~{_tr_dec(rb, 1)}" if rb is not None else "B hesaplanamadı"]
        ratio_line = (
            f"Takas oranı (gerçek arıza şüphesi / şüpheli sahte alarm): {' · '.join(parts)}\n"
            "— iki yön farklı, senaryolar ayrı değerlendirilmeli."
        )

    print(
        "\nNot: eşik değişikliği gerçek bir sürüşü başarılı/başarısız YAPMAZ; yalnız "
        "'başarısız'\ndamgasını değiştirir. İsabet ızgarada sabit olduğu için hiçbir "
        "senaryo teşhisi\niyileştirmez — yalnız neye bakıldığının kapsamını ve hacmini "
        f"değiştirir.\n{ratio_line}\nHangisinin tercih edileceği bir maliyet dengesi "
        "sorusudur; ops_cost_model boş olduğu\niçin bu çıktı bir eşik ÖNERMEZ, "
        "seçeneklerin etkisini ölçer. Ciro/TL iddiası yoktur."
    )


def _print_threshold_scenario_comparison(base: dict, a: dict, b: dict) -> None:
    """SENARYO KARŞILAŞTIRMASI — Mevcut Kural / Senaryo A / Senaryo B yan yana."""
    print("\nSenaryo Karşılaştırması")
    col = 16
    print(f"{'':<20}{'Mevcut Kural':>{col}}{'Senaryo A':>{col}}{'Senaryo B':>{col}}")
    print("─" * (20 + col * 3))
    rows = (
        ("İşaretlenen sürüş", "flagged", _tr_int),
        ("İsabet", "precision_pct", _tr_pct),
        ("Kapsam", "recall_pct", _tr_pct),
        ("F1", "f1_pct", _tr_pct),
        ("Boşa görev", "wasted_missions", _tr_int),
    )
    for label, key, fmt in rows:
        print(f"{label:<20}{fmt(base[key]):>{col}}{fmt(a[key]):>{col}}{fmt(b[key]):>{col}}")


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
    """CLI: argümanları ayrıştırır ve ilgili komutu çalıştırır.

    `UnknownScopeName` burada `SystemExit`'e çevrilir: process kararı (çık, kodu 1)
    shell'in işidir, `data/` katmanının değil. Non-zero exit `run.ps1`'in
    `$LASTEXITCODE` guard'ını tetikler ve pipeline 1. adımda durur.
    """
    from binbin.data.repository import UnknownScopeName

    _force_utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        _HANDLERS[args.command](args)
    except UnknownScopeName as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
