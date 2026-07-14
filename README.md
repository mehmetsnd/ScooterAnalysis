# Binbin — Başarısız Sürüş Analizi + Sahte Arıza Alarmı

Paylaşımlı e-skuter sürüşlerinde başarısızlık nedenlerinin analizi ve projenin ana
çıktısı olan **sahte arıza alarmı** ölçümü. Katmanlı mimari, functional core /
imperative shell: `cli / api / reporting → core → domain`, veri kaynağı Repository
deseniyle pluggable (`data/`).

**Ana iş problemi:** Çalışan bir cihaz "arızalı" bildirilirse 3 boşa görev doğar
(sahadan toplama → atölye kontrolü → sahaya geri bırakma). Bu sırada gerçekten
arızalı araçlar sahada bekler. Amaç bunu ölçmek. Adlandırma disiplini: "SAHTE"
değil **"ŞÜPHELİ"** — veri kesin hüküm veremez.

## Kurulum notları

- Sanal ortam ve paketler elle kuruludur (`.venv`): fastapi, uvicorn, sqlalchemy,
  psycopg, alembic, pandas, matplotlib, plotly, python-dotenv, pytest.
- DB şeması `db/01_reset_ve_kurulum.sql` + `db/02_false_fault.sql` ile PostgreSQL'de
  zaten oluşturulmuştur (aylık partition'lı `ride`, bileşik FK'ler, view'ler).
- `.env.example` → `.env` kopyalayıp `DATABASE_URL`'i doldur.
- Ham CSV `data_raw/` klasörüne konur (`.gitignore`'da; tek `.csv` olmalı).

## Kapsam (scope)

İşlenen veri bu adımda **yalnızca Türkiye + İstanbul**'dur, ama bu koda gömülü
değildir — `config.py:DEFAULT_SCOPE` tek kaynaktır. Dört komut da aynı semantiği
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

# Uçtan uca akış (Postgres gerekir):
.\run.ps1  # Özel Kural: süre 60-200 saniye, mesafe 20-150 metre

# Boş girişte varsayılan 75 saniye / 60 metre kullanılır (doğruluk-optimal Özel Kural:
# kaynak etiketiyle en yüksek uyum; başarı oranını gerçek başarısızları gizlemeden artırır).
# Otomasyon veya tekrar üretilebilir bir çalışma için değerler doğrudan verilebilir:
.\run.ps1 -WiDuration 75 -WiDistance 60

# Yalnız analizi doğrudan çalıştırmak için:
# Özel eşik karşılaştırması — iki okunur senaryo birlikte raporlanır:
#   Mevcut Kural = BASARISIZ_HARD veya 120sn/60m
#   Özel Kural   = kaynak etiketi yok; yalnız 75sn/60m (varsayılan, doğruluk-optimal)
python -m binbin.cli analyze --wi-duration 75 --wi-distance 60 \
  --false-fault --detay --derin --charts out\

# API (http://127.0.0.1:8000/health) — analiz endpoint'leri sonraki PART
python -m uvicorn binbin.api.app:app --reload
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

Reset sonrasında tüm CSV'leri yeniden `ingest` et; ardından `classify` ve
`assess --refresh` çalıştır.

## Başarısızlık senaryoları

Analiz, özel eşikler verildiğinde aynı sürüş kümesini iki kuralla karşılaştırır:

- **Mevcut Kural:** kaynak `BASARISIZ_HARD` veya 120sn/60m eşiğine uyan kaynak başarılı.
- **Özel Kural:** kaynak outcome yok sayılır; yalnız CLI'daki özel eşik uygulanır.

CLI; her senaryonun başarısızlık oranını ve `Mevcut Kural → Özel Kural` geçişi için
başarısız→başarılı, başarılı→başarısız, net adet ve yüzde-puan farklarını gösterir.
Özel Kural'da süre veya mongo mesafesi eksik sürüşler başarılı sayılmaz;
`değerlendirilemedi` olarak ayrı raporlanır.

## Yapı

```
src/binbin/
├── config.py    # DEFAULT_SCOPE — kapsamın TEK kaynağı
├── domain/      # saf veri: enums.py, models.py (şemayla birebir)
├── data/        # katmanlı veri erişimi (aşağıda ayrıntı)
│   ├── repository.py  # Protocol (arayüz kontratı — DIP)
│   ├── schema.py      # SQLAlchemy Table() tanımları (şema envanteri)
│   ├── engine.py      # Engine + scope derleyici + run_scoped yürütücü
│   ├── queries.py     # okuma sorguları (serbest fonksiyonlar)
│   ├── classify.py    # yazma: sınıflandırma
│   ├── assess.py      # yazma: sahte arıza değerlendirmesi
│   ├── postgres_repo.py # ince Protocol impl (yukarıdakilere delege)
│   └── ingest.py      # CSV → Postgres ETL
├── core/        # SAF çekirdek: keywords, classifier, false_fault, analysis (I/O yok)
├── reporting/   # charts.py (matplotlib PNG), report.py (Plotly HTML — sonraki PART)
├── api/         # app.py (FastAPI)
└── cli/         # main.py (ingest/classify/assess/analyze/loads + kapsam)

db/              # PostgreSQL şeması (elle çalıştırılır)
├── 01_reset_ve_kurulum.sql
├── 02_false_fault.sql
├── 03_pre_data_reset_check.sql
├── 04_reset_operational_data.sql
└── 05_post_data_reset_check.sql
```

Veri kaynağı soyutlaması `repository.py` Protocol'ü ile tanımlanır; tek somut
implementasyon `PostgresRideRepository`'dir. Testler DB'ye bağlanmadan inline
`_FakeRepo` duck-typing ile bu kontratı doğrular.
