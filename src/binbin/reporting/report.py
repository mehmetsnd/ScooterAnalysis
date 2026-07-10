"""Raporlama — analiz çıktısından Plotly tabanlı HTML rapor üretimi (imperative shell)."""

from pathlib import Path


def build_html_report(analysis: dict, out_path: Path) -> Path:
    """Analiz sonuçlarından Plotly grafikli tek dosyalık HTML rapor üretir.

    analysis: core/analysis.py fonksiyonlarının döndürdüğü dict'lerin birleşimi.
    Dönüş: yazılan dosyanın yolu (out_path).
    """
    raise NotImplementedError
