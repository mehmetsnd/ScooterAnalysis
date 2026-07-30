# Binbin — Başarısız Sürüş Analizi + Şüpheli Arıza Alarmı

Paylaşımlı e-skuter sürüşlerinde başarısızlık nedenlerinin analizi ve projenin ana
çıktısı olan **şüpheli (sahte) arıza alarmı** ölçümü. Katmanlı mimari, functional
core / imperative shell: `cli / reporting → core → data → domain`, veri kaynağı
Repository deseniyle pluggable (`data/`).

**Bu proje YALNIZCA CLI'dır.** Web katmanı (FastAPI/Plotly) fikri terk edildi ve
silindi; çıktı terminal + opsiyonel PNG grafiklerdir.

**Ana iş problemi:** Çalışan bir cihaz "arızalı" bildirilirse 3 boşa görev doğar
(sahadan toplama → atölye kontrolü → sahaya geri bırakma). Bu sırada gerçekten
arızalı araçlar sahada bekler. Amaç bunu ölçmek. Adlandırma disiplini: "SAHTE"
değil **"ŞÜPHELİ"** — veri kesin hüküm veremez.

## Kurulum notları

- Sanal ortam ve paketler elle kuruludur (`.venv`): sqlalchemy, psycopg, pandas,
  matplotlib, python-dotenv, pytest.
- DB şeması `db/01`→`db/07` sırasıyla PostgreSQL'de elle kurulur (aylık partition'lı
  `ride`/`fleet_status_event`, bileşik FK'ler, view'ler, kural kitabı seed'i).
