"""Matplotlib ile PNG grafik üretimi (imperative shell).

matplotlib yalnız bu modülde kullanılır ve projenin TEK grafik kütüphanesidir
(web/HTML rapor fikri terk edildi; çıktı terminal + PNG'dir). Her `chart_scenario_*`
fonksiyonu `scenario_analysis.analyze_scenarios`'un ürettiği senaryo raporu dict'ini alır,
çizer, kaydeder ve dosya yolunu döner (tek/iki senaryoyu yan yana gösterir).

DataViz: colorblind-safe palet; büyüklük ölçen bar'larda tek hue (mavi). Başlık +
alt başlık ("ne gösteriyor") Türkçe; ham dict anahtarları sunumda Türkçeleştirilir.
"""

from collections import defaultdict
from math import ceil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # başsız (dosyaya) render; ekran gerektirmez
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from binbin.reporting.format import fmt_threshold as _fmt_threshold  # noqa: E402
from binbin.reporting.format import tr_dec as _tr_dec  # noqa: E402
from binbin.reporting.format import tr_int as _tr_int  # noqa: E402
from binbin.reporting.format import GROUP_LABELS as _GROUP_LABELS  # noqa: E402
from binbin.reporting.format import tr_pct as _tr_pct  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"       # primary metin
INK2 = "#52514e"      # secondary metin (değer etiketleri, alt başlık)
MUTED = "#898781"     # eksen/tick
GRID = "#e1e0d9"      # hairline ızgara
BASELINE = "#c3c2b7"  # spine
BLUE = "#2a78d6"      # kategorik slot-1 (ana ölçü hue'su)
AQUA = "#189c6d"      # kategorik slot-2; beyaz etiket kontrastı için koyulaştırıldı (3,49:1)
ORANGE = "#d97706"    # kategorik slot-3; eşik grafiklerinde Senaryo A kimliği


def _apply_style() -> None:
    """Modül genel matplotlib stilini (bir kez) ayarlar."""
    matplotlib.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "text.color": INK,
            "axes.labelcolor": INK2,
            "axes.labelsize": 11,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "figure.dpi": 160,
        }
    )


_apply_style()


def _new_fig(width: float, height: float):
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
    return fig, ax


def _fig_title(fig, title: str, y: float | None = None) -> None:
    """Şekil başlığı — TEK yer. Kopyalanırsa PNG'ler birbirinden sessizce sapar
    (`format.py`/`GROUP_LABELS` ile aynı gerekçe)."""
    fig.suptitle(title, x=0.012, y=y, ha="left", fontsize=15, fontweight="bold", color=INK)


def _header(fig, ax, title: str, subtitle: str) -> None:
    """Sol hizalı Türkçe başlık + tek satır açıklayıcı alt başlık (ne gösteriyor)."""
    _fig_title(fig, title)
    ax.set_title(subtitle, loc="left", fontsize=10.5, color=INK2, pad=6)


