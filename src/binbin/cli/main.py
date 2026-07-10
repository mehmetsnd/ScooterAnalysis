"""Projenin CLI (Command Line Interface) giriş noktası — Imperative Shell.

Burada iş mantığı (core logic) bulunmaz; sadece terminalden gelen komutları 
argparse ile yakalar, ilgili argümanları parse eder ve alttaki modüllere paslarız.

Kullanım örnekleri (PYTHONPATH=src klasöründe):
    python -m binbin.cli ingest   [--data-dir data_raw] [--country ...] [--city ...] [--all]
    python -m binbin.cli classify [--batch-size 10000]  [--country ...] [--city ...] [--all]
    python -m binbin.cli assess   [--country ...] [--city ...] [--all]
    python -m binbin.cli analyze  [--detay] [--derin] [--false-fault] [--charts DIR] ...

Scope (Kapsam) Mantığı:
    Hiçbir bayrak verilmezse   -> config.DEFAULT_SCOPE (Türkiye + İstanbul Avrupa/Anadolu)
    --country/--city verilirse -> Girilen lokasyonlar çekilir (config'i ezer)
    --all flag'i verilirse     -> Filtre kalkar, DB'deki tüm data işlenir
    Not: --all ile lokasyon bayrakları aynı anda kullanılamaz, hata patlatır.
"""

import argparse
import sys
from pathlib import Path

