"""CSV seçim mantığı testleri — DB'siz, dosya sistemi (tmp_path) ile."""

from types import SimpleNamespace

import pytest

from binbin.cli.main import build_parser, cmd_ingest, select_csv
from binbin.data.ingest import list_source_csvs


def _touch(dir_, name):
    p = dir_ / name
    p.write_text("x", encoding="utf-8")
    return p


def test_list_source_csvs_bos_bir_cok(tmp_path):
    assert list_source_csvs(tmp_path) == []
    a = _touch(tmp_path, "a.csv")
    assert list_source_csvs(tmp_path) == [a]
    b = _touch(tmp_path, "b.csv")
    _touch(tmp_path, "not_a_csv.txt")
    assert list_source_csvs(tmp_path) == [a, b]  # ada göre sıralı


def test_list_source_csvs_klasor_yoksa_hata(tmp_path):
    with pytest.raises(FileNotFoundError):
        list_source_csvs(tmp_path / "yok")


def test_select_explicit_dosya(tmp_path):
    a = _touch(tmp_path, "a.csv")
    b = _touch(tmp_path, "b.csv")
    assert select_csv([a, b], explicit=b, prompt_fn=None) == b


def test_select_explicit_yoksa_hata(tmp_path):
    with pytest.raises(SystemExit):
        select_csv([], explicit=tmp_path / "yok.csv", prompt_fn=None)


def test_select_tek_dosya_otomatik(tmp_path):
    a = _touch(tmp_path, "a.csv")
    assert select_csv([a], explicit=None, prompt_fn=None) == a


def test_select_cok_dosya_prompt_ile(tmp_path):
    a = _touch(tmp_path, "a.csv")
    b = _touch(tmp_path, "b.csv")
    # "2" seçimi → ikinci dosya
    assert select_csv([a, b], explicit=None, prompt_fn=lambda _: "2") == b


def test_select_cok_dosya_prompt_yok_hata(tmp_path):
    a = _touch(tmp_path, "a.csv")
    b = _touch(tmp_path, "b.csv")
    with pytest.raises(SystemExit):
        select_csv([a, b], explicit=None, prompt_fn=None)


def test_select_hic_dosya_hata():
    with pytest.raises(SystemExit):
        select_csv([], explicit=None, prompt_fn=None)


def test_parser_ingest_file_force():
    args = build_parser().parse_args(["ingest", "--file", "x.csv", "--force"])
    assert args.command == "ingest"
    assert str(args.file) == "x.csv"
    assert args.force is True


def test_parser_assess_refresh():
    args = build_parser().parse_args(["assess", "--refresh"])
    assert args.command == "assess"
    assert args.refresh is True


def test_parser_loads():
    args = build_parser().parse_args(["loads"])
    assert args.command == "loads"


# --- cmd_ingest: data_dir'deki CSV'leri türüne göre otomatik yönlendirir -----
def _fake_rides_report(**over):
    defaults = dict(
        data_load_id=1, file_name="rides.csv", status="SUCCESS",
        rows_read=0, rows_eligible=0, rows_inserted=0, rows_skipped=0,
        rows_flagged=0, cities=0, sub_regions=0, end_reasons=0, warnings=[],
    )
    defaults.update(over)
    return SimpleNamespace(**defaults)


def _fake_status_report(**over):
    defaults = dict(
        data_load_id=2, file_name="status.csv", status="SUCCESS",
        rows_read=0, rows_inserted=0, rows_skipped=0, vehicles_created=0, warnings=[],
    )
    defaults.update(over)
    return SimpleNamespace(**defaults)


def test_cmd_ingest_iki_turu_de_basligindan_ayirip_yonlendirir(tmp_path, monkeypatch, capsys):
    rides_csv = tmp_path / "a_rides.csv"
    rides_csv.write_text("rental_id,user_id\n1,2\n", encoding="utf-8")
    status_csv = tmp_path / "b_status.csv"
    status_csv.write_text(
        "id,vehicle_id,status_id,status_reason_id,previous_status_id\n1,2,3,4,5\n",
        encoding="utf-8",
    )

    calls = []

    def fake_run_ingest(csv_path, scope, force=False):
        calls.append(("rides", csv_path))
        return _fake_rides_report()

    def fake_run_status_ingest(csv_path, scope, force=False):
        calls.append(("status", csv_path))
        return _fake_status_report()

    monkeypatch.setattr("binbin.data.ingest.run_ingest", fake_run_ingest)
    monkeypatch.setattr("binbin.data.ingest_status.run_status_ingest", fake_run_status_ingest)

    args = build_parser().parse_args(["ingest", "--data-dir", str(tmp_path)])
    cmd_ingest(args)

    assert ("rides", rides_csv) in calls
    assert ("status", status_csv) in calls
    output = capsys.readouterr().out
    assert "CSV [rides]" in output
    assert "CSV [status]" in output


def test_cmd_ingest_file_bayragi_turu_otomatik_algilar(tmp_path, monkeypatch):
    status_csv = tmp_path / "only_status.csv"
    status_csv.write_text(
        "id,vehicle_id,status_id,status_reason_id,previous_status_id\n1,2,3,4,5\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run_status_ingest(csv_path, scope, force=False):
        calls.append(csv_path)
        return _fake_status_report()

    monkeypatch.setattr("binbin.data.ingest_status.run_status_ingest", fake_run_status_ingest)

    args = build_parser().parse_args(["ingest", "--file", str(status_csv)])
    cmd_ingest(args)

    assert calls == [status_csv]


# --- classify --refresh: assess ile SİMETRİK sözleşme ------------------------
# Bu bayrak olmadan kalıcı ride.failure_category, classified_at damgası yüzünden
# kural kitabı/sinyal değişse bile ASLA tazelenmiyordu (canlı analyze ile tutmuyordu).
def test_classify_refresh_bayragi_var_ve_varsayilan_kapali():
    parser = build_parser()
    assert parser.parse_args(["classify"]).refresh is False
    assert parser.parse_args(["classify", "--refresh"]).refresh is True


def test_assess_refresh_bayragi_ayni_sozlesme():
    parser = build_parser()
    assert parser.parse_args(["assess"]).refresh is False
    assert parser.parse_args(["assess", "--refresh"]).refresh is True


def test_cmd_classify_refresh_repoya_gecirilir(monkeypatch):
    captured = {}

    class FakeRepo:
        def resolve_scope(self, scope):
            return None

        def classify_all(self, scope, batch_size=10000, refresh=False):
            captured["refresh"] = refresh
            return {"processed": 3, "classified": 1}

    monkeypatch.setattr(
        "binbin.data.postgres_repo.PostgresRideRepository", lambda *a, **k: FakeRepo()
    )
    from binbin.cli.main import cmd_classify

    cmd_classify(build_parser().parse_args(["classify", "--refresh"]))
    assert captured["refresh"] is True

    cmd_classify(build_parser().parse_args(["classify"]))
    assert captured["refresh"] is False


def test_analyze_sinyal_denetimi_bayragi():
    parser = build_parser()
    assert parser.parse_args(["analyze"]).sinyal_denetimi is False
    assert parser.parse_args(["analyze", "--sinyal-denetimi"]).sinyal_denetimi is True
