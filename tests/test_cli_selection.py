"""CSV seçim mantığı testleri — DB'siz, dosya sistemi (tmp_path) ile."""

import pytest

from binbin.cli.main import build_parser, select_csv
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
