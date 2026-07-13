"""Matplotlib ile PNG grafik üretimi (imperative shell).

matplotlib yalnız bu modülde kullanılır (Plotly gelirse ayrı modül). Her fonksiyon
core/analysis.py'dan dict alır, çizer, kaydeder ve dosya yolunu döner.

DataViz: colorblind-safe palet; büyüklük ölçen bar'larda tek hue (mavi). Başlık +
alt başlık ("ne gösteriyor") Türkçe; ham dict anahtarları sunumda Türkçeleştirilir.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # başsız (dosyaya) render; ekran gerektirmez
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

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


# --- Türkçe sayı biçimi -----------------------------------------------------
def _tr_int(v) -> str:
    """12345 → '12.345' (Türkçe binlik ayracı)."""
    return f"{int(round(v)):,}".replace(",", ".")


def _tr_pct(v) -> str:
    """91.5 → '%91,5'."""
    return f"%{v:.1f}".replace(".", ",")


def _tr_dec(v, n: int = 2) -> str:
    """6.06 → '6,06'."""
    return f"{v:.{n}f}".replace(".", ",")


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
def chart_cause_distribution(data: dict, out_dir: Path) -> Path:
    """Başarısızlık nedeni dağılımı — YATAY ÇUBUK (pasta değil)."""
    rows = [
        (c["category"], c["count"], c["pct"], False) for c in data["categories"]
    ]
    sig = data["signalless"]
    if sig["count"]:
        rows.append(("SİNYALSİZ", sig["count"], sig["pct"], True))
    rows.sort(key=lambda r: r[1])  # barh: en büyük en üstte olsun diye artan

    labels = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    colors = [MUTED if r[3] else BLUE for r in rows]
    bar_labels = [f"{_tr_int(r[1])} · {_tr_pct(r[2])}" for r in rows]

    n = len(labels)
    fig, ax = _new_fig(9, max(3.8, 0.72 * n + 1.8))
    bars = ax.barh(labels, counts, color=colors, height=0.68)
    ax.bar_label(bars, labels=bar_labels, padding=5, color=INK2, fontsize=10)
    ax.set_xlabel("Başarısız sürüş sayısı")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: _tr_int(x)))
    ax.margins(x=0.22)
    _style_axes(ax, value_axis="x")
    _header(
        fig, ax,
        "Başarısız Sürüşlerin Neden Dağılımı",
        f"Başarısızların {_tr_pct(sig['pct'])}'i sinyalsiz (gri) — kalanlar kategoriye ayrıldı",
    )
    return _save(fig, out_dir, "cause_distribution.png")


def chart_control_group(data: dict, out_dir: Path) -> Path:
    """Üç grubun 'sonradan sağlam çıkma' oranı — kontrol grubu karşılaştırması."""
    groups = data["groups"]
    labels = [_GROUP_LABELS.get(g["group"], g["group"]) for g in groups]
    rates = [g["healthy_rate_pct"] for g in groups]
    # Kontrol grubu (bildirimsiz) koyu blue ile vurgulanır; bildirimliler açık blue.
    colors = [BLUE if g["group"] == "bildirimsiz" else BLUE_LIGHT for g in groups]

    fig, ax = _new_fig(8.5, 5.2)
    bars = ax.bar(labels, rates, color=colors, width=0.56)
    ax.bar_label(bars, labels=[_tr_pct(r) for r in rates], padding=4, color=INK2, fontsize=11)
    ax.set_ylabel("Sonradan sağlam çıkma oranı (%)")
    ax.set_ylim(bottom=0)
    ax.margins(y=0.22)
    _style_axes(ax, value_axis="y")
    _header(
        fig, ax,
        "Kontrol Grubu Karşılaştırması",
        "Bildirimli araçlar, bildirimsiz kontrol grubundan DAHA AZ toparlanıyor "
        "→ bildirimler gerçek sinyal taşıyor",
    )
    return _save(fig, out_dir, "control_group.png")


def _thr_txt(scenario: dict) -> str:
    """Eşik etiketi: '120sn / 60m' (tam sayı ondalıksız)."""
    def n(v):
        return str(int(v)) if float(v).is_integer() else str(v).replace(".", ",")
    return f"{n(scenario['duration_threshold'])}sn / {n(scenario['distance_threshold'])}m"


def chart_criteria_whatif(data: dict, out_dir: Path) -> Path:
    """Gerçek eşik vs what-if eşik — DOĞRULAMA ORANI gruplu çubuk + delta alt başlıkta."""
    real, what, delta = data["real"], data["whatif"], data["delta"]
    r_pct = real["failed_meeting_criterion"]["pct_of_failed"]
    w_pct = what["failed_meeting_criterion"]["pct_of_failed"]
    labels = [f"Gerçek\n{_thr_txt(real)}", f"What-if\n{_thr_txt(what)}"]
    rates = [r_pct, w_pct]
    # Gerçek koyu blue (referans), what-if aqua (ikinci seri).
    colors = [BLUE, AQUA]

    fig, ax = _new_fig(8, 5.2)
    bars = ax.bar(labels, rates, color=colors, width=0.5)
    ax.bar_label(bars, labels=[_tr_pct(r) for r in rates], padding=4, color=INK2, fontsize=11)
    ax.set_ylabel("Doğrulama oranı (%)")
    ax.set_ylim(bottom=0)
    ax.margins(y=0.22)
    _style_axes(ax, value_axis="y")
    pp = f"{delta['confirm_pct_points']:+.1f}".replace(".", ",")
    rel = f"{delta['rel_pct']:+.1f}".replace(".", ",")
    _header(
        fig, ax,
        "Başarısızlık Eşiği: Gerçek vs What-if",
        f"Kaydedilmiş başarısızların eşiğe uyma oranı — what-if ile {pp} puan "
        f"({rel}% göreli) değişim",
    )
    return _save(fig, out_dir, "criteria_whatif.png")


def chart_vehicle_hotspots(data: dict, out_dir: Path) -> Path:
    """En çok başarısızlık üreten araçlar — YATAY ÇUBUK."""
    vehicles = sorted(data["vehicles"], key=lambda v: v["failures"])  # artan (en büyük üstte)
    labels = [str(v.get("external_code") or v["vehicle_id"]) for v in vehicles]
    failures = [v["failures"] for v in vehicles]

    fig, ax = _new_fig(9, max(3.5, 0.5 * len(labels) + 1.8))
    bars = ax.barh(labels, failures, color=BLUE, height=0.66)
    ax.bar_label(bars, labels=[_tr_int(f) for f in failures], padding=5, color=INK2, fontsize=10)
    ax.set_xlabel("Başarısızlık sayısı")
    ax.margins(x=0.16)
    _style_axes(ax, value_axis="x")
    _header(
        fig, ax,
        "En Çok Başarısızlık Üreten Araçlar",
        f"En az {data['min_failures']} başarısızlık üreten araçlar (plaka koduna göre)",
    )
    return _save(fig, out_dir, "vehicle_hotspots.png")


def chart_subregion_false_fault(data: dict, out_dir: Path) -> Path:
    """Alt bölge sahte-alarm yoğunluğu — YATAY ÇUBUK (1000 sürüş başına)."""
    subs = sorted(data["sub_regions"], key=lambda s: s["false_alarm_per_1000"])
    labels = [f"{s['city']} · Bölge {s['sub_region_code']}" for s in subs]
    density = [s["false_alarm_per_1000"] for s in subs]

    fig, ax = _new_fig(9.5, max(3.5, 0.6 * len(labels) + 1.8))
    bars = ax.barh(labels, density, color=BLUE, height=0.6)
    ax.bar_label(bars, labels=[_tr_dec(d) for d in density], padding=5, color=INK2, fontsize=10)
    ax.set_xlabel("Sahte alarm şüphesi / 1000 sürüş")
    ax.margins(x=0.18)
    _style_axes(ax, value_axis="x")
    _header(
        fig, ax,
        "Alt Bölge Sahte-Alarm Yoğunluğu",
        "1000 sürüş başına sahte-alarm şüphesi — regülasyon hipotezinin mekânsal izi",
    )
    return _save(fig, out_dir, "subregion_false_fault.png")


def chart_hourly_failure_rate(data: dict, out_dir: Path) -> Path:
    """Yerel saate göre başarısızlık oranı — ÇİZGİ, şehir başına seri."""
    buckets = data["buckets"]
    cities: dict[str, list[tuple[int, float]]] = {}
    for b in buckets:
        cities.setdefault(b["city"], []).append((b["hour"], b["failure_rate_pct"]))

    series_colors = [BLUE, AQUA]
    fig, ax = _new_fig(10, 5.2)
    for idx, (city, points) in enumerate(sorted(cities.items())):
        points.sort()
        hours = [p[0] for p in points]
        rates = [p[1] for p in points]
        color = series_colors[idx % len(series_colors)]
        ax.plot(hours, rates, color=color, linewidth=2, marker="o", markersize=8,
                label=city, zorder=3)
        # Çizgi ucunda şehir adı (INK — düşük kontrastlı hue'ya bağımlı kalınmaz)
        ax.annotate(
            city, xy=(hours[-1], rates[-1]), xytext=(6, 0), textcoords="offset points",
            va="center", ha="left", fontsize=9.5, color=INK, fontweight="bold",
            annotation_clip=False,
        )
    ax.set_xlabel("Yerel saat")
    ax.set_ylabel("Başarısızlık oranı (%)")
    ax.set_xlim(-0.5, 24.5)
    ax.set_xticks(range(0, 24, 2))
    ax.set_ylim(bottom=0)
    _style_axes(ax, value_axis="y")
    # 2+ seri için legend her zaman (kimlik renge bağımlı kalmasın)
    ax.legend(
        frameon=True, fancybox=False, edgecolor=GRID,
        loc="lower center", ncol=len(cities), fontsize=10, labelcolor=INK2,
        bbox_to_anchor=(0.5, -0.18),
    )
    _header(
        fig, ax,
        "Saatlik Başarısızlık Oranı (yerel saat)",
        "Şehir bazında gün içi seyir — çizgi ucundaki ad ilgili şehri gösterir",
    )
    return _save(fig, out_dir, "hourly_failure_rate.png")
