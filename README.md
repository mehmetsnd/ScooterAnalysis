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
python -m binbin.cli ingest                       # CSV → Postgres (~406.814 satır)
python -m binbin.cli classify                     # başarısızları sınıflandır
python -m binbin.cli assess                       # sahte arıza değerlendirmesi
python -m binbin.cli analyze --false-fault --detay --derin --charts out\

# What-if eşik karşılaştırması (gerçek 120sn/60m vs kendi kuralın):
python -m binbin.cli analyze --wi-duration 100 --wi-distance 45 --charts out\

# API (http://127.0.0.1:8000/health) — analiz endpoint'leri sonraki PART
python -m uvicorn binbin.api.app:app --reload
```

Ingest sonrası DB doğrulama: `country`=3, `city`≥2 (is_test hariç),
`SELECT count(*) FROM ride_default` = 0, `data_load.status='SUCCESS'`.

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
└── 02_false_fault.sql
```

Veri kaynağı soyutlaması `repository.py` Protocol'ü ile tanımlanır; tek somut
implementasyon `PostgresRideRepository`'dir. Testler DB'ye bağlanmadan inline
`_FakeRepo` duck-typing ile bu kontratı doğrular.
