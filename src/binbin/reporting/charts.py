"""Matplotlib ile PNG grafik üretimi (imperative shell).

matplotlib yalnız bu modülde kullanılır (Plotly gelirse ayrı modül). Her `chart_scenario_*`
fonksiyonu `scenario_analysis.analyze_scenarios`'un ürettiği senaryo raporu dict'ini alır,
çizer, kaydeder ve dosya yolunu döner (tek/iki senaryoyu yan yana gösterir).

DataViz: colorblind-safe palet; büyüklük ölçen bar'larda tek hue (mavi). Başlık +
alt başlık ("ne gösteriyor") Türkçe; ham dict anahtarları sunumda Türkçeleştirilir.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # başsız (dosyaya) render; ekran gerektirmez
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from binbin.reporting.format import tr_dec as _tr_dec  # noqa: E402
from binbin.reporting.format import tr_int as _tr_int  # noqa: E402
from binbin.reporting.format import tr_pct as _tr_pct  # noqa: E402

# --- Palet (dataviz doğrulanmış referans, light) ---------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"       # primary metin
INK2 = "#52514e"      # secondary metin (değer etiketleri, alt başlık)
MUTED = "#898781"     # eksen/tick
GRID = "#e1e0d9"      # hairline ızgara
BASELINE = "#c3c2b7"  # spine
BLUE = "#2a78d6"      # kategorik slot-1 (ana ölçü hue'su)
BLUE_LIGHT = "#86b6ef"  # vurgu-dışı (de-emphasize)
AQUA = "#1baf7a"      # kategorik slot-2 (ikinci seri)
ORANGE = "#d97706"    # kategorik slot-3 (yalnız özel eşik)

# --- Ham anahtar → Türkçe okunur etiket (sunum katmanı) --------------------
_GROUP_LABELS = {
    "ariza_metinli": "Arıza metinli bildirim",
    "herhangi_bildirimli": "Herhangi bildirim",
    "bildirimsiz": "Bildirimsiz (kontrol grubu)",
}


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


# --- Ortak eksen/başlık yardımcıları ---------------------------------------
def _new_fig(width: float, height: float):
    fig, ax = plt.subplots(figsize=(width, height), layout="constrained")
    return fig, ax


def _header(fig, ax, title: str, subtitle: str) -> None:
    """Sol hizalı Türkçe başlık + tek satır açıklayıcı alt başlık (ne gösteriyor)."""
    fig.suptitle(title, x=0.012, ha="left", fontsize=15, fontweight="bold", color=INK)
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


# ---------------------------------------------------------------------------
# İki senaryolu karşılaştırma grafikleri
_SCENARIO_COLORS = [BLUE, AQUA]
_TOP_N = 15  # sıcak nokta grafiklerinde gösterilecek azami çubuk sayısı


def _scenarios(report: dict) -> list[dict]:
    return [report["scenarios"][key] for key in report["scenario_order"]]


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
    ax.bar_label(
        bars,
        labels=[f"{_tr_pct(rate)}\n{_tr_int(count)} sürüş" for rate, count in zip(rates, counts)],
        padding=5,
        color=INK2,
        fontsize=10,
    )
    ax.set_ylabel("Başarısızlık oranı (%)")
    ax.set_ylim(bottom=0)
    ax.margins(y=0.26)
    _style_axes(ax, value_axis="y")
    _header(
        fig, ax,
        "Genel Başarısızlık Karşılaştırması",
        "Mesafe kaynağı: mongo_distance_meters — oranlar değerlendirilebilir sürüşlere göredir",
    )
    return _save(fig, out_dir, "scenario_overview.png")


def chart_scenario_transitions(report: dict, out_dir: Path) -> Path:
    comparisons = report["comparisons"]
    if not comparisons:
        return _empty_chart(
            "Senaryolar Arası Geçişler", "Karşılaştırma için özel eşik verilmedi",
            out_dir, "scenario_transitions.png",
        )
    labels = [f"{c['from_label']}\n→ {c['to_label']}" for c in comparisons]
    lost = [c["failed_to_success"] for c in comparisons]
    gained = [c["success_to_failed"] for c in comparisons]
    excluded = [c["failed_to_unevaluated"] for c in comparisons]
    x = list(range(len(labels)))
    width = 0.24
    fig, ax = _new_fig(11, 5.8)
    left = ax.bar([v - width for v in x], lost, width, color=BLUE,
                  label="Başarısız → başarılı")
    right = ax.bar(x, gained, width, color=ORANGE,
                   label="Başarılı → başarısız")
    missing = ax.bar([v + width for v in x], excluded, width, color=MUTED,
                     label="Başarısız → değerlendirme dışı")
    ax.bar_label(left, labels=[_tr_int(v) for v in lost], padding=4, color=INK2, fontsize=9)
    ax.bar_label(right, labels=[_tr_int(v) for v in gained], padding=4, color=INK2, fontsize=9)
    ax.bar_label(missing, labels=[_tr_int(v) for v in excluded], padding=4, color=INK2, fontsize=9)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Sürüş sayısı")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _tr_int(value)))
    ax.margins(y=0.25)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="upper right")
    net = " · ".join(
        f"{c['to_label']}: {c['failed_count_delta']:+,} ({c['failure_rate_pp_delta']:+.1f} puan)"
        for c in comparisons
    ).replace(",", ".")
    _header(fig, ax, "Senaryolar Arası Durum Geçişleri", net)
    return _save(fig, out_dir, "scenario_transitions.png")


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
    ordered = sorted(keys, key=lambda key: max(v.get(key, 0) for v in values), reverse=True)
    x = list(range(len(ordered)))
    width = 0.75 / len(scenarios)
    fig, ax = _new_fig(10, 5.8)
    for idx, (scenario, mapping) in enumerate(zip(scenarios, values)):
        positions = [v - 0.375 + width / 2 + idx * width for v in x]
        counts = [mapping.get(key, 0) for key in ordered]
        bars = ax.bar(positions, counts, width, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_int(c) for c in counts], padding=3, fontsize=8, color=INK2,
                     rotation=90)
    ax.set_xticks(x, ordered)
    ax.set_ylabel("Başarısız sürüş sayısı")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _tr_int(value)))
    ax.margins(y=0.3)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="upper right")
    _header(fig, ax, "Neden Dağılımı", "Her senaryonun başarısız sürüşleri yeniden sınıflandırıldı")
    return _save(fig, out_dir, "scenario_causes.png")


def chart_scenario_control(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    group_keys = ["ariza_metinli", "herhangi_bildirimli", "bildirimsiz"]
    labels = [_GROUP_LABELS[key] for key in group_keys]
    x = list(range(len(labels)))
    width = 0.75 / len(scenarios)
    fig, ax = _new_fig(10, 5.5)
    for idx, scenario in enumerate(scenarios):
        mapping = {g["group"]: g for g in scenario["control"]["groups"]}
        rates = [mapping[key]["healthy_rate_pct"] for key in group_keys]
        positions = [v - 0.375 + width / 2 + idx * width for v in x]
        bars = ax.bar(positions, rates, width, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_pct(rate) for rate in rates], padding=3, fontsize=8, color=INK2)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Sonradan sağlam çıkma oranı (%)")
    ax.set_ylim(bottom=0)
    ax.margins(y=0.25)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="upper right")
    _header(fig, ax, "Kontrol Grubu Karşılaştırması", "Sağlamlık kanıtı her senaryoda yeniden hesaplandı")
    return _save(fig, out_dir, "scenario_control_group.png")


def chart_scenario_false_fault(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    keys = ["GECICI_TEKNIK", "REGULASYON"]
    labels = ["Geçici Teknik", "Regülasyon"]
    x = list(range(len(keys)))
    width = 0.75 / len(scenarios)
    fig, ax = _new_fig(9, 5.4)
    for idx, scenario in enumerate(scenarios):
        mapping = {r["key"]: r for r in scenario["false_fault"]["primary"]}
        events = [mapping[key]["events"] for key in keys]
        positions = [v - 0.375 + width / 2 + idx * width for v in x]
        bars = ax.bar(positions, events, width, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_int(v) for v in events], padding=3, fontsize=9, color=INK2)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Şüpheli olay sayısı")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: _tr_int(value)))
    ax.margins(y=0.24)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="upper right")
    _header(fig, ax, "Sahte Arıza Özeti", "Ana görünüm yalnız Geçici Teknik ve Regülasyon ayrımını gösterir")
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
    height = 0.75 / len(scenarios)
    fig, ax = _new_fig(10, max(5, 0.48 * len(selected) + 2))
    for idx, (scenario, mapping) in enumerate(zip(scenarios, mappings)):
        positions = [v - 0.375 + height / 2 + idx * height for v in y]
        counts = [mapping.get(key, 0) for key in selected]
        bars = ax.barh(positions, counts, height, color=_SCENARIO_COLORS[idx], label=scenario["label"])
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
    height = 0.75 / len(scenarios)
    fig, ax = _new_fig(11, max(5, 0.5 * len(selected) + 2))
    for idx, (scenario, mapping) in enumerate(zip(scenarios, mappings)):
        positions = [v - 0.375 + height / 2 + idx * height for v in y]
        density = [mapping.get(key, {}).get("false_alarm_per_1000", 0) for key in selected]
        bars = ax.barh(positions, density, height, color=_SCENARIO_COLORS[idx], label=scenario["label"])
        ax.bar_label(bars, labels=[_tr_dec(v) for v in density], padding=3, fontsize=8, color=INK2)
    ax.set_yticks(y, [f"{key[0]} · Bölge {key[1]}" for key in selected])
    ax.invert_yaxis()
    ax.set_xlabel("Geçici teknik + regülasyon şüphesi / 1000 sürüş")
    ax.margins(x=0.18)
    _style_axes(ax, value_axis="x")
    ax.legend(frameon=True, edgecolor=GRID, loc="lower right")
    _header(fig, ax, "Alt Bölge Sahte-Arıza Yoğunluğu", "Senaryolara göre en yoğun ilk 15 alt bölge")
    return _save(fig, out_dir, "scenario_subregions.png")


def chart_scenario_hourly(report: dict, out_dir: Path) -> Path:
    scenarios = _scenarios(report)
    cities = sorted({r["city"] for s in scenarios for r in s["hourly"]["buckets"]})
    if not cities:
        return _empty_chart("Saatlik Başarısızlık", "Gösterilecek saatlik veri yok",
                            out_dir, "scenario_hourly.png")
    fig, ax = _new_fig(11, 6)
    line_styles = ["-", "--", ":"]
    city_markers = ["o", "s", "^"]
    for city_idx, city in enumerate(cities):
        for scenario_idx, scenario in enumerate(scenarios):
            rows = sorted(
                (r for r in scenario["hourly"]["buckets"] if r["city"] == city),
                key=lambda r: r["hour"],
            )
            if not rows:
                continue
            ax.plot(
                [r["hour"] for r in rows],
                [r["failure_rate_pct"] for r in rows],
                color=_SCENARIO_COLORS[scenario_idx],
                linestyle=line_styles[scenario_idx],
                marker=city_markers[city_idx % len(city_markers)],
                markersize=4,
                linewidth=1.8,
                label=f"{city} · {scenario['label']}",
            )
    ax.set_xlabel("Yerel saat")
    ax.set_ylabel("Başarısızlık oranı (%)")
    ax.set_xlim(-0.5, 23.5)
    ax.set_xticks(range(0, 24, 2))
    ax.set_ylim(bottom=0)
    _style_axes(ax, value_axis="y")
    ax.legend(frameon=True, edgecolor=GRID, loc="lower center", ncol=max(1, len(scenarios)),
              fontsize=8, bbox_to_anchor=(0.5, -0.26))
    _header(fig, ax, "Saatlik Başarısızlık Oranı", "Renk senaryoyu, işaret biçimi şehri gösterir")
    return _save(fig, out_dir, "scenario_hourly.png")