def _style_axes(ax, value_axis: str) -> None:
    """Üst/sağ spine kaldır, hairline ızgara (yalnız değer ekseninde), tick'siz."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(length=0)
    ax.set_axisbelow(True)
    ax.grid(axis=value_axis, color=GRID, linewidth=0.8)


def _save(fig, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.savefig(path)
    plt.close(fig)
    return path


_SCENARIO_COLORS = [BLUE, AQUA]
_TOP_N = 15  # sıcak nokta grafiklerinde gösterilecek azami çubuk sayısı
# Saatlik grafikte bu kütlenin altındaki şehir-saat kovaları çizilmez (ör. İGA gibi çok
# düşük hacimli şehirler tek bir uç noktayla y-eksenini domine ediyordu — MIN_SUBREGION_RIDES
# desenine paralel, ölçüm istikrarı için asgari kütle şartı).
MIN_HOURLY_BUCKET_RIDES = 200


def _scenarios(report: dict) -> list[dict]:
    return [report["scenarios"][key] for key in report["scenario_order"]]


_GROUP_SPAN = 0.75  # bir kategori yuvasının çubuklara ayrılan payı (kalanı boşluk)


def _grouped_bars(ticks: list[int], scenario_count: int) -> tuple[float, list[list[float]]]:
    """Gruplu çubuk genişliği + senaryo başına merkez konumları.

    Beş grafik bu formülü ayrı ayrı yazıyordu; `_GROUP_SPAN` bir yerde değişince
    diğerlerinin sessizce sapması an meselesiydi.
    """
    size = _GROUP_SPAN / scenario_count
    offsets = [
        [tick - _GROUP_SPAN / 2 + size / 2 + idx * size for tick in ticks]
        for idx in range(scenario_count)
    ]
    return size, offsets


def _empty_chart(title: str, subtitle: str, out_dir: Path, name: str) -> Path:
    fig, ax = _new_fig(8, 4)
    ax.text(0.5, 0.5, "Gösterilecek veri yok", ha="center", va="center", color=INK2)
    ax.set_axis_off()
    _header(fig, ax, title, subtitle)
    return _save(fig, out_dir, name)


def chart_scenario_overview(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    labels = [s["label"] for s in scenarios]
    rates = [s["overview"]["failure_rate_pct"] for s in scenarios]
    counts = [s["overview"]["failed"] for s in scenarios]
    fig, ax = _new_fig(9, 5.4)
    bars = ax.bar(labels, rates, color=_SCENARIO_COLORS[: len(scenarios)], width=0.58)
    # label_type="center": çubuğun tepesine basılan etiket dar aralıklarda alt başlığa değiyordu.
    ax.bar_label(
        bars,
        labels=[f"{_tr_pct(rate)}\n{_tr_int(count)} sürüş" for rate, count in zip(rates, counts)],
        label_type="center",
        color="white",
        fontsize=10,
        fontweight="bold",
    )
    ax.set_ylabel("Başarısızlık oranı (%)")
    ax.set_ylim(bottom=0)
    ax.margins(y=0.12)
    _style_axes(ax, value_axis="y")
    _header(
        fig, ax,
        "Genel Başarısızlık Karşılaştırması",
        "Mesafe kaynağı: mongo_distance_meters — oranlar değerlendirilebilir sürüşlere göredir",
    )
    return _save(fig, out_dir, "scenario_overview.png")


def chart_scenario_causes(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    values = []
    keys = set()
    for scenario in scenarios:
        cause = scenario["cause"]
        mapping = {r["category"]: r["count"] for r in cause["categories"]}
        mapping["SİNYALSİZ"] = cause["signalless"]["count"]
        values.append(mapping)
        keys.update(mapping)
    if not keys:
        return _empty_chart("Neden Dağılımı", "Senaryolara göre başarısızlık nedenleri",
                            out_dir, "scenario_causes.png")
    # SİNYALSİZ kütlesinin asıl bulgusu (rapor §9.2): büyük çoğunluğu bildirimsiz,
    # yalnız küçük bir dilimi bildirimli ama kategori atanamayan. Bu ayrım
    # category_matrix'in SINYALSIZ satırından geliyor (reported / no_report).
    reported_by_scenario = []
    for scenario in scenarios:
        row = next(
            (r for r in scenario["category_matrix"]["rows"] if r["category"] == "SINYALSIZ"),
            None,
        )
        reported_by_scenario.append(row["reported"] if row else 0)
    ordered = sorted(keys, key=lambda key: max(v.get(key, 0) for v in values), reverse=True)
    x = list(range(len(ordered)))
    width, offsets = _grouped_bars(x, len(scenarios))
    fig, ax = _new_fig(10, 5.8)
    for idx, (scenario, mapping) in enumerate(zip(scenarios, values)):
        positions = offsets[idx]
        counts = [mapping.get(key, 0) for key in ordered]
        reported = reported_by_scenario[idx]
        no_report = [c - reported if key == "SİNYALSİZ" else c for key, c in zip(ordered, counts)]
        bars = ax.bar(positions, no_report, width, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        sig_pos = positions[ordered.index("SİNYALSİZ")]
        ax.bar(
            sig_pos, reported, width, bottom=counts[ordered.index("SİNYALSİZ")] - reported,
            color=_SCENARIO_COLORS[idx], edgecolor=SURFACE, hatch="////",
            label="— bildirimli ama kategorisiz" if idx == 0 else None,
        )
        ax.bar_label(bars, labels=[_tr_int(c) for c in counts], padding=3, fontsize=8, color=INK2,
                     rotation=90)
    ax.set_xticks(x, ordered)
    ax.set_ylabel("Başarısız sürüş sayısı")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _tr_int(value)))
    ax.margins(y=0.3)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="upper right")
    _header(fig, ax, "Neden Dağılımı",
            "SİNYALSİZ çubuğunda taralı üst dilim: bildirim var ama kategori atanamadı")
    return _save(fig, out_dir, "scenario_causes.png")


def chart_scenario_control(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    group_keys = ["ariza_metinli", "herhangi_bildirimli", "bildirimsiz"]
    labels = [_GROUP_LABELS[key] for key in group_keys]
    x = list(range(len(labels)))
    width, offsets = _grouped_bars(x, len(scenarios))
    fig, ax = _new_fig(10, 5.5)
    for idx, scenario in enumerate(scenarios):
        mapping = {g["group"]: g for g in scenario["control"]["groups"]}
        rates = [mapping[key]["healthy_rate_pct"] for key in group_keys]
        bars = ax.bar(offsets[idx], rates, width, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_pct(rate) for rate in rates], padding=3, fontsize=8, color=INK2)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Sonradan sağlam çıkma oranı (%)")
    ax.set_ylim(bottom=0)
    ax.margins(y=0.25)
    _style_axes(ax, value_axis="y")
    # loc="upper right" değer etiketiyle çakışıyordu (bildirimsiz grup en yüksek bar,
    # sağda) — sol grup her zaman daha kısa olduğundan legend'ı oraya taşımak güvenli.
    ax.legend(frameon=True, edgecolor=GRID, loc="upper left")
    _header(fig, ax, "Kontrol Grubu Karşılaştırması", "Sağlamlık kanıtı her senaryoda yeniden hesaplandı")
    return _save(fig, out_dir, "scenario_control_group.png")


def chart_scenario_false_fault(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    keys = ["GECICI_TEKNIK", "REGULASYON"]
    labels = ["Geçici Teknik", "Regülasyon"]
    x = list(range(len(keys)))
    width, offsets = _grouped_bars(x, len(scenarios))
    fig, ax = _new_fig(9, 5.4)
    for idx, scenario in enumerate(scenarios):
        mapping = {r["key"]: r for r in scenario["false_fault"]["primary"]}
        events = [mapping[key]["events"] for key in keys]
        bars = ax.bar(offsets[idx], events, width, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_int(v) for v in events], padding=3, fontsize=9, color=INK2)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Şüpheli olay sayısı")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _tr_int(value)))
    ax.margins(y=0.24)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="upper right")
    _header(fig, ax, "Sahte Alarm Şüphesi Özeti", "Ana görünüm yalnız Geçici Teknik ve Regülasyon ayrımını gösterir")
    return _save(fig, out_dir, "scenario_false_fault.png")


def chart_scenario_vehicles(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    mappings = []
    labels = {}
    keys = set()
    for scenario in scenarios:
        mapping = {}
        for row in scenario["vehicle"]["vehicles"]:
            mapping[row["vehicle_id"]] = row["failures"]
            labels[row["vehicle_id"]] = str(row.get("external_code") or row["vehicle_id"])
            keys.add(row["vehicle_id"])
        mappings.append(mapping)
    selected = sorted(keys, key=lambda key: max(m.get(key, 0) for m in mappings), reverse=True)[:_TOP_N]
    if not selected:
        return _empty_chart("Araç Sıcak Noktaları", "Gösterilecek araç yok",
                            out_dir, "scenario_vehicles.png")
    y = list(range(len(selected)))
    height, offsets = _grouped_bars(y, len(scenarios))
    fig, ax = _new_fig(10, max(5, 0.48 * len(selected) + 2))
    for idx, (scenario, mapping) in enumerate(zip(scenarios, mappings)):
        counts = [mapping.get(key, 0) for key in selected]
        bars = ax.barh(offsets[idx], counts, height, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_int(v) for v in counts], padding=3, fontsize=8, color=INK2)
    ax.set_yticks(y, [labels[key] for key in selected])
    ax.invert_yaxis()
    ax.set_xlabel("Başarısız sürüş sayısı")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: _tr_int(value)))
    ax.margins(x=0.18)
    _style_axes(ax, value_axis="x")
    ax.legend(frameon=True, edgecolor=GRID, loc="lower right")
    _header(fig, ax, "Araç Sıcak Noktaları", "Senaryolardan birinde en yüksek değere sahip ilk 15 araç")
    return _save(fig, out_dir, "scenario_vehicles.png")


def chart_scenario_subregions(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    mappings = []
    keys = set()
    for scenario in scenarios:
        mapping = {(r["city"], r["sub_region_code"]): r for r in scenario["subregion"]["sub_regions"]}
        mappings.append(mapping)
        keys.update(mapping)
    selected = sorted(
        keys,
        key=lambda key: max(m.get(key, {}).get("false_alarm_per_1000", 0) for m in mappings),
        reverse=True,
    )[:_TOP_N]
    if not selected:
        return _empty_chart("Alt Bölge Sıcak Noktaları", "En az 2.000 sürüşlü bölge yok",
                            out_dir, "scenario_subregions.png")
    y = list(range(len(selected)))
    height, offsets = _grouped_bars(y, len(scenarios))
    fig, ax = _new_fig(11, max(5, 0.5 * len(selected) + 2))
    for idx, (scenario, mapping) in enumerate(zip(scenarios, mappings)):
        density = [mapping.get(key, {}).get("false_alarm_per_1000", 0) for key in selected]
        bars = ax.barh(offsets[idx], density, height, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_dec(v) for v in density], padding=3, fontsize=8, color=INK2)
    n_by_key = {
        key: next(m[key]["total_rides"] for m in mappings if key in m) for key in selected
    }
    ax.set_yticks(
        y, [f"{key[0]} · Bölge {key[1]} (n={_tr_int(n_by_key[key])})" for key in selected]
    )
    ax.invert_yaxis()
    ax.set_xlabel("Geçici teknik + regülasyon şüphesi / 1000 sürüş")
    ax.margins(x=0.18)
    _style_axes(ax, value_axis="x")
    ax.legend(frameon=True, edgecolor=GRID, loc="lower right")
    _header(fig, ax, "Alt Bölge Sahte Alarm Şüphesi Yoğunluğu", "Senaryolara göre en yoğun ilk 15 alt bölge; n = alt bölgenin toplam sürüş sayısı")
    return _save(fig, out_dir, "scenario_subregions.png")


_MIN_HOURLY_CITY_POINTS = 6  # bu kadar saatlik nokta kalmayan şehir paneli hiç çizilmez


def chart_scenario_hourly(report: dict, out_dir: Path) -> Path:
    """Küçük çoklular (small multiples): tek eksende 20+ şehir × senaryo çizgisi
    birbirine giriyordu (bkz. teslim öncesi QA). Her şehir kendi panelinde, ortak
    ölçekte (sharey) çizilir; tek legend figürün altında.
    """
    scenarios = _scenarios(report)
    buckets_by_scenario = [
        {(r["city"], r["hour"]): r for r in s["hourly"]["buckets"]} for s in scenarios
    ]
    city_totals: dict[str, int] = defaultdict(int)
    for r in scenarios[0]["hourly"]["buckets"]:
        city_totals[r["city"]] += r["total"]
    ordered_cities = sorted(city_totals, key=lambda c: city_totals[c], reverse=True)
    if not ordered_cities:
        return _empty_chart("Saatlik Başarısızlık Oranı", "Gösterilecek saatlik veri yok",
                            out_dir, "scenario_hourly.png")

    # MIN_HOURLY_BUCKET_RIDES altındaki (düşük hacim → gürültülü oran) saat kovaları
    # çizgiden düşürülür; kalan nokta azsa şehir paneli tamamen atlanır.
    panels: list[tuple[str, list[list[dict]]]] = []
    dropped: list[str] = []
    for city in ordered_cities:
        series_per_scenario = []
        max_points = 0
        for buckets in buckets_by_scenario:
            rows = sorted(
                (r for (c, _h), r in buckets.items() if c == city and r["total"] >= MIN_HOURLY_BUCKET_RIDES),
                key=lambda r: r["hour"],
            )
            series_per_scenario.append(rows)
            max_points = max(max_points, len(rows))
        if max_points < _MIN_HOURLY_CITY_POINTS:
            dropped.append(city)
            continue
        panels.append((city, series_per_scenario))

    if not panels:
        return _empty_chart(
            "Saatlik Başarısızlık Oranı",
            f"Hiçbir şehirde {MIN_HOURLY_BUCKET_RIDES} sürüş/saat kovası şartını karşılayan yeterli nokta yok",
            out_dir, "scenario_hourly.png",
        )

    # Panel sayısı 4'ten azsa ızgara da daralır. Sabit 4 sütun bırakılırsa (ör. yalnız
    # 2 şehir çizilebildiğinde) figürün sağ yarısı boş kalıyor ve PNG "kırpılmış" gibi
    # görünüyordu — genişlik de panel sayısıyla ölçeklenir.
    ncols = min(4, len(panels))
    nrows = ceil(len(panels) / ncols)
    # layout="constrained" KULLANILMIYOR: fig.text ile eklenen başlık/legend'ı hesaba
    # katmadığı için PNG'nin üstünü kırpıyordu — subplots_adjust ile elle pay ayrılır.
    fig, axes = plt.subplots(
        nrows, ncols,
        figsize=(max(5.5, 2.75 * ncols), max(5.5, 2.15 * nrows)),  # 4 sütunda 11 inç (eski davranış)
        sharex=True, sharey=True,
    )
    fig.subplots_adjust(top=0.85, bottom=0.10, hspace=0.55, wspace=0.22)
    axes_flat = list(axes.flat) if len(panels) > 1 else [axes]
    # Gösterge TÜM panellerden toplanır: yalnız ilk panele bakmak, orada hacim şartına
    # takılıp başka panelde çizilen senaryoyu göstergeden düşürüyordu.
    legend_handles: dict[int, object] = {}
    for idx, (city, series_per_scenario) in enumerate(panels):
        ax = axes_flat[idx]
        for scenario_idx, (scenario, rows) in enumerate(zip(scenarios, series_per_scenario)):
            if not rows:
                continue
            line, = ax.plot(
                [r["hour"] for r in rows],
                [r["failure_rate_pct"] for r in rows],
                color=_SCENARIO_COLORS[scenario_idx],
                marker="o",
                markersize=2.4,
                linewidth=1.3,
                label=scenario["label"],
            )
            legend_handles.setdefault(scenario_idx, line)
        ax.set_title(f"{city} · {_tr_int(city_totals[city])} sürüş", fontsize=8.3, color=INK2, pad=2)
        ax.set_xlim(-0.5, 23.5)
        ax.set_xticks(range(0, 24, 6))
        _style_axes(ax, value_axis="y")
    for j in range(len(panels), len(axes_flat)):
        axes_flat[j].set_axis_off()

    drawn = sorted(legend_handles)
    handles = [legend_handles[i] for i in drawn]
    plot_labels = [scenarios[i]["label"] for i in drawn]
    fig.legend(
        handles, plot_labels, loc="upper right", ncol=max(1, len(scenarios)),
        frameon=True, edgecolor=GRID, fontsize=9, bbox_to_anchor=(0.99, 0.985),
    )
    fig.text(0.5, 0.015, "Yerel saat", ha="center", fontsize=10, color=INK2)
    fig.text(0.008, 0.5, "Başarısızlık oranı (%)", va="center", rotation="vertical",
             fontsize=10, color=INK2)

    subtitle = "Her panel bir şehir; ortak dikey ölçek"
    if dropped:
        subtitle += f" — {len(dropped)} şehir düşük hacim nedeniyle gösterilmedi ({', '.join(dropped)})"
    fig.suptitle("Saatlik Başarısızlık Oranı", x=0.012, y=0.985, ha="left",
                 fontsize=15, fontweight="bold", color=INK)
    fig.text(0.012, 0.925, subtitle, ha="left", fontsize=9.5, color=INK2)
    return _save(fig, out_dir, "scenario_hourly.png")


def chart_threshold_scan(scan: dict, out_dir: Path) -> Path:
    """Eşik Taraması — her mesafe eşiği bir çizgi, x süre eşiği, y F1 (isabet).

    Boşa görev/kapsam gibi diğer sütunlar bu grafikte YOK — F1 tek başına karar
    değişkenidir (bkz. core/threshold_scan modül dokstring'i); ayrıntı yalnız tablo
    ve rapor metninde.
    """
    rows = scan["rows"]
    durations = scan["duration_grid"]
    distances = scan["distance_grid"]
    by_cell = {(r["duration_threshold"], r["distance_threshold"]): r for r in rows}
    cmap = matplotlib.colormaps["Blues"]
    colors = [
        cmap(0.35 + 0.55 * i / max(1, len(distances) - 1)) for i in range(len(distances))
    ]
    fig, ax = _new_fig(9, 5.6)
    for idx, dist in enumerate(distances):
        series = [by_cell[(d, dist)] for d in durations]
        ax.plot(
            durations,
            [r["f1_pct"] for r in series],
            color=colors[idx],
            marker="o",
            markersize=4,
            linewidth=1.8,
            label=f"mesafe < {_tr_dec(dist, 0)} m",
        )
    baseline = scan["baseline"]
    ax.axvline(baseline["duration"], color=MUTED, linestyle="--", linewidth=1)
    rec = scan["recommended"]
    ax.scatter(
        [rec["duration_threshold"]], [rec["f1_pct"]],
        s=150, marker="*", color=ORANGE, zorder=5, label="Senaryo A — isabet/kapsam",
    )
    cons = scan["conservative"]
    if cons is not scan["baseline_row"]:
        ax.scatter(
            [cons["duration_threshold"]], [cons["f1_pct"]],
            s=110, marker="D", color=AQUA, zorder=5, label="Senaryo B — boşa görev azaltma",
        )
    ax.set_xlabel("Süre eşiği (sn)")
    ax.set_ylabel("F1 (%)")
    ax.set_xticks(durations)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="best", fontsize=8.5)
    _header(
        fig, ax,
        "Eşik Taraması — Özel Kural İsabeti (F1)",
        f"Mevcut Kural {_tr_dec(baseline['duration'], 0)} sn / "
        f"{_tr_dec(baseline['distance'], 0)} m referans (kesikli çizgi)",
    )
    return _save(fig, out_dir, "threshold_scan.png")


# Senaryo kimliği threshold_scan.png ile aynı: A turuncu (yıldız), B yeşil (elmas).
_TRADEOFF_COLORS = (BLUE, ORANGE, AQUA)
_TRADEOFF_PANELS = (
    ("İsabet", "precision_pct", True),
    ("Kapsam", "recall_pct", True),
    ("İşaretlenen sürüş", "flagged", False),
    ("Boşa görev", "wasted_missions", False),
)


def chart_threshold_tradeoff(scan: dict, out_dir: Path) -> Path:
    """Mevcut Kural / Senaryo A / Senaryo B — dört ölçüde yan yana karşılaştırma.

    Küçük çoklu, tek panelde gruplu çubuk DEĞİL: iki ölçü yüzde, ikisi adet; tek
    eksene bindirmek çift-eksen grafiği olur ve iki büyüklüğü kıyaslanabilir
    gösterip yanıltırdı. Eksenler sıfırdan başlar — isabet panelinin düz
    görünmesi bulgunun kendisidir, gizlenecek bir kusur değil.
    """
    rows = (scan["baseline_row"], scan["recommended"], scan["conservative"])
    x = range(len(rows))
    labels = [
        f"{name}\n{_fmt_threshold(r['duration_threshold'])} sn / "
        f"{_fmt_threshold(r['distance_threshold'])} m"
        for name, r in zip(("Mevcut Kural", "Senaryo A", "Senaryo B"), rows)
    ]

    fig, axes = plt.subplots(2, 2, figsize=(9, 6.2), layout="constrained")
    for ax, (title, key, is_pct) in zip(axes.flat, _TRADEOFF_PANELS):
        values = [r[key] for r in rows]
        bars = ax.bar(x, values, color=_TRADEOFF_COLORS, width=0.6, zorder=3)
        ax.bar_label(
            bars,
            labels=[_tr_pct(v) if is_pct else _tr_int(v) for v in values],
            padding=4, color=INK, fontsize=9.5, fontweight="bold",
        )
        ax.set_title(title, loc="left", fontsize=11, color=INK, fontweight="bold", pad=4)
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, fontsize=8.2, color=MUTED)
        ax.set_ylim(0, (max(values) or 1) * 1.24)
        ax.set_yticklabels([])
        _style_axes(ax, value_axis="y")

    _fig_title(fig, "Senaryo Karşılaştırması — Mevcut Kural / A / B")
    precisions = [r["precision_pct"] for r in rows]
    spread = max(precisions) - min(precisions)
    fig.supxlabel(
        f"İsabet üç senaryoda {_tr_pct(min(precisions))}–{_tr_pct(max(precisions))} "
        f"arasında ({_tr_dec(spread, 1)} puan fark); değişen kapsam ve saha yükü.",
        x=0.012, ha="left", fontsize=9.5, color=INK2,
    )
    return _save(fig, out_dir, "scenario_tradeoff.png")
