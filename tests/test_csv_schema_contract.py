"""CSV şema sözleşmesi: başlığın tamamı doğrulanır, ilk sütunu değil."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from binbin.data.ingest import (
    RIDES_COLUMNS,
    STATUS_COLUMNS,
    IngestReport,
    SchemaContractError,
    detect_csv_kind,
    validate_csv_header,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_header(tmp_path, name, header: str):
    p = tmp_path / name
    p.write_text(header + "\n", encoding="utf-8")
    return p


def _write(tmp_path, name, columns):
    return _write_header(tmp_path, name, ",".join(columns))


@pytest.mark.parametrize(
    "kind,columns", [("rides", RIDES_COLUMNS), ("status", STATUS_COLUMNS)]
)
def test_birebir_ayni_baslik_gecerlidir(tmp_path, kind, columns):
    validate_csv_header(_write(tmp_path, "f.csv", columns), kind)


def test_bom_ve_bosluk_baslikta_tolere_edilir(tmp_path):
    p = _write_header(tmp_path, "r.csv", "﻿" + ", ".join(RIDES_COLUMNS))
    validate_csv_header(p, "rides")


def test_bom_tur_tespitini_de_bozmaz(tmp_path):
    """`cmd_ingest` önce tür tespiti çağırır; orada patlarsa doğrulayıcıya hiç ulaşmaz."""
    p = _write_header(tmp_path, "r.csv", "﻿" + ",".join(RIDES_COLUMNS))
    assert detect_csv_kind(p) == "rides"


def test_tirnakli_baslik_kabul_edilir(tmp_path):
    p = _write_header(tmp_path, "r.csv", ",".join(f'"{c}"' for c in RIDES_COLUMNS))
    validate_csv_header(p, "rides")


def test_kolon_sirasi_degisirse_hata(tmp_path):
    """En tehlikeli senaryo: tür tespiti geçer ama veri kayar."""
    cols = list(RIDES_COLUMNS)
    cols[2], cols[3] = cols[3], cols[2]
    with pytest.raises(SchemaContractError, match="3."):
        validate_csv_header(_write(tmp_path, "r.csv", cols), "rides")


def test_beklenen_kolon_eksikse_hata(tmp_path):
    cols = [c for c in RIDES_COLUMNS if c != "mongo_distance_meters"]
    with pytest.raises(SchemaContractError, match="mongo_distance_meters"):
        validate_csv_header(_write(tmp_path, "r.csv", cols), "rides")


def test_yeni_kolonlar_adlariyla_hata_verir(tmp_path):
    """Uyarı olamaz: COPY konumsal, fazladan sütunu zaten reddeder."""
    cols = list(RIDES_COLUMNS) + ["unlock_ack", "motor_error_code"]
    with pytest.raises(SchemaContractError) as e:
        validate_csv_header(_write(tmp_path, "r.csv", cols), "rides")
    assert "unlock_ack" in str(e.value) and "motor_error_code" in str(e.value)


def test_sondaki_virgul_fazla_sutun_sayilir(tmp_path):
    p = _write_header(tmp_path, "r.csv", ",".join(RIDES_COLUMNS) + ",")
    with pytest.raises(SchemaContractError, match="adsız"):
        validate_csv_header(p, "rides")


def test_bos_dosya_sebebi_soyleyen_hata_verir(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(SchemaContractError, match="boş"):
        validate_csv_header(p, "rides")


def test_bozuk_csv_sema_hatasina_cevrilir(tmp_path):
    """`csv.Error` `ValueError` değildir; sarmalanmazsa CLI'ı atlayıp traceback verir."""
    p = _write_header(tmp_path, "r.csv", 'rental_id,"user_id,' + "x" * 200_000)
    with pytest.raises(SchemaContractError, match="okunamadı"):
        validate_csv_header(p, "rides")


def test_bilinmeyen_tur_sema_hatasi_verir(tmp_path):
    p = _write(tmp_path, "r.csv", RIDES_COLUMNS)
    with pytest.raises(SchemaContractError):
        validate_csv_header(p, "bilinmeyen")


def _ingest_args(tmp_path, **over):
    defaults = dict(data_dir=tmp_path, file=None, force=False,
                    country=None, city=None, all=True)
    defaults.update(over)
    return SimpleNamespace(**defaults)


def _patch_ingest(monkeypatch):
    from binbin.data.ingest_status import StatusIngestReport

    calls = []

    def fake(report_cls):
        def run(*a, **k):
            calls.append(a)
            return report_cls(data_load_id=1, file_name="x.csv", status="SUCCESS")

        return run

    monkeypatch.setattr("binbin.data.ingest.run_ingest", fake(IngestReport))
    monkeypatch.setattr(
        "binbin.data.ingest_status.run_status_ingest", fake(StatusIngestReport)
    )
    return calls


def test_cli_sema_ihlalinde_hicbir_yukleme_baslatmaz(tmp_path, monkeypatch):
    from binbin.cli import main as cli

    calls = _patch_ingest(monkeypatch)
    cols = list(RIDES_COLUMNS)
    cols[2] = "beklenmeyen"
    _write(tmp_path, "a_rides.csv", cols)
    with pytest.raises(SystemExit):
        cli.cmd_ingest(_ingest_args(tmp_path))
    assert calls == []


def test_cli_coklu_dosyada_hicbiri_yuklenmeden_once_dogrular(tmp_path, monkeypatch):
    """Sıra rides→status; bozuk status başlığı ~1M satır yazıldıktan sonra fark edilmemeli."""
    from binbin.cli import main as cli

    calls = _patch_ingest(monkeypatch)
    _write(tmp_path, "a_rides.csv", RIDES_COLUMNS)
    bad = list(STATUS_COLUMNS)
    bad[5] = "beklenmeyen"
    _write(tmp_path, "b_status.csv", bad)
    with pytest.raises(SystemExit):
        cli.cmd_ingest(_ingest_args(tmp_path))
    assert calls == []


def test_cli_temiz_semada_her_iki_dosyayi_yukler(tmp_path, monkeypatch):
    from binbin.cli import main as cli

    calls = _patch_ingest(monkeypatch)
    _write(tmp_path, "a_rides.csv", RIDES_COLUMNS)
    _write(tmp_path, "b_status.csv", STATUS_COLUMNS)
    cli.cmd_ingest(_ingest_args(tmp_path))
    assert len(calls) == 2


@pytest.mark.parametrize(
    "sql_file,table,columns",
    [
        ("db/01_reset_ve_kurulum.sql", "stg_rental_raw", RIDES_COLUMNS),
        ("db/06_vehicle_status.sql", "stg_status_raw", STATUS_COLUMNS),
    ],
)
def test_sabit_liste_staging_semasiyla_ayni_sirada(sql_file, table, columns):
    """Liste DDL'den saparsa guard yanlış şemayı onaylar, koruma tersine döner."""
    sql = (ROOT / sql_file).read_text(encoding="utf-8")
    body = re.search(rf"CREATE UNLOGGED TABLE {table} \((.*?)\);", sql, re.S).group(1)
    assert re.findall(r"(\w+)\s+text", body) == list(columns)
