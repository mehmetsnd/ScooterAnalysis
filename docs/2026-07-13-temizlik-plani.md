# Temizlik Planı — Ölü Paralel Yığın + Docstring + DRY

Tarih: 2026-07-13
Amaç: Kod incelemesinde tespit edilen sorunları **çalışan kodu bozmadan** gidermek.
Kapsam yalnız temizlik/refactor; yeni özellik veya davranış değişikliği YOK.

## Bağlam (Neden)

Codex refactor'ı `analyze` komutunu tamamen yeni `scenario_analysis` yoluna taşıdı,
ama eski analiz yığını (analysis.py + eski query/printer/chart'lar + Protocol metotları)
silinmedi. Bu ~670 satır ölü kod yalnızca testler tarafından ayakta tutuluyor;
production (canlı `analyze`) hiçbirini çağırmıyor (grep ile kanıtlandı). Sonuç: her
şeyin iki kopyası var, "hangi dosyada hangi fonksiyon" netliği bozuldu, testler ölü
kodu doğrulayıp yanlış güven veriyor.

## Güvence Stratejisi ("çalışan kodu bozma")

1. **Golden çıktı ağı:** İşe başlamadan önce canlı `analyze` çıktısı iki modda dosyaya
   alınır (referans):
   - `analyze --false-fault --detay --derin` (senaryo tek: Mevcut Kural)
   - `analyze --false-fault --detay --derin --wi-duration 100 --wi-distance 45`
   Her increment sonrası aynı komutlar çalıştırılıp `diff` ile **birebir aynı** olduğu
   doğrulanır. Fark çıkarsa o adım geri alınır.
2. **Test kapısı:** Her increment sonrası `pytest tests/ -q` yeşil olmalı.
3. **Silme, satır-aralığı ile değil fonksiyon-fonksiyon** yapılır; çünkü bazı yardımcılar
   (ör. `_fmt_thr`) hem ölü hem canlı fonksiyonlarca kullanılıyor (aşağıda işaretli).
4. Her increment ayrı commit → gerekirse tek adım geri alınır.

## KESİNLİKLE KORUNACAKLAR (aşırı-silmeye karşı)

- Data plumbing: `_scope_clause`, `_as_dicts`, `_CITY_ALIAS`, `get_engine`, `_database_url`
  ([engine.py](../src/binbin/data/engine.py)) — `analysis_timeline`, ops sorguları ve
  `test_backend_hardening.py` kullanıyor.
- Canlı query'ler: `resolve_scope`, `analysis_timeline`, `ops_cost_rows`, `list_data_loads`
  ([queries.py](../src/binbin/data/queries.py)).
- Yazma yolu: `classify.py`, `assess.py`, `classify_all`, `assess_all`.
- `main._fmt_thr` — ölü printer'lar dışında **canlı** `_rule_text` de kullanıyor → KALIR.
- Tüm `scenario_analysis.py`, tüm `_print_scenario_*`, tüm `chart_scenario_*`.
- `core/classifier.py`, `core/false_fault.py`, `core/keywords.py` (senaryo motoru bunları
  yeniden kullanıyor).

---

## Increment 0 — Golden referans al (kod değişmez)

`analyze`'ın iki modunu `out/_golden_before_*.txt`'e yaz. Bu dosyalar geçici, commit
edilmez (`.gitignore`/`out/` zaten hariç). Sonraki her adımın kıyas tabanı.

---

## Increment 1 — Ölü paralel yığını sil (sıfır davranış riski)

Bağımlılık sırasıyla, her alt-adımdan sonra pytest + golden diff:

1. **Eski chart'lar** ([charts.py:111-280](../src/binbin/reporting/charts.py)):
   `chart_cause_distribution`, `chart_control_group`, `chart_criteria_whatif`,
   `chart_vehicle_hotspots`, `chart_subregion_false_fault`, `chart_hourly_failure_rate`
   + yalnız bunlara hizmet eden `_thr_txt` → sil.
   `test_charts.py` tamamen bu eski chart'ları test ediyor (senaryo chart'ları
   `test_scenario_charts.py`'de) → **dosyayı sil**.
2. **Eski printer'lar** ([main.py:257-341](../src/binbin/cli/main.py)):
   `_print_cause`, `_print_criteria`, `_print_criteria_whatif`, `_print_control`,
   `_print_false_fault`, `_print_vehicle_hotspots`, `_print_subregion`, `_print_hourly`
   → sil. **DİKKAT:** `_fmt_thr` KALIR (canlı `_rule_text` kullanıyor).
3. **`core/analysis.py`** (tümü) → sil. `test_analysis.py` yalnız bunu test ediyor
   (canlı motor `test_scenario_analysis.py` ile kapsanıyor) → **dosyayı sil**.
4. **Eski query'ler** ([queries.py:107-238](../src/binbin/data/queries.py)):
   `failure_category_counts`, `failure_criteria_counts`, `vehicle_failure_counts`,
   `control_group_stats`, `false_fault_counts`, `subregion_stats`, `hour_region_counts`
   → sil. Silme sonrası `run_scoped` öksüz kalırsa (grep ile doğrula) onu ve artık
   gereksiz importları da temizle.
5. **Protocol + delege** temizliği:
   - [repository.py](../src/binbin/data/repository.py) `RideQueryRepository`'den yukarıdaki
     7 metodun bildirimini sil; `resolve_scope`, `analysis_timeline`, `ops_cost_rows` kalır.
   - [postgres_repo.py](../src/binbin/data/postgres_repo.py) aynı 7 delege metodunu sil.
6. Silme sonrası artık kullanılmayan importları (ör. `text`, `Iterable`) temizle;
   `python -c "import ..."` ile tüm modüllerin hâlâ import edildiğini doğrula.

Beklenen sonuç: ~670 satır azalma, golden çıktı **birebir aynı**, pytest yeşil
(test sayısı azalır çünkü ölü-kod testleri kalkar — bu beklenen ve doğru).

---

## Increment 2 — Bayat docstring'leri gerçeğe uydur (doc-only)

- [repository.py:6-7](../src/binbin/data/repository.py): "ağır agregasyon DB'de" ifadesini,
  canlı yolun `analysis_timeline` ile ham satır çekip Python'da (core fonksiyonlarını
  yeniden kullanarak) topladığı gerçeğiyle değiştir.