- `.env.example` → `.env` kopyalayıp `DATABASE_URL`'i doldur.
- Ham CSV `data_raw/` klasörüne konur (`.gitignore`'da).

## Kapsam (scope)

İşlenen veri bu adımda **yalnızca Türkiye + İstanbul**'dur, ama bu koda gömülü
değildir — `config.py:DEFAULT_SCOPE` tek kaynaktır. Tüm komutlar aynı semantiği
paylaşır:

- bayrak yok → `DEFAULT_SCOPE` (Türkiye + İstanbul Avrupa/Anadolu)
- `--country AD` / `--city AD` (tekrarlanabilir) → verilen kapsam
- `--all` → filtre yok (tüm veri); `--country/--city` ile birlikte verilemez

## Çalıştırma (src-layout, kurulumsuz)

```powershell
# PowerShell — önce .venv aktive et, sonra:
$env:PYTHONPATH = "src"

# Testler (DB'siz, hepsi yeşil)
python -m pytest tests/ -q

# Uçtan uca akış (Postgres gerekir): ingest → classify → assess → analyze
.\run.ps1  # param yoksa interaktif sorar; Özel Kural varsayılanı 75 sn / 60 m

# Otomasyon veya tekrar üretilebilir bir çalışma için değerler doğrudan verilebilir:
.\run.ps1 -WiDuration 75 -WiDistance 60

# Yalnız analizi doğrudan çalıştırmak için (iki senaryo birlikte raporlanır):
#   Mevcut Kural = kaynak BASARISIZ_HARD veya 120 sn/60 m
#   Özel Kural   = kaynak etiketi yok sayılır; yalnız CLI'daki eşik uygulanır
python -m binbin.cli analyze --wi-duration 75 --wi-distance 60 \
  --false-fault --detay --derin --sinyal-denetimi --esik-taramasi --charts out
```

Ingest sonrası DB doğrulama: `country`=3, `city`≥2 (is_test hariç),
`SELECT count(*) FROM ride_default` = 0, `data_load.status='SUCCESS'`.

## Mesafe kaynağı ve veri-only reset

`ride.distance_m` alanının tek kanonik kaynağı CSV'deki
`mongo_distance_meters` kolonudur. `distance_meters` ve `distance` analiz
kararlarında kullanılmaz; mongo alanı boşsa değer `NULL` kalır.

Mongo mesafesiyle yeniden ingest öncesinde `db/01_reset_ve_kurulum.sql`
çalıştırılmamalıdır; o betik tüm `public` şemasını silip yeniden kurar. Tablo,
enum, indeks, partition ve referans/config kayıtlarını koruyarak yalnız operasyonel
veriyi temizlemek için sırasıyla:

```text
db/03_pre_data_reset_check.sql      # salt okunur mevcut durum/audit
db/04_reset_operational_data.sql    # ride, feedback, assessment, load, staging
db/05_post_data_reset_check.sql     # tablolar boş, şema/partition/config sağlam mı
```

Reset sonrasında tüm CSV'leri yeniden `ingest` et; ardından `classify --refresh`
ve `assess --refresh` çalıştır.

## Başarısızlık senaryoları

Analiz, özel eşikler verildiğinde aynı sürüş kümesini iki kuralla karşılaştırır:

- **Mevcut Kural:** kaynak `BASARISIZ_HARD` veya 120 sn/60 m eşiğine uyan sürüş.
- **Özel Kural:** kaynak outcome yok sayılır; yalnız CLI'daki özel eşik uygulanır.

CLI; her senaryonun başarısızlık oranını ve `Mevcut Kural → Özel Kural` geçişi için
başarısız→başarılı, başarılı→başarısız, net adet ve yüzde-puan farklarını gösterir.
Özel Kural'da süre veya mongo mesafesi eksik sürüşler başarılı sayılmaz;
`değerlendirilemedi` olarak ayrı raporlanır.

`--wi-duration`/`--wi-distance` **BİRLİKTE** verilmelidir; yalnız biri verilirse
net hata döner. `--esik-taramasi` bayrağı, Mevcut Kural (120/60) çevresinde dar bir
ızgarayı (90–150 sn × 40–80 m) F1 skoruyla tarayıp iki alternatif eşik senaryosu
önerir — ayrıntı için `CLAUDE.md`'deki "EŞİK TARAMASI" bölümüne bakın.

## Yapı

```
src/binbin/
├── config.py    # DEFAULT_SCOPE — kapsamın TEK kaynağı
├── domain/      # saf veri: enums.py, models.py (şemayla birebir)
├── data/        # katmanlı veri erişimi
│   ├── repository.py    # Protocol (arayüz kontratı — DIP)
│   ├── engine.py         # Engine + scope derleyici + sinyal-join SQL
│   ├── queries.py        # okuma sorguları (serbest fonksiyonlar)
│   ├── classify.py       # yazma: sınıflandırma
│   ├── assess.py         # yazma: sahte arıza değerlendirmesi
│   ├── postgres_repo.py  # ince Protocol impl (yukarıdakilere delege)
│   ├── ingest.py         # sürüş CSV → Postgres ETL
│   └── ingest_status.py  # araç durum-değişim CSV → Postgres ETL
├── core/        # SAF çekirdek (I/O yok): classifier, false_fault, keywords,
│                # scenario_analysis, threshold_scan, signal_audit
├── reporting/   # charts.py (matplotlib PNG), format.py (ortak biçimleyiciler)
└── cli/         # main.py — TEK giriş noktası (ingest/classify/assess/analyze/loads)

db/              # PostgreSQL şeması (elle çalıştırılır)
├── 01_reset_ve_kurulum.sql
├── 02_false_fault.sql
├── 03_pre_data_reset_check.sql
├── 04_reset_operational_data.sql
├── 05_post_data_reset_check.sql
├── 06_vehicle_status.sql
├── 07_signal_rulebook_revision.sql
└── 08_veri_kalite_bayragi_duzeltme.sql
```

Veri kaynağı soyutlaması `repository.py` Protocol'ü ile tanımlanır; tek somut
implementasyon `PostgresRideRepository`'dir. Testler DB'ye bağlanmadan inline
`_FakeRepo` duck-typing ile bu kontratı doğrular.

Daha ayrıntılı mimari/konvansiyon dokümantasyonu için `CLAUDE.md`'ye bakın.