from binbin.config import DEFAULT_SCOPE, UNRESTRICTED_SCOPE, Scope
from binbin.core import analysis


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
    """CLI argüman ayrıştırıcısını kurar (subcommand'lar + global --source)."""
    parser = argparse.ArgumentParser(prog="binbin", description="Binbin başarısız sürüş analizi")
    parser.add_argument(
        "--source",
        choices=("postgres", "mock"),
        default="postgres",
        help="Veri kaynağı (mock → DB'ye bağlanmaz; yalnız analyze)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="CSV → Postgres")
    p_ing.add_argument("--data-dir", type=Path, default=Path("data_raw"), help="Ham CSV klasörü")
    p_ing.add_argument("--file", type=Path, metavar="PATH", help="İşlenecek CSV (çok dosya varsa seç)")
    p_ing.add_argument("--force", action="store_true", help="Zaten yüklenmiş dosyayı yeniden yükle")
    _add_scope_args(p_ing)

    p_cls = sub.add_parser("classify", help="Başarısız sürüşleri sınıflandır")
    p_cls.add_argument("--batch-size", type=int, default=10000)
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
    p_an.add_argument("--false-fault", action="store_true", help="Sahte arıza özeti")
    p_an.add_argument("--charts", type=Path, metavar="DIR", help="PNG'leri bu klasöre üret")
    _add_scope_args(p_an)
    return parser


def select_csv(files: list[Path], explicit: Path | None, prompt_fn=None) -> Path:
    """İşlenecek CSV'yi seçer (saf, test edilebilir; web de çağırabilir).

    - explicit verilmişse doğrula ve döndür.
    - tek dosya → otomatik.
    - çok dosya + prompt_fn → numaralı menü.
    - çok dosya + prompt_fn yok (pipe/web/CI) → dosyaları listeleyip hata ver.
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
            # İnteraktif giriş yoksa (pipe/CI/web) temiz hataya düş.
            raise SystemExit(f"\nSeçim yapılmadı; --file ile birini seç:\n{listing}")
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return files[int(choice) - 1]
        print(f"Geçersiz seçim: {choice!r}")


# --------------------------------------------------------------------- komutlar
def _require_postgres(source: str, command: str) -> None:
    if source != "postgres":
        raise SystemExit(f"Hata: '{command}' komutu Postgres gerektirir (--source mock desteklenmez).")


def cmd_ingest(args: argparse.Namespace) -> None:
    from binbin.data.ingest import list_source_csvs, run_ingest

    _require_postgres(args.source, "ingest")
    scope = _scope_from_args(args)
    files = list_source_csvs(args.data_dir)
    prompt_fn = input if sys.stdin is not None and sys.stdin.isatty() else None
    csv_path = select_csv(files, args.file, prompt_fn)
    print(f"CSV: {csv_path} ({csv_path.stat().st_size / 1_048_576:.1f} MB)")
    report = run_ingest(csv_path, scope, force=args.force)
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


def cmd_classify(args: argparse.Namespace) -> None:
    from binbin.data.postgres_repo import PostgresRideRepository

    _require_postgres(args.source, "classify")
    repo = PostgresRideRepository()
    ascope = repo.resolve_scope(_scope_from_args(args))
    result = repo.classify_all(ascope, batch_size=args.batch_size)
    print(f"Sınıflandırma: işlenen={result['processed']:,} kategori-atanan={result['classified']:,}")


def cmd_assess(args: argparse.Namespace) -> None:
    from binbin.data.postgres_repo import PostgresRideRepository

    _require_postgres(args.source, "assess")
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

    _require_postgres(args.source, "loads")
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


def _make_repo(source: str):
    if source == "mock":
        from binbin.data.mock_source import MockRideRepository

        return MockRideRepository()
    from binbin.data.postgres_repo import PostgresRideRepository

    return PostgresRideRepository()


def cmd_analyze(args: argparse.Namespace) -> None:
    repo = _make_repo(args.source)
    ascope = repo.resolve_scope(_scope_from_args(args))

    cause = analysis.cause_distribution(repo, ascope)
    criteria = analysis.failure_criteria_check(repo, ascope)
    control = analysis.control_group_comparison(repo, ascope)
    _print_cause(cause)
    _print_criteria(criteria)
    _print_control(control)

    chart_data: dict = {}
    if args.false_fault:
        ff = analysis.false_fault_summary(repo, ascope)
        _print_false_fault(ff)
    if args.detay:
        vh = analysis.vehicle_hotspots(repo, ascope)
        sr = analysis.subregion_hotspots(repo, ascope)
        _print_vehicle_hotspots(vh)
        _print_subregion(sr)
        chart_data["vehicle"] = vh
        chart_data["subregion"] = sr
    if args.derin:
        hr = analysis.hour_region_breakdown(repo, ascope)
        _print_hourly(hr)
        chart_data["hourly"] = hr

    if args.charts:
        from binbin.reporting import charts

        paths = [
            charts.chart_cause_distribution(cause, args.charts),
            charts.chart_control_group(control, args.charts),
        ]
        if "vehicle" in chart_data:
            paths.append(charts.chart_vehicle_hotspots(chart_data["vehicle"], args.charts))
        if "subregion" in chart_data:
            paths.append(charts.chart_subregion_false_fault(chart_data["subregion"], args.charts))
        if "hourly" in chart_data:
            paths.append(charts.chart_hourly_failure_rate(chart_data["hourly"], args.charts))
        print("\nGrafikler:")
        for p in paths:
            print(f"  {p}")


# ------------------------------------------------------------------ yazdırıcılar
def _print_cause(d: dict) -> None:
    print(f"\n=== Neden Dağılımı (toplam başarısız: {d['total_failed']:,}) ===")
    for c in d["categories"]:
        print(f"  {c['category']:<12} {c['count']:>8,}  %{c['pct']}")
    s = d["signalless"]
    print(f"  {'SİNYALSİZ':<12} {s['count']:>8,}  %{s['pct']}  (kategori uydurulmaz)")


def _print_criteria(d: dict) -> None:
    print(f"\n=== Başarısızlık Kriteri ({d['criterion']}) ===")
    fm = d["failed_meeting_criterion"]
    hf = d["hidden_failed"]
    print(f"  kritere uyan başarısız : {fm['count']:,}  (başarısızın %{fm['pct_of_failed']})")
    print(f"  gizli başarısız (BAŞARILI ama kritere uyan): {hf['count']:,}  (%{hf['pct_of_success']})")
    print(f"  açıldı hiç gitmedi (dist≈0, süre>120sn): {d['opened_never_moved']['count']:,}")


def _print_control(d: dict) -> None:
    print("\n=== Kontrol Grubu Karşılaştırması (healthy_proof oranı) ===")
    for g in d["groups"]:
        print(f"  {g['group']:<20} n={g['total']:>7,}  sağlam=%{g['healthy_rate_pct']}")
    print("  Not: bildirimli araçlar bildirimsizden DAHA AZ toparlanıyorsa, bildirimler gerçek sinyal taşır.")


def _print_false_fault(d: dict) -> None:
    print(f"\n=== Sahte Arıza Özeti (toplam boşa görev: {d['total_wasted_missions']:,}) ===")
    for r in d["breakdown"]:
        print(
            f"  {r['verdict']:<22} {r['hypothesis']:<20} "
            f"olay={r['events']:>6,} araç={r['vehicles']:>6,} boşa_görev={r['wasted_missions']:>6,}"
        )
    if d["cost"] is None:
        print("  Maliyet: ops_cost_model boş → yalnız GÖREV SAYISI raporlanır (TL yok).")
    else:
        c = d["cost"]
        print(f"  Maliyet: ~{c['amount']:,} {c['currency']} ({c['wasted_missions']:,} boşa görev)")


def _print_vehicle_hotspots(d: dict) -> None:
    print(f"\n=== Araç Sıcak Noktaları (min {d['min_failures']} başarısızlık) ===")
    for v in d["vehicles"]:
        print(f"  {str(v.get('external_code') or v['vehicle_id']):<10} {v['failures']:>5,}")


def _print_subregion(d: dict) -> None:
    print(f"\n=== Alt Bölge Sıcak Noktaları (min {d['min_rides']:,} sürüş) ===")
    for s in d["sub_regions"]:
        print(
            f"  {s['city']:<18} #{s['sub_region_code']:<6} "
            f"başarısızlık=%{s['failure_rate_pct']:<5} sahte_alarm/1000={s['false_alarm_per_1000']}"
        )


def _print_hourly(d: dict) -> None:
    print("\n=== Saatlik Başarısızlık Oranı (yerel saat) ===")
    for b in d["buckets"]:
        print(f"  {b['city']:<18} {b['hour']:02d}:00  n={b['total']:>6,}  başarısız=%{b['failure_rate_pct']}")


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