- [main.py:6-16](../src/binbin/cli/main.py): `analyze` kullanım örneğine `--wi-duration/
  --wi-distance` ve iki-senaryo davranışını ekle.
- [charts.py:4](../src/binbin/reporting/charts.py): "core/analysis.py'dan dict alır" →
  "senaryo raporu (scenario_analysis) dict'i alır".
- [cmd_analyze](../src/binbin/cli/main.py) üstüne kısa yorum: `analyze` her zaman tam
  timeline tarar (classify/assess mantığını SQL'de tekrarlamamak için bilinçli takas);
  mevcut aylık ölçekte uygun, çok büyük ölçekte yeniden değerlendirilmeli.

---

## Increment 3 — DRY: kopyalanmış yardımcıları tek modüle topla (davranış aynı)

Yeni `src/binbin/reporting/format.py` (veya mevcut bir uygun yer):
- Türkçe sayı biçimleyiciler: `tr_int`, `tr_pct`, `tr_dec`, `signed_int`
  (şu an [main.py](../src/binbin/cli/main.py) ve [charts.py](../src/binbin/reporting/charts.py)'de
  kopya).
- Eşik biçimleyici: `fmt_threshold` (main `_fmt_thr` + charts iç `n()` birleşimi).
- `GROUP_LABELS` (main + charts kopya) tek kaynağa.

Fonksiyon gövdeleri **birebir** taşınır; çağrı yerleri yeni modülü import eder. Golden
diff ile çıktının değişmediği kanıtlanır. (Not: `_pct` main tarafında yok; `scenario_analysis._pct`
core'da kalır, formatlama modülüne karıştırılmaz — core→reporting bağımlılığı doğmasın.)

---

## Increment 4 — Magic number'ları adlandırılmış sabitlere al (davranış aynı)

[scenario_analysis.py](../src/binbin/core/scenario_analysis.py) modül başına:
- `MIN_SUBREGION_RIDES = 2000` (şu an 291 ve 352'de gömülü — ikisini de bununla değiştir)
- `MIN_VEHICLE_FAILURES = 10`
Sunum eşikleri (printer `[:100]`, chart `[:15]`) ilgili dosyalarda adlandırılmış sabit.
Değerler aynı → çıktı aynı.

---

## Increment 5 — Terminoloji: yalnız metin hizala (bayrak adı DEĞİŞMEZ)

Karar: `--wi-duration/--wi-distance` bayrak adları **korunur** (run.ps1, settings.local.json
kırılmaz). Yalnız:
- Bayrakların `help=` metinleri "Özel Kural (senaryo) eşiği" diline çekilir (zaten büyük
  ölçüde öyle; "what-if" kalıntıları temizlenir).
- İç değişken/docstring'lerdeki "what-if" sözcüğü, rapor diline ("senaryo/özel kural")
  hizalanır. Fonksiyon adları (ör. `_whatif_from_args`) davranışsız olduğu için opsiyonel;
  istenirse `_custom_rule_from_args` olarak yeniden adlandırılır (yalnız iç ad).

---

## Doğrulama (her increment + final)

1. `PYTHONPATH=src pytest tests/ -q` → yeşil.
2. Import bütünlüğü: tüm `binbin.*` modülleri hatasız import.
3. Golden diff: her iki `analyze` modu Increment 0 çıktısıyla **birebir aynı**.
4. Final: `.\run.ps1` uçtan uca çalışır, grafikler `out/`'a üretilir.

## Sıralama ve commit

0→1→2→3→4→5, her biri ayrı commit. En riskli tek adım Increment 1 (silme); golden diff +
pytest ikili kapısı onu güvenceye alır. 2-5 zaten davranışsızdır.
